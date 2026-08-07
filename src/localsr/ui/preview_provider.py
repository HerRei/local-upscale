import base64
import threading
from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QImage, QImageReader, QPainter
from PySide6.QtQuick import QQuickImageProvider


class PreviewImageProvider(QQuickImageProvider):
    """Thread-safe, bounded image storage for source and progressive previews."""

    def __init__(self, maximum_dimension: int = 1600):
        super().__init__(QQuickImageProvider.Image)
        self.maximum_dimension = maximum_dimension
        self._lock = threading.Lock()
        self._source = QImage()
        self._progressive = QImage()
        self._output_width = 0
        self._output_height = 0

    def requestImage(self, image_id, size, requested_size):  # noqa: N802
        key = image_id.partition("?")[0]
        with self._lock:
            image = self._progressive if key == "progressive" else self._source
            result = image.copy()
        if size is not None:
            size.setWidth(result.width())
            size.setHeight(result.height())
        if requested_size.isValid() and not result.isNull():
            result = result.scaled(
                requested_size,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        return result

    def _bounded(self, image: QImage) -> QImage:
        if image.isNull():
            return image
        if max(image.width(), image.height()) <= self.maximum_dimension:
            return image.convertToFormat(QImage.Format_RGB32)
        return image.scaled(
            self.maximum_dimension,
            self.maximum_dimension,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        ).convertToFormat(QImage.Format_RGB32)

    def set_source_path(self, path: str | Path) -> bool:
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        original_size = reader.size()
        if original_size.isValid() and max(original_size.width(), original_size.height()) > 0:
            scale = min(
                1.0,
                self.maximum_dimension / max(original_size.width(), original_size.height()),
            )
            reader.setScaledSize(
                QSize(
                    max(1, round(original_size.width() * scale)),
                    max(1, round(original_size.height() * scale)),
                )
            )
        image = reader.read()
        if image.isNull():
            return False
        self.set_source_image(image)
        return True

    def set_source_base64(self, jpeg_base64: str) -> bool:
        try:
            data = base64.b64decode(jpeg_base64, validate=True)
        except (ValueError, TypeError):
            return False
        image = QImage.fromData(data)
        if image.isNull():
            return False
        self.set_source_image(image)
        return True

    def set_source_image(self, image: QImage):
        bounded = self._bounded(image)
        with self._lock:
            self._source = bounded
            self._progressive = QImage()
            self._output_width = 0
            self._output_height = 0

    def clear(self):
        with self._lock:
            self._source = QImage()
            self._progressive = QImage()
            self._output_width = 0
            self._output_height = 0

    def reset_progressive(self, output_width: int, output_height: int):
        output_width = max(1, int(output_width))
        output_height = max(1, int(output_height))
        scale = min(1.0, self.maximum_dimension / max(output_width, output_height))
        preview_size = QSize(
            max(1, round(output_width * scale)),
            max(1, round(output_height * scale)),
        )
        with self._lock:
            if self._source.isNull():
                image = QImage(preview_size, QImage.Format_RGB32)
                image.fill(QColor("#111722"))
            else:
                image = self._source.scaled(
                    preview_size,
                    Qt.IgnoreAspectRatio,
                    Qt.SmoothTransformation,
                )
                painter = QPainter(image)
                painter.fillRect(image.rect(), QColor(5, 9, 15, 178))
                painter.end()
            self._progressive = image
            self._output_width = output_width
            self._output_height = output_height

    def apply_tile(
        self,
        *,
        jpeg_base64: str,
        output_x: int,
        output_y: int,
        output_width: int,
        output_height: int,
        image_width: int,
        image_height: int,
    ) -> bool:
        if not jpeg_base64 or image_width <= 0 or image_height <= 0:
            return False
        try:
            tile = QImage.fromData(base64.b64decode(jpeg_base64, validate=True))
        except (ValueError, TypeError):
            return False
        if tile.isNull():
            return False
        with self._lock:
            if (
                self._progressive.isNull()
                or self._output_width != image_width
                or self._output_height != image_height
            ):
                return False
            target = QRect(
                round(output_x / image_width * self._progressive.width()),
                round(output_y / image_height * self._progressive.height()),
                max(1, round(output_width / image_width * self._progressive.width())),
                max(1, round(output_height / image_height * self._progressive.height())),
            )
            painter = QPainter(self._progressive)
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
            painter.drawImage(target, tile)
            painter.end()
        return True
