"""Bounded, independently scheduled full-resolution Compare crops."""

import base64
import io
import os
import threading
from collections import OrderedDict

from PIL import Image, ImageCms, ImageOps

from localsr.protocol.messages import RegionFailed, RegionReady

REGION_CACHE_BYTES = 256 * 1024 * 1024


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
            if not cached:
                with Image.open(path) as opened:
                    # Pillow stores RGB pixels in four bytes per pixel internally.
                    size = opened.width * opened.height * 4
                    # One image above the cache budget is decoded for this crop
                    # only (a 4x output of a 12 MP photo); beyond twice the
                    # budget the preview stays.
                    if size > 2 * self.budget:
                        raise ValueError("Image exceeds the Compare cache budget")
                    cached = size <= self.budget
                    while cached and self.bytes + size > self.budget:
                        _, (old, old_size) = self.images.popitem(last=False)
                        old.close()
                        self.bytes -= old_size
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
                        except (ImageCms.PyCMSError, OSError, ValueError):
                            pass
                    image = image.convert("RGB")
                if cached:
                    self.images[key] = (image, size)
                    self.bytes += size
            if cached:
                self.images.move_to_end(key)
            x, y, width, height, tw, th = (
                int(data[k]) for k in ("x", "y", "width", "height", "target_width", "target_height")
            )
            if (
                x < 0
                or y < 0
                or width <= 0
                or height <= 0
                or x + width > image.width
                or y + height > image.height
                or not (1 <= tw <= 2048 and 1 <= th <= 2048)
            ):
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
        self.thread = threading.Thread(target=self._run, name="compare-regions", daemon=True)
        self.thread.start()

    def submit(self, data):
        with self.condition:
            if isinstance(data, dict) and data.get("slot") in ("slot1", "slot2", "slot3", "slot4"):
                # A newer request for a slot queues behind the others, and the
                # oldest request renders first, so no slot starves.
                self.pending.pop(data["slot"], None)
                self.pending[data["slot"]] = (self.epoch, dict(data))
                self.condition.notify()

    def clear(self):
        with self.condition:
            self.epoch += 1
            self.pending.clear()
            self.clearing = True
            self.condition.notify()

    def close(self):
        with self.condition:
            self.closed = True
            self.condition.notify()
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
