import os

import numpy as np
import pytest
from PIL import Image, ImageCms

from localsr.core.image_io import ImageManager


def create_srgb_profile():
    return ImageCms.createProfile("sRGB").tobytes()


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
    assert not os.path.exists(out_path + ".tmp")
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

    # Check that temporary file .tmp was cleaned up
    assert not os.path.exists(out_path + ".tmp")
    assert not os.path.exists(out_path)
