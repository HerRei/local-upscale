import os
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image, ImageCms

from localsr.core.image_io import ImageManager


def create_srgb_profile():
    return ImageCms.createProfile("sRGB").tobytes()


class FakeRawDecode:
    def __init__(self, rgb):
        self.rgb = rgb
        self.kwargs = None
        self.sizes = SimpleNamespace(width=6, height=4, flip=0)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def postprocess(self, **kwargs):
        self.kwargs = kwargs
        return self.rgb


def test_dng_load_uses_libraw_camera_wb_and_srgb(tmp_path, monkeypatch):
    path = tmp_path / "input.DNG"
    metadata_image = Image.new("RGB", (6, 4))
    exif = metadata_image.getexif()
    exif[315] = "RAW Photographer"
    metadata_image.save(path, format="TIFF", exif=exif)

    decoded = np.full((4, 6, 3), 128, dtype=np.uint8)
    fake_raw = FakeRawDecode(decoded)
    fake_color_space = SimpleNamespace(sRGB=object())
    fake_rawpy = SimpleNamespace(
        imread=lambda _path: fake_raw,
        ColorSpace=fake_color_space,
    )
    monkeypatch.setitem(sys.modules, "rawpy", fake_rawpy)

    data = ImageManager().load(str(path))

    assert data["tensor"].shape == (3, 4, 6)
    assert data["tensor"].dtype.is_floating_point
    assert data["safe_exif"][315] == "RAW Photographer"
    assert data["icc_profile"]
    assert fake_raw.kwargs == {
        "use_camera_wb": True,
        "use_auto_wb": False,
        "output_color": fake_color_space.sRGB,
        "output_bps": 8,
    }


def test_rgba_processing(tmp_path):
    img_path = str(tmp_path / "test_rgba.png")
    # Create RGBA image
    arr = np.zeros((10, 10, 4), dtype=np.uint8)
    arr[:, :, 3] = 128  # Semi-transparent
    img = Image.fromarray(arr, "RGBA")
    img.save(img_path)

    manager = ImageManager()
    data = manager.load(img_path)

    assert data["original_mode"] == "RGBA"
    assert data["alpha_image"] is not None
    assert data["tensor"].shape == (3, 10, 10)

    # Fake output writing
    out_mmap = np.zeros((3, 20, 20), dtype=np.uint8)
    out_path = str(tmp_path / "out_rgba.png")

    manager.save_from_writer(out_mmap, out_path, "png", 98, False, None, {}, 2)

    out_img = Image.open(out_path)
    assert out_img.mode == "RGBA"
    assert out_img.size == (20, 20)


def test_native_model_output_can_be_saved_at_a_smaller_selected_scale(tmp_path):
    manager = ImageManager()
    manager.alpha_channel = Image.new("L", (10, 8), color=180)
    native_4x_output = np.zeros((3, 32, 40), dtype=np.uint8)
    output_path = str(tmp_path / "selected_2x.png")

    manager.save_from_writer(
        native_4x_output,
        output_path,
        "png",
        98,
        False,
        None,
        {},
        4,
        output_scale=2,
    )

    with Image.open(output_path) as output:
        assert output.size == (20, 16)
        assert output.mode == "RGBA"


def test_exif_sanitization(tmp_path):
    # Testing exif extraction and orientation setting
    img_path = str(tmp_path / "test_exif.jpg")
    img = Image.new("RGB", (10, 10))
    # Add dummy exif
    exif = img.getexif()
    exif[274] = 6  # Orientation 90 deg
    exif[315] = "Test Artist"
    img.save(img_path, exif=exif)

    manager = ImageManager()
    data = manager.load(img_path)

    # 274 should not be in safe_exif normally because we don't copy it directly, we enforce 1 on save
    assert 315 in data["safe_exif"]
    assert data["safe_exif"][315] == "Test Artist"

    out_mmap = np.zeros((3, 10, 10), dtype=np.uint8)
    out_path = str(tmp_path / "out_exif.jpg")

    manager.save_from_writer(out_mmap, out_path, "jpg", 98, True, None, data["safe_exif"], 1)

    out_img = Image.open(out_path)
    out_exif = out_img.getexif()
    assert out_exif[274] == 1
    assert out_exif[315] == "Test Artist"


class MockOutputWriter:
    def __init__(self, arr):
        self._arr = arr

    def get_array(self):
        return self._arr


def test_image_manager_save_delegation(tmp_path):
    manager = ImageManager()
    out_mmap = np.zeros((3, 10, 10), dtype=np.uint8)
    writer = MockOutputWriter(out_mmap)
    out_path = str(tmp_path / "out_save.png")
    unrelated = Path(out_path + ".tmp")
    unrelated.write_text("belongs to the user", encoding="utf-8")

    manager.save(
        output_writer=writer,
        destination_path=out_path,
        format="png",
        quality=95,
        preserve_metadata=True,
        icc_profile=b"dummy_icc_bytes",
        safe_exif={315: "Test Artist"},
        scale=1,
    )

    assert os.path.exists(out_path)
    assert unrelated.read_text(encoding="utf-8") == "belongs to the user"
    assert not list(tmp_path.glob(".out_save.png.localsr-image-*.tmp"))
    out_img = Image.open(out_path)
    assert out_img.size == (10, 10)


def test_atomic_save_error_cleanup(tmp_path, monkeypatch):
    manager = ImageManager()
    out_mmap = np.zeros((3, 10, 10), dtype=np.uint8)
    out_path = str(tmp_path / "out_fail.png")

    def bad_replace(src, dst):
        assert os.path.exists(src)
        raise RuntimeError("Disk error during replace")

    monkeypatch.setattr(os, "replace", bad_replace)

    with pytest.raises(RuntimeError, match="Disk error during replace"):
        manager.save_from_writer(out_mmap, out_path, "png", 98, False, None, {}, 1)

    assert not list(tmp_path.glob(".out_fail.png.localsr-image-*.tmp"))
    assert not os.path.exists(out_path)


def test_cancel_during_encode_cleans_owned_scratch_and_preserves_existing_output(
    tmp_path, monkeypatch
):
    import threading

    cancel = threading.Event()
    destination = tmp_path / "existing.png"
    destination.write_bytes(b"previous output")
    scratch = tmp_path / ".localsr-job-test"
    scratch.mkdir()
    real_save = Image.Image.save

    def cancel_after_encoding(image, path, *args, **kwargs):
        assert Path(path).parent == scratch
        real_save(image, path, *args, **kwargs)
        cancel.set()

    monkeypatch.setattr(Image.Image, "save", cancel_after_encoding)
    with pytest.raises(InterruptedError, match="during image export"):
        ImageManager().save(
            np.zeros((3, 16, 16), dtype=np.uint8),
            str(destination),
            "png",
            temporary_directory=scratch,
            cancel_event=cancel,
        )
    assert destination.read_bytes() == b"previous output"
    assert not list(scratch.iterdir())


def test_avif_and_jpeg_2000_decode_through_pillow(tmp_path):
    for name, fmt in (("photo.avif", "AVIF"), ("scan.jp2", "JPEG2000")):
        path = tmp_path / name
        Image.new("RGB", (64, 48), (200, 30, 30)).save(path, format=fmt)
        data = ImageManager().load(str(path))
        assert data["tensor"].shape == (3, 48, 64)


def test_gif_transparency_survives_as_alpha(tmp_path):
    path = tmp_path / "logo.gif"
    image = Image.new("P", (8, 8), 0)
    image.putpalette([0, 0, 0, 255, 0, 0] + [0] * 762)
    image.paste(1, (2, 2, 6, 6))
    image.save(path, transparency=0)
    data = ImageManager().load(str(path))
    assert data["alpha_image"] is not None
    assert data["alpha_image"].getextrema() == (0, 255)


@pytest.mark.skipif(sys.platform != "darwin", reason="HEIC is decoded by macOS")
def test_heic_decodes_through_macos_without_a_spurious_alpha_channel(tmp_path):
    import subprocess

    source = tmp_path / "source.png"
    Image.new("RGB", (120, 80), (30, 90, 200)).save(source)
    heic = tmp_path / "IMG_0001.HEIC"
    subprocess.run(
        ["/usr/bin/sips", "-s", "format", "heic", str(source), "--out", str(heic)],
        check=True,
        capture_output=True,
    )
    data = ImageManager().load(str(heic))
    assert data["tensor"].shape == (3, 80, 120)
    assert data["alpha_image"] is None
    red, green, blue = (float(channel.mean()) for channel in data["tensor"])
    assert blue > green > red  # the colour survives HEVC and the profile conversion


@pytest.mark.skipif(sys.platform != "darwin", reason="HEIC is decoded by macOS")
def test_unreadable_heic_reports_that_macos_could_not_decode_it(tmp_path):
    from localsr.core.image_io import open_image

    broken = tmp_path / "broken.heic"
    broken.write_bytes(b"not an image")
    with pytest.raises(OSError, match="macOS could not decode broken.heic"):
        open_image(broken)


def test_open_photo_develops_camera_raw_for_editing(tmp_path, monkeypatch):
    from localsr.core.image_io import open_photo

    path = tmp_path / "DSCF0001.RAF"
    path.write_bytes(b"not a TIFF, as most RAW containers are not")
    fake_raw = FakeRawDecode(np.full((4, 6, 3), 90, dtype=np.uint8))
    monkeypatch.setitem(
        sys.modules,
        "rawpy",
        SimpleNamespace(imread=lambda _path: fake_raw, ColorSpace=SimpleNamespace(sRGB=object())),
    )
    with open_photo(path) as image:
        assert image.size == (6, 4) and image.mode == "RGB"
