import numpy as np
import pytest
from PIL import Image, ImageCms, features

from localsr.core.image_io import SAFE_EXIF_TAGS, ImageManager


def _srgb_profile() -> bytes:
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def test_metadata_allowlist_excludes_location_and_machine_identity():
    assert SAFE_EXIF_TAGS == {306, 315, 33432}
    assert 34853 not in SAFE_EXIF_TAGS  # GPS IFD
    assert 316 not in SAFE_EXIF_TAGS  # HostComputer
    assert 42033 not in SAFE_EXIF_TAGS  # BodySerialNumber


def test_orientation_is_applied_once_and_output_dimensions_are_regenerated(tmp_path):
    source = tmp_path / "oriented.jpg"
    image = Image.new("RGB", (3, 2), "red")
    exif = image.getexif()
    exif[274] = 6
    exif[315] = "Fixture Artist"
    exif[316] = "private-host"
    image.save(source, exif=exif)

    manager = ImageManager()
    loaded = manager.load(str(source))
    assert loaded["tensor"].shape == (3, 3, 2)
    assert loaded["safe_exif"] == {315: "Fixture Artist"}

    output = tmp_path / "oriented-output.jpg"
    manager.save_from_writer(
        np.zeros((3, 6, 4), dtype=np.uint8),
        str(output),
        "jpg",
        95,
        True,
        _srgb_profile(),
        loaded["safe_exif"],
        2,
    )
    with Image.open(output) as result:
        assert result.size == (4, 6)
        result_exif = result.getexif()
        assert result_exif[274] == 1
        assert result_exif[40962] == 4
        assert result_exif[40963] == 6
        assert result_exif[315] == "Fixture Artist"
        assert 316 not in result_exif


@pytest.mark.parametrize("output_format", ["png", "tif", "webp"])
def test_alpha_and_srgb_profile_survive_supported_alpha_formats(tmp_path, output_format):
    if output_format == "webp" and not features.check("webp"):
        pytest.skip("Pillow was built without WebP")
    manager = ImageManager()
    manager.alpha_channel = Image.new("L", (3, 2), 123)
    output = tmp_path / f"alpha.{output_format}"
    manager.save_from_writer(
        np.full((3, 4, 6), 80, dtype=np.uint8),
        str(output),
        output_format,
        95,
        True,
        _srgb_profile(),
        {315: "Fixture Artist"},
        2,
    )
    with Image.open(output) as result:
        assert result.size == (6, 4)
        assert "A" in result.getbands()
        assert result.getchannel("A").getextrema() == (123, 123)
        assert result.info.get("icc_profile")
        result_exif = result.getexif()
        assert result_exif[274] == 1
        assert result_exif[40962] == 6
        assert result_exif[40963] == 4


def test_metadata_opt_out_strips_exif_and_icc_and_jpeg_strips_alpha(tmp_path):
    manager = ImageManager()
    manager.alpha_channel = Image.new("L", (2, 2), 100)
    output = tmp_path / "private.jpg"
    manager.save_from_writer(
        np.zeros((3, 2, 2), dtype=np.uint8),
        str(output),
        "jpg",
        95,
        False,
        _srgb_profile(),
        {315: "Private Artist"},
        1,
    )
    with Image.open(output) as result:
        assert result.mode == "RGB"
        assert not result.getexif()
        assert "icc_profile" not in result.info


def test_metadata_opt_in_generates_only_structural_exif_when_source_has_none(tmp_path):
    output = tmp_path / "structural.jpg"
    ImageManager().save_from_writer(
        np.zeros((3, 7, 11), dtype=np.uint8),
        str(output),
        "jpg",
        95,
        True,
        None,
        {},
        1,
    )
    with Image.open(output) as result:
        exif = result.getexif()
        assert dict(exif) == {274: 1, 40962: 11, 40963: 7}
