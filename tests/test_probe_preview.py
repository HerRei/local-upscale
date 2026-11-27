import base64
import io
import sys
import time
import tracemalloc
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from localsr.core.image_io import ImageManager, load_preview
from localsr.core.image_io_16bit import write_tiff_16
from localsr.worker.server import _encode_chw_jpeg


def _old(path, maximum):
    tensor = ImageManager().load(str(path))["tensor"]
    _, height, width = tensor.shape
    encoded = _encode_chw_jpeg(tensor, maximum, quality=82)
    return np.asarray(Image.open(io.BytesIO(base64.b64decode(encoded)))), width, height


def _new(path, maximum):
    pixels, width, height = load_preview(str(path), maximum)
    encoded = _encode_chw_jpeg(pixels.transpose(2, 0, 1), maximum, quality=82)
    return np.asarray(Image.open(io.BytesIO(base64.b64decode(encoded)))), width, height


@pytest.fixture(scope="module")
def images(tmp_path_factory):
    directory = tmp_path_factory.mktemp("probe-images")
    x = np.arange(6000, dtype=np.uint16)[None, :]
    y = np.arange(4000, dtype=np.uint16)[:, None]
    rgb = np.empty((4000, 6000, 3), dtype=np.uint8)
    rgb[:, :, 0] = x * 255 // 6000
    rgb[:, :, 1] = y * 255 // 4000
    rgb[:, :, 2] = (x // 40 + y // 40) % 256
    image = Image.fromarray(rgb)
    exif = Image.Exif()
    exif[274] = 6
    image.save(directory / "oriented.jpg", quality=90, exif=exif)
    image.save(directory / "large.png")
    p3 = Path("/System/Library/ColorSync/Profiles/Display P3.icc")
    if p3.exists():
        image.resize((900, 600)).save(directory / "p3.jpg", icc_profile=p3.read_bytes())
    rgba = image.resize((900, 600)).convert("RGBA")
    rgba.putalpha(Image.linear_gradient("L").resize(rgba.size))
    rgba.save(directory / "alpha.png")
    grey = np.arange(900 * 600, dtype=np.uint32).reshape(600, 900).astype(np.uint16)
    Image.fromarray(grey).save(directory / "grey.png")
    wide = np.repeat(grey[:, :, None], 3, axis=2)
    write_tiff_16(directory / "wide.tiff", wide)
    if sys.platform == "darwin":
        import subprocess

        subprocess.run(
            [
                "/usr/bin/sips",
                "-s",
                "format",
                "heic",
                str(directory / "alpha.png"),
                "--out",
                str(directory / "image.heic"),
            ],
            check=True,
            capture_output=True,
        )
    return directory


def test_preview_matches_existing(images):
    for path in images.iterdir():
        if path.name == "large.png":
            continue
        old, width, height = _old(path, 2048)
        new, actual_width, actual_height = _new(path, 2048)
        assert (actual_width, actual_height) == (width, height), path
        difference = np.abs(old.astype(np.int16) - new.astype(np.int16))
        # DCT scaling and JPEG encoding can differ by a few levels at edges.
        assert difference.mean() <= 2, (path, difference.mean())
        assert difference.max() <= 32, (path, difference.max())


def test_a_large_jpeg_preview_never_decodes_the_full_image_in_float(images, monkeypatch):
    import localsr.core.image_io as image_io

    path = images / "oriented.jpg"
    start = time.perf_counter()
    tensor = ImageManager().load(str(path))["tensor"]
    pixels = tensor.clamp(0, 1).mul(255).byte().numpy().transpose(1, 2, 0)
    preview = Image.fromarray(pixels)
    preview.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
    old = time.perf_counter() - start
    del tensor, pixels, preview
    original = image_io.torch.from_numpy

    def check_array(array):
        assert array.size < 6000 * 4000 * 3
        return original(array)

    monkeypatch.setattr(image_io.torch, "from_numpy", check_array)
    # Pillow decodes outside Python's allocator, so check the decoded size itself: the
    # JPEG must already arrive reduced (DCT scaling), not at 6000 x 4000.
    decoded = []
    transpose = image_io.ImageOps.exif_transpose

    def record(image, **kwargs):
        image.load()
        decoded.append(image.size)
        return transpose(image, **kwargs)

    monkeypatch.setattr(image_io.ImageOps, "exif_transpose", record)
    start = time.perf_counter()
    load_preview(str(path), 2048)
    new = time.perf_counter() - start
    assert decoded and max(decoded[-1]) <= 3000, decoded
    tracemalloc.start()
    load_preview(str(path), 2048)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert peak < 6000 * 4000 * 3 * 4 // 2
    # Times only for the record (about 4x here); a wall-clock bound flakes on a busy machine,
    # and the asserts above already fail when the full image is decoded in float.
    print(f"JPEG old={old:.3f}s new={new:.3f}s")


@pytest.mark.parametrize(("sensor", "half"), [((6000, 4000), False), ((12000, 8000), True)])
def test_raw_preview_uses_half_size_only_far_above_the_preview_size(
    tmp_path, monkeypatch, sensor, half
):
    from types import SimpleNamespace

    path = tmp_path / "photo.dng"
    Image.new("RGB", (60, 40)).save(path, format="TIFF")
    calls = []
    long_side, short_side = sensor

    class Raw:
        sizes = SimpleNamespace(
            width=long_side, height=short_side, raw_width=long_side, raw_height=short_side, flip=6
        )

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def postprocess(self, **kwargs):
            calls.append(kwargs)
            divisor = 2 if kwargs["half_size"] else 1
            return np.zeros((long_side // divisor, short_side // divisor, 3), dtype=np.uint8)

    monkeypatch.setitem(
        sys.modules,
        "rawpy",
        SimpleNamespace(imread=lambda path: Raw(), ColorSpace=SimpleNamespace(sRGB=1)),
    )
    pixels, width, height = load_preview(str(path), 2048)
    assert (width, height) == (short_side, long_side)
    assert pixels.shape == (2048, round(2048 * short_side / long_side), 3)
    assert calls[0]["half_size"] is half
