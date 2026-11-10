"""Bounded, independently scheduled full-resolution Compare crops."""

import base64
import contextlib
import io
import os
import threading
from collections import OrderedDict

from PIL import Image, ImageCms, ImageOps

from localsr.protocol.messages import RegionFailed, RegionReady
from localsr.worker.result_preview import open_result_image

REGION_CACHE_BYTES = 256 * 1024 * 1024
# One image above the cache budget is decoded for a single crop and then dropped. This
# many budgets of pixels is the most such a decode may hold: a 4x output of a 12 MP photo
# (195 MP, 780 MB) fits, and the decode stays bounded.
ONE_OFF_BUDGETS = 4
_LIMIT_LOCK = threading.Lock()


@contextlib.contextmanager
def _pixel_limit_lifted():
    """Open an export of any size: Pillow refuses images above twice 89 MP as a bomb.

    These files are the app's own finished results, which a 4x upscale of a phone photo
    makes larger than that. Source images are checked against LocalSR's own pixel limit
    when they are read, and the memory of this decode is bounded by the caller.
    """
    with _LIMIT_LOCK:
        previous = Image.MAX_IMAGE_PIXELS
        Image.MAX_IMAGE_PIXELS = None
        try:
            yield
        finally:
            Image.MAX_IMAGE_PIXELS = previous


class RegionCache:
    def __init__(self, budget=REGION_CACHE_BYTES):
        self.budget = budget
        self.images = OrderedDict()
        self.bytes = 0

    def clear(self):
        for image, _ in self.images.values():
            image.close()
        self.images.clear()
        self.bytes = 0

    @staticmethod
    def _valid(x, y, width, height, tw, th, size):
        return not (
            x < 0
            or y < 0
            or width <= 0
            or height <= 0
            or x + width > size[0]
            or y + height > size[1]
            or not (1 <= tw <= 2048 and 1 <= th <= 2048)
        )

    @staticmethod
    def _finish(image):
        """Profile to sRGB and RGB: the pixels a crop shows."""
        profile = image.info.get("icc_profile")
        if profile:
            try:
                image = ImageCms.profileToProfile(
                    image,
                    ImageCms.ImageCmsProfile(io.BytesIO(profile)),
                    ImageCms.createProfile("sRGB"),
                    outputMode="RGB",
                )
            except (ImageCms.PyCMSError, OSError, ValueError):
                pass
        return image.convert("RGB")

    def render(self, data):
        identity = {
            key: data.get(key, "" if key in ("image_path", "slot", "target_id") else 0)
            for key in ("image_path", "slot", "generation", "target_id", "request_id")
        }
        try:
            path = identity["image_path"]
            stat = os.stat(path)
            key = (path, stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
            cached = key in self.images
            image = self.images[key][0] if cached else None
            x, y, width, height, tw, th = (
                int(data[k]) for k in ("x", "y", "width", "height", "target_width", "target_height")
            )
            crop = None
            if not cached:
                with _pixel_limit_lifted():
                    opened = open_result_image(path)
                with opened:
                    # Pillow stores RGB pixels in four bytes per pixel internally.
                    size = opened.width * opened.height * 4
                    if size > ONE_OFF_BUDGETS * self.budget:
                        raise ValueError("Image exceeds the Compare cache budget")
                    cached = size <= self.budget
                    while cached and self.bytes + size > self.budget:
                        _, (old, old_size) = self.images.popitem(last=False)
                        old.close()
                        self.bytes -= old_size
                    turned = opened.getexif().get(274, 1) != 1
                    if turned and size > 2 * self.budget:
                        # Turning it needs whole-image copies beside the decode.
                        raise ValueError("Image exceeds the Compare cache budget")
                    if not cached and not turned:
                        # Cut the crop out of the one decode before any whole-image copy
                        # (a profile conversion, RGB) can double it.
                        if not self._valid(x, y, width, height, tw, th, opened.size):
                            raise ValueError("Invalid Compare region")
                        crop = self._finish(opened.crop((x, y, x + width, y + height)))
                    else:
                        image = self._finish(ImageOps.exif_transpose(opened))
                if cached:
                    self.images[key] = (image, size)
                    self.bytes += size
            if cached:
                self.images.move_to_end(key)
            if crop is None:
                if not self._valid(x, y, width, height, tw, th, image.size):
                    raise ValueError("Invalid Compare region")
                crop = image.crop((x, y, x + width, y + height))
            crop = crop.resize((tw, th), Image.Resampling.LANCZOS)
            encoded = io.BytesIO()
            crop.save(encoded, format="PNG")
            return RegionReady(
                **identity, png_base64=base64.b64encode(encoded.getvalue()).decode("ascii")
            )
        except Exception as error:  # noqa: BLE001 - retain the preview on any decode failure
            return RegionFailed(**identity, error_message=str(error))


class RegionService:
    def __init__(self, emit):
        self.emit = emit
        self.cache = RegionCache()
        self.condition = threading.Condition()
        self.pending = {}
        self.epoch = 0
        self.clearing = False
        self.closed = False
        # Started by the first request: a worker that never compares keeps no thread.
        self.thread = None

    def _start(self):
        if self.thread is None and not self.closed:
            self.thread = threading.Thread(target=self._run, name="compare-regions", daemon=True)
            self.thread.start()

    def submit(self, data):
        with self.condition:
            if isinstance(data, dict) and data.get("slot") in ("slot1", "slot2", "slot3", "slot4"):
                self._start()
                # A newer request for a slot queues behind the others, and the
                # oldest request renders first, so no slot starves.
                self.pending.pop(data["slot"], None)
                self.pending[data["slot"]] = (self.epoch, dict(data))
                self.condition.notify()

    def clear(self):
        with self.condition:
            self.epoch += 1
            self.pending.clear()
            self.clearing = self.thread is not None
            self.condition.notify()

    def close(self):
        with self.condition:
            self.closed = True
            self.condition.notify()
        if self.thread is not None:
            self.thread.join(timeout=2)

    def _run(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.closed or self.clearing or self.pending)
                if self.closed:
                    self.cache.clear()
                    return
                if self.clearing:
                    self.cache.clear()
                    self.clearing = False
                    continue
                slot = next(iter(self.pending))
                epoch, data = self.pending.pop(slot)
            result = self.cache.render(data)
            with self.condition:
                if epoch == self.epoch and not self.closed:
                    self.emit(result)
