"""Bounded result previews that remain responsive during a long inference."""

import base64
import io
import json
import os
import subprocess
import sys
import threading
import warnings
from collections import OrderedDict
from dataclasses import replace

from PIL import Image, ImageCms, ImageOps

from localsr.core.image_formats import is_system_decoded
from localsr.protocol.messages import PreviewFailed, PreviewReady


def open_result_image(path):
    """Open an export: macOS decodes HEIC; the codec-only child avoids loading the engine."""
    if is_system_decoded(path):
        from localsr.core.image_io import open_image

        return open_image(path)
    return Image.open(path)


def render_result_preview(data):
    path = str(data.get("image_path", ""))
    try:
        maximum = max(64, min(2048, int(data.get("max_dimension", 1600))))
        with open_result_image(path) as opened:
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
                preview_width=image.width,
                preview_height=image.height,
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


# Pending requests are kept per purpose: the two sides of the selected pair,
# a selection from a host without pair slots, and the automatic preview of a
# job that just finished. Earlier keys are decoded first.
_PAIR_SLOTS = ("showing", "against")
_COMPARE_SLOTS = ("slot1", "slot2", "slot3", "slot4")
_PENDING_ORDER = (*_COMPARE_SLOTS, *_PAIR_SLOTS, "selected", "automatic")
# The selected pair plus the automatic preview, which is often one of them.
_CACHE_SIZE = 5
# Encoded JPEG bytes retained by the worker, independently of image dimensions.
_CACHE_BYTES = 16 * 1024 * 1024


def _generation(data):
    try:
        return max(0, int(data.get("generation", 0)))
    except (TypeError, ValueError):
        return 0


class ResultPreviewService:
    """One decoder, the latest desired pair and an independent completion slot."""

    def __init__(self, emit):
        self.emit = emit
        self.condition = threading.Condition()
        self.pending = {}
        self.closed = False
        self.thread = None
        self.cache = OrderedDict()
        self.generations = {"pair": 0, "compare": 0}

    def submit(self, data):
        if not isinstance(data, dict):
            self.emit(
                PreviewFailed(image_path="", error_message="Preview request must be an object.")
            )
            return
        with self.condition:
            if self.closed:
                return
            if not data.get("comparison", False):
                key = "automatic"
            elif data.get("slot") in (*_PAIR_SLOTS, *_COMPARE_SLOTS):
                key = data["slot"]
                group = "pair" if key in _PAIR_SLOTS else "compare"
                slots = _PAIR_SLOTS if group == "pair" else _COMPARE_SLOTS
                generation = _generation(data)
                # A newer pair replaces both sides of an older one, including
                # a side the newer pair does not need decoded again.
                if generation < self.generations[group]:
                    return
                if generation > self.generations[group]:
                    self.generations[group] = generation
                    for slot in slots:
                        self.pending.pop(slot, None)
            else:
                key = "selected"
            self.pending[key] = dict(data)
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
                key = next(key for key in _PENDING_ORDER if key in self.pending)
                data = self.pending.pop(key)
            # Invalidate a cached preview when its file changes.
            try:
                stat = os.stat(str(data.get("image_path", "")))
                cache_key = (
                    data.get("image_path"),
                    data.get("max_dimension", 1600),
                    stat.st_mtime_ns,
                    stat.st_ctime_ns,
                    stat.st_size,
                )
            except OSError:
                cache_key = None
            if cache_key is not None and cache_key in self.cache:
                message = self.cache[cache_key]
                self.cache.move_to_end(cache_key)
            else:
                message = render_result_preview(data)
                if isinstance(message, PreviewReady) and cache_key is not None:
                    self.cache[cache_key] = message
                    while (
                        len(self.cache) > _CACHE_SIZE
                        or sum(len(item.jpeg_base64) for item in self.cache.values()) > _CACHE_BYTES
                    ):
                        self.cache.popitem(last=False)
            # Every answer, a cache hit and a failure from the large-result
            # child or its timeout included, names the request it answers.
            if isinstance(message, (PreviewReady, PreviewFailed)):
                message = replace(
                    message,
                    slot=str(data.get("slot", "")),
                    generation=_generation(data),
                    target_id=str(data.get("target_id", "")),
                )
            with self.condition:
                if self.closed:
                    return
            self.emit(message)
