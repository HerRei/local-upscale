"""Bounded result previews that remain responsive during a long inference."""

import base64
import io
import json
import os
import subprocess
import sys
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
    except Image.DecompressionBombError:
        # A normal camera photo enlarged 4× can exceed Pillow's input limit.
        # These are completed, host-authorized exports. Decode them separately
        # so the input decoder's limit stays intact, including during inference.
        return _render_large_result(data)
    except Exception as error:  # noqa: BLE001 - a bad preview must not stop the worker
        return PreviewFailed(image_path=path, error_message=str(error))


def _render_large_result(data):
    command = [sys.executable]
    if not getattr(sys, "frozen", False):
        command += ["-m", "localsr.worker"]
    command += ["--large-result-preview"]
    options = {}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        result = subprocess.run(
            command,
            input=json.dumps(data),
            capture_output=True,
            text=True,
            timeout=180,
            **options,
        )
        if result.returncode:
            raise OSError(
                "The image preview could not be decoded. Your saved image is still available."
            )
        message = json.loads(result.stdout)
        if message["type"] == "preview_ready":
            return PreviewReady(**message["data"])
        return PreviewFailed(**message["data"])
    except subprocess.TimeoutExpired:
        return PreviewFailed(
            image_path=str(data.get("image_path", "")),
            error_message="The preview is taking too long. Your saved image is still available.",
        )
    except (OSError, ValueError, KeyError, TypeError) as error:
        return PreviewFailed(image_path=str(data.get("image_path", "")), error_message=str(error))


def large_result_main():
    """Codec-only child for completed exports, never for source image imports."""
    # This setting belongs only to this short-lived process. The worker retains
    # Pillow's default checks and receives only a JPEG of at most 2048×2048.
    Image.MAX_IMAGE_PIXELS = None
    message = render_result_preview(json.load(sys.stdin))
    print(message.to_json(), flush=True)


class ResultPreviewService:
    """One decoder, at most one pending selection and one automatic preview."""

    def __init__(self, emit):
        self.emit = emit
        self.condition = threading.Condition()
        self.pending = {}
        self.closed = False
        self.thread = None
        self.cached_key = None
        self.cached_preview = None

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
            # The host may request a completion and selection preview together.
            # Keep just the latest result, and invalidate it if the file changes.
            try:
                stat = os.stat(str(data.get("image_path", "")))
                key = (
                    data.get("image_path"),
                    data.get("max_dimension", 1600),
                    stat.st_mtime_ns,
                    stat.st_ctime_ns,
                    stat.st_size,
                )
            except OSError:
                key = None
            if key is not None and key == self.cached_key:
                message = self.cached_preview
            else:
                message = render_result_preview(data)
                if isinstance(message, PreviewReady):
                    self.cached_key, self.cached_preview = key, message
            with self.condition:
                if self.closed:
                    return
            self.emit(message)
