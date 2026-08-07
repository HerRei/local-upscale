from PIL import Image

from localsr.core import image_formats


def test_probe_dng_uses_display_oriented_container_dimensions(tmp_path):
    path = tmp_path / "portrait.DNG"
    image = Image.new("RGB", (12, 8))
    exif = image.getexif()
    exif[274] = 6
    image.save(path, format="TIFF", exif=exif)

    assert image_formats.probe_image_size(path) == (8, 12)
    assert image_formats.is_raw_input(path)
    assert "*.dng" in image_formats.IMAGE_FILE_DIALOG_FILTER


def test_probe_raster_applies_exif_orientation(tmp_path):
    path = tmp_path / "rotated.jpg"
    image = Image.new("RGB", (12, 8))
    exif = image.getexif()
    exif[274] = 6
    image.save(path, exif=exif)

    assert image_formats.probe_image_size(path) == (8, 12)
