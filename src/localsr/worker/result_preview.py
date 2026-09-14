"""Bounded result previews that remain responsive during a long inference."""

import base64
import io
import threading
import warnings

from PIL import Image, ImageCms, ImageOps

from localsr.protocol.messages import PreviewFailed, PreviewReady


def render_result_preview(data):
    path = str(data.get("image_path", ""))
    try:
        maximum = max(64, min(2048, int(data.get("max_dimension", 1600))))
        with Image.open(path) as opened:
            width, height = opened.size
            if opened.getexif().get(274, 1) in (5, 6, 7, 8):
                width, height = height, width
            # JPEG can downsample during decode. Other formats keep a single
            # byte image, without allocating a full-resolution float tensor or
            # touching the inference device and its allocator.
            opened.draft("RGB", (maximum, maximum))
            opened.thumbnail((maximum, maximum), Image.Resampling.LANCZOS)
            image = ImageOps.exif_transpose(opened)
            profile = image.info.get("icc_profile")
            if profile:
                try:
                    image = ImageCms.profileToProfile(
                        image,
                        ImageCms.ImageCmsProfile(io.BytesIO(profile)),
                        ImageCms.createProfile("sRGB"),
                        outputMode="RGB",
                    )
                except (ImageCms.PyCMSError, OSError, ValueError) as error:
                    warnings.warn(
                        f"Invalid preview ICC profile: {error}", RuntimeWarning, stacklevel=2
                    )
            image = image.convert("RGB")
            encoded = io.BytesIO()
            image.save(encoded, format="JPEG", quality=82, optimize=True)
            return PreviewReady(
                image_path=path,
                width=width,
                height=height,
                jpeg_base64=base64.b64encode(encoded.getvalue()).decode("ascii"),
            )
    except Exception as error:  # noqa: BLE001 - a bad preview must not stop the worker
        return PreviewFailed(image_path=path, error_message=str(error))


class ResultPreviewService:
    """One decoder, at most one pending selection and one automatic preview."""

    def __init__(self, emit):
        self.emit = emit
        self.condition = threading.Condition()
        self.pending = {}
        self.closed = False
        self.thread = None

    def submit(self, data):
        if not isinstance(data, dict):
            self.emit(
                PreviewFailed(image_path="", error_message="Preview request must be an object.")
            )
            return
        with self.condition:
            if self.closed:
                return
            # Completion previews must not displace a user-selected result.
            self.pending[bool(data.get("comparison", False))] = dict(data)
            if self.thread is None:
                self.thread = threading.Thread(target=self._run, name="result-preview", daemon=True)
                self.thread.start()
            self.condition.notify()

    def close(self):
        with self.condition:
            self.closed = True
            self.pending.clear()
            self.condition.notify_all()
        if self.thread is not None:
            self.thread.join(timeout=2)

    def _run(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.closed or bool(self.pending))
                if self.closed:
                    return
                data = self.pending.pop(True if True in self.pending else False)
            message = render_result_preview(data)
            with self.condition:
                if self.closed:
                    return
            self.emit(message)
