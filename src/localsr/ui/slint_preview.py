"""Bounded Pillow preview compositor for the Slint frontend."""

from __future__ import annotations

import base64
import io
import tempfile
from pathlib import Path

from PIL import Image, ImageOps


class SlintPreviewBuffer:
    def __init__(self, maximum_dimension: int = 1600):
        self.maximum_dimension = maximum_dimension
        self._temporary = tempfile.TemporaryDirectory(prefix="localsr-preview-")
        self._root = Path(self._temporary.name)
        self._source: Image.Image | None = None
        self._progressive: Image.Image | None = None
        self._output_width = 0
        self._output_height = 0
        self._revision = 0
        self._media_thumbnails: dict[str, Path] = {}

    def _bounded(self, image: Image.Image) -> Image.Image:
        result = image.convert("RGB")
        result.thumbnail(
            (self.maximum_dimension, self.maximum_dimension),
            Image.Resampling.LANCZOS,
        )
        return result

    def _write(self, image: Image.Image, stem: str, quality: int = 90) -> Path:
        self._revision += 1
        destination = self._root / f"{stem}-{self._revision % 2}.jpg"
        image.save(destination, format="JPEG", quality=quality, optimize=False)
        return destination

    @staticmethod
    def _decode(jpeg_base64: str) -> Image.Image | None:
        try:
            payload = base64.b64decode(jpeg_base64, validate=True)
            with Image.open(io.BytesIO(payload)) as image:
                return image.convert("RGB")
        except (OSError, TypeError, ValueError):
            return None

    def set_source_base64(self, jpeg_base64: str) -> Path | None:
        decoded = self._decode(jpeg_base64)
        if decoded is None:
            return None
        self._source = self._bounded(decoded)
        self._progressive = None
        self._output_width = 0
        self._output_height = 0
        return self._write(self._source, "source", quality=92)

    def media_thumbnail(self, source_path: str, size: int = 96) -> Path | None:
        """Create a tiny square queue thumbnail without retaining source pixels.

        Pillow does not decode every camera RAW variant; those inputs simply
        keep the neutral file placeholder until the worker provides a preview.
        """

        cached = self._media_thumbnails.get(source_path)
        if cached is not None and cached.is_file():
            return cached
        from localsr.core.image_formats import is_video_input

        if is_video_input(source_path):
            # First decoded frame stands in for the clip.
            try:
                from localsr.core.video_io import decode_frames

                _, rgb = next(iter(decode_frames(source_path, 0, 0)))
                frame = Image.fromarray(rgb)
            except (StopIteration, OSError, ValueError, ImportError):
                return None
            thumbnail = ImageOps.fit(
                frame.convert("RGB"), (size, size), method=Image.Resampling.LANCZOS
            )
            destination = self._root / f"media-{len(self._media_thumbnails)}.jpg"
            thumbnail.save(destination, format="JPEG", quality=82, optimize=False)
            self._media_thumbnails[source_path] = destination
            return destination
        try:
            with Image.open(source_path) as source:
                oriented = ImageOps.exif_transpose(source)
                thumbnail = ImageOps.fit(
                    oriented.convert("RGB"),
                    (size, size),
                    method=Image.Resampling.LANCZOS,
                )
        except (OSError, TypeError, ValueError):
            return None
        destination = self._root / f"media-{len(self._media_thumbnails)}.jpg"
        thumbnail.save(destination, format="JPEG", quality=82, optimize=False)
        self._media_thumbnails[source_path] = destination
        return destination

    def reset_progressive(self, output_width: int, output_height: int) -> Path:
        self._output_width = max(1, int(output_width))
        self._output_height = max(1, int(output_height))
        scale = min(1.0, self.maximum_dimension / max(self._output_width, self._output_height))
        size = (
            max(1, round(self._output_width * scale)),
            max(1, round(self._output_height * scale)),
        )
        if self._source is None:
            progressive = Image.new("RGB", size, "#10151c")
        else:
            # Begin with the source at normal brightness. Completed output tiles
            # replace their matching source regions, producing a stable live
            # preview instead of a distracting dark-to-sharp "unblur" effect.
            progressive = self._source.resize(size, Image.Resampling.LANCZOS)
        self._progressive = progressive
        return self._write(progressive, "progressive", quality=86)

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
    ) -> Path | None:
        if (
            self._progressive is None
            or image_width != self._output_width
            or image_height != self._output_height
        ):
            return None
        tile = self._decode(jpeg_base64)
        if tile is None:
            return None
        preview_width, preview_height = self._progressive.size
        left = round(output_x / image_width * preview_width)
        top = round(output_y / image_height * preview_height)
        right = round((output_x + output_width) / image_width * preview_width)
        bottom = round((output_y + output_height) / image_height * preview_height)
        target_size = (max(1, right - left), max(1, bottom - top))
        self._progressive.paste(tile.resize(target_size, Image.Resampling.LANCZOS), (left, top))
        return self._write(self._progressive, "progressive", quality=86)

    def mark_active_tile(
        self,
        output_x: int,
        output_y: int,
        output_width: int,
        output_height: int,
        image_width: int,
        image_height: int,
    ) -> tuple[float, float, float, float]:
        if image_width <= 0 or image_height <= 0:
            return 0.0, 0.0, 0.0, 0.0
        return (
            output_x / image_width,
            output_y / image_height,
            output_width / image_width,
            output_height / image_height,
        )

    def close(self) -> None:
        self._source = None
        self._progressive = None
        self._media_thumbnails.clear()
        self._temporary.cleanup()
