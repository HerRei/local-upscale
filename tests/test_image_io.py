import io
import os
import struct
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image, ImageCms

from localsr.core.image_io import ImageManager, open_image


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
    assert not list(tmp_path.glob(".localsr-image-*.tmp"))
    out_img = Image.open(out_path)
    assert out_img.size == (10, 10)


def test_atomic_save_error_cleanup(tmp_path, monkeypatch):
    manager = ImageManager()
    out_mmap = np.zeros((3, 10, 10), dtype=np.uint8)
    out_path = str(tmp_path / "out_fail.png")

    def bad_publish(src, dst):
        assert os.path.exists(src)
        raise RuntimeError("Disk error during publish")

    monkeypatch.setattr(os, "link", bad_publish)

    with pytest.raises(RuntimeError, match="Disk error during publish"):
        manager.save_from_writer(out_mmap, out_path, "png", 98, False, None, {}, 1)

    assert not list(tmp_path.glob(".localsr-image-*.tmp"))
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


def _grey_profile(gamma: float) -> bytes:
    """A minimal ICC v2 grey display profile with a gamma tone curve."""

    def s15(value):
        return struct.pack(">i", round(value * 65536))

    white = s15(0.9642) + s15(1.0) + s15(0.8249)
    name = b"Test grey\0"
    tags = [
        (b"desc", b"desc\0\0\0\0" + struct.pack(">I", len(name)) + name + b"\0" * 78),
        (b"wtpt", b"XYZ \0\0\0\0" + white),
        (b"kTRC", b"curv\0\0\0\0" + struct.pack(">IH", 1, round(gamma * 256)) + b"\0\0"),
        (b"cprt", b"text\0\0\0\0none\0\0\0\0"),
    ]
    offset = 128 + 4 + 12 * len(tags)
    table, body = struct.pack(">I", len(tags)), b""
    for signature, data in tags:
        data += b"\0" * (-len(data) % 4)
        table += signature + struct.pack(">II", offset + len(body), len(data))
        body += data
    header = struct.pack(">I", offset + len(body)) + b"lcms" + bytes([2, 0x10, 0, 0])
    header += b"mntrGRAYXYZ " + struct.pack(">6H", 2026, 11, 9, 0, 0, 0) + b"acspAPPL"
    header += b"\0" * 24 + white + b"lcms" + b"\0" * 44
    return header + table + body


def test_sixteen_bit_grey_scans_keep_their_grey_levels(tmp_path):
    # Scanners write 16-bit grey PNG and TIFF. Pillow clips those to 8 bits instead of
    # scaling them, which turned every grey above 0.4 % into white (issue #97).
    levels = np.tile(np.arange(256, dtype=np.uint16), (4, 1))
    for name, array in (
        ("scan.png", levels * 257),
        ("scan.tif", levels * 257),
        ("scan-int32.tif", (levels * 257).astype(np.int32)),
    ):
        path = tmp_path / name
        Image.fromarray(array).save(path)
        data = ImageManager().load(str(path))
        pixels = data["tensor"].mul(255).round().numpy()
        assert np.abs(pixels - levels).max() <= 1, name
        assert (pixels[0] == pixels[1]).all() and (pixels[1] == pixels[2]).all(), name


def test_a_grey_colour_profile_is_applied_and_the_output_tagged(tmp_path):
    # A grey ICC profile (Photoshop's Gray Gamma and Dot Gain profiles are common on manga
    # scans) used to fail the conversion, so its tone curve was ignored and the output
    # carried no profile: mid-tones shifted against the source in colour-managed apps.
    path = tmp_path / "linear-grey.png"
    Image.new("L", (8, 8), 128).save(path, icc_profile=_grey_profile(1.0))
    data = ImageManager().load(str(path))

    assert data["has_invalid_icc"] is False
    output_profile = ImageCms.ImageCmsProfile(io.BytesIO(data["icc_profile"]))
    assert "sRGB" in ImageCms.getProfileDescription(output_profile)
    # Linear 50 % grey is about 188 in sRGB.
    assert abs(float(data["tensor"].mean()) * 255 - 188) < 3


def test_a_grey_profile_keeps_transparency(tmp_path):
    path = tmp_path / "grey-alpha.png"
    image = Image.merge("LA", (Image.new("L", (8, 8), 128), Image.new("L", (8, 8), 77)))
    image.save(path, icc_profile=_grey_profile(1.0))
    data = ImageManager().load(str(path))

    assert data["has_invalid_icc"] is False
    assert data["alpha_image"].getextrema() == (77, 77)
    assert abs(float(data["tensor"].mean()) * 255 - 188) < 3


def _tinted(height, width):
    """A model output whose colour channels drift apart around a grey level."""
    output = np.full((3, height, width), 120, dtype=np.uint8)
    output[0] += 9
    output[2] -= 7
    return output


def _grey_palette_image():
    image = Image.new("P", (4, 3), 90)
    image.putpalette([value for level in range(256) for value in (level, level, level)])
    return image


@pytest.mark.parametrize(
    "make_source",
    [
        lambda: Image.new("L", (4, 3), 90),
        lambda: Image.new("RGB", (4, 3), (90, 90, 90)),
        lambda: Image.new("1", (4, 3), 1),
        _grey_palette_image,
        lambda: Image.merge("LA", (Image.new("L", (4, 3), 90), Image.new("L", (4, 3), 200))),
    ],
    ids=["L", "grey-RGB", "1-bit", "grey-palette", "LA"],
)
def test_a_grey_source_is_saved_without_the_colour_a_model_adds(tmp_path, make_source):
    # Grey pages carry no colour, so any chroma in the output is the model's invention
    # (HFA2k tints manga pages, issue #97). The saved file keeps the luma only.
    source = make_source()
    path = tmp_path / "page.png"
    source.save(path)
    manager = ImageManager()
    data = manager.load(str(path))
    assert data["grayscale"] is True

    output = tmp_path / "out.png"
    manager.save_from_writer(_tinted(12, 16), str(output), "png", 98, False, None, {}, 4)
    with Image.open(output) as saved:
        red, green, blue = (np.asarray(band) for band in saved.convert("RGB").split())
        assert (red == green).all() and (green == blue).all()
        # Rec. 601 luma of (129, 120, 113), as Pillow's convert("L") rounds it.
        assert int(green[0, 0]) == 122
        assert saved.mode == ("RGBA" if source.mode == "LA" else "RGB")


def test_a_colour_source_keeps_the_model_colour(tmp_path):
    path = tmp_path / "photo.png"
    Image.new("RGB", (4, 3), (90, 91, 90)).save(path)
    manager = ImageManager()
    assert manager.load(str(path))["grayscale"] is False

    output = tmp_path / "out.png"
    manager.save_from_writer(_tinted(12, 16), str(output), "png", 98, False, None, {}, 4)
    with Image.open(output) as saved:
        assert saved.getpixel((0, 0)) == (129, 120, 113)


def test_twelve_bit_tiff_uses_declared_sample_depth(tmp_path):
    # Two packed 12-bit samples, 2048 and 4095, in a baseline grayscale TIFF.
    entries = [
        (256, 4, 1, 2),
        (257, 4, 1, 1),
        (258, 3, 1, 12),
        (259, 3, 1, 1),
        (262, 3, 1, 1),
        (273, 4, 1, 122),
        (277, 3, 1, 1),
        (278, 4, 1, 1),
        (279, 4, 1, 3),
    ]
    path = tmp_path / "12bit.tif"
    path.write_bytes(
        b"II"
        + struct.pack("<HIH", 42, 8, len(entries))
        + b"".join(struct.pack("<HHII", *entry) for entry in entries)
        + struct.pack("<I", 0)
        + bytes([0x80, 0x0F, 0xFF])
    )
    pixels = ImageManager().load(str(path))["tensor"][0, 0].mul(255).round().tolist()
    assert pixels == [128, 255]


def _float_rgb_tiff(path, pixels):
    """A baseline 32-bit float RGB TIFF; Pillow cannot read or write one."""
    height, width, _ = pixels.shape
    data = pixels.astype("<f4").tobytes()
    bits_at = 8 + len(data)
    formats_at = bits_at + 6
    directory_at = formats_at + 6
    entries = [
        (256, 4, 1, width),
        (257, 4, 1, height),
        (258, 3, 3, bits_at),
        (259, 3, 1, 1),
        (262, 3, 1, 2),
        (273, 4, 1, 8),
        (277, 3, 1, 3),
        (278, 4, 1, height),
        (279, 4, 1, len(data)),
        (284, 3, 1, 1),
        (339, 3, 3, formats_at),
    ]
    path.write_bytes(
        b"II"
        + struct.pack("<HI", 42, directory_at)
        + data
        + struct.pack("<3H", 32, 32, 32)
        + struct.pack("<3H", 3, 3, 3)
        + struct.pack("<H", len(entries))
        + b"".join(struct.pack("<HHII", *entry) for entry in entries)
        + struct.pack("<I", 0)
    )


def test_float_grey_tiff_spans_the_full_range_instead_of_turning_black(tmp_path):
    ramp = np.tile(np.linspace(0.0, 1.0, 64, dtype=np.float32), (4, 1))
    path = tmp_path / "grey_float.tif"
    Image.fromarray(ramp).save(path)
    assert Image.open(path).mode == "F"
    pixels = ImageManager().load(str(path))["tensor"][0, 0].mul(255).round().numpy()
    assert pixels[0] == 0 and pixels[-1] == 255
    assert abs(pixels[32] - 32 * 255 / 63) <= 1


def test_float_grey_tiff_above_one_is_refused_instead_of_clipped_to_white(tmp_path):
    path = tmp_path / "grey_hdr.tif"
    Image.fromarray(np.linspace(0.0, 200.0, 64, dtype=np.float32)[None, :]).save(path)
    with pytest.raises(ValueError, match="floating-point"):
        ImageManager().load(str(path))


def test_float_rgb_tiff_says_it_is_not_supported(tmp_path):
    path = tmp_path / "rgb_float.tif"
    _float_rgb_tiff(path, np.random.default_rng(1).random((8, 8, 3)))
    with pytest.raises(OSError, match="floating-point colour TIFF"):
        ImageManager().load(str(path))


def _png_claiming(path, width, height):
    """A few bytes of PNG whose header claims a huge size; its pixels are not there."""
    import zlib

    def chunk(kind, body):
        return (
            struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
        )

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00" * 16))
        + chunk(b"IEND", b"")
    )


@pytest.mark.parametrize(("width", "height"), [(14_000, 14_000), (90_000, 90_000)])
def test_an_image_beyond_the_decompression_limit_is_refused_with_one_sentence(
    tmp_path, monkeypatch, width, height
):
    path = tmp_path / "claims.png"
    _png_claiming(path, width, height)

    def never(*_args, **_kwargs):
        raise AssertionError("pixels were decoded")

    monkeypatch.setattr(Image.Image, "load", never)
    with pytest.raises(ValueError, match="more than 179 megapixels"):
        ImageManager().load(str(path))


def test_an_image_in_the_range_pillow_only_warns_about_is_not_refused_for_its_size(
    tmp_path, recwarn
):
    from localsr.core.image_io import MAX_INPUT_PIXELS, require_pixel_budget

    assert MAX_INPUT_PIXELS == 2 * Image.MAX_IMAGE_PIXELS
    require_pixel_budget(11_648, 8_736)  # a 102 megapixel medium-format frame
    require_pixel_budget(13_377, 13_377)  # the last square size inside the limit
    with pytest.raises(ValueError, match="megapixels"):
        require_pixel_budget(13_378, 13_378)
    path = tmp_path / "claims.png"
    _png_claiming(path, 12_000, 10_000)
    # Its data is not there, so the load stops as damaged; the size alone does not stop it
    # and Pillow's warning is not raised.
    with pytest.raises(OSError, match="claims.png is damaged"):
        ImageManager().load(str(path))
    assert not [w for w in recwarn if issubclass(w.category, Image.DecompressionBombWarning)]


def _damaged_files(tmp_path):
    from PIL import ImageDraw

    picture = Image.new("RGB", (96, 64), (10, 20, 30))
    ImageDraw.Draw(picture).ellipse((10, 10, 80, 50), fill=(200, 90, 40))
    files = {"zero.png": b"", "text.jpg": b"this is not a picture", "garbage.tif": os.urandom(300)}
    for name, (fmt, options) in {
        "cut.png": ("PNG", {}),
        "cut.jpg": ("JPEG", {"quality": 95}),
        "cut.tif": ("TIFF", {}),
        "cut.webp": ("WEBP", {}),
        "cut.bmp": ("BMP", {}),
        "cut.gif": ("GIF", {}),
    }.items():
        buffer = io.BytesIO()
        picture.save(buffer, format=fmt, **options)
        files[name] = buffer.getvalue()[: len(buffer.getvalue()) * 6 // 10]
    paths = []
    for name, data in files.items():
        (tmp_path / name).write_bytes(data)
        paths.append(tmp_path / name)
    return paths


@pytest.mark.filterwarnings("ignore::RuntimeWarning")
def test_every_damaged_file_gives_one_plain_sentence_and_keeps_the_library_text_as_cause(
    tmp_path,
):
    for path in _damaged_files(tmp_path):
        with pytest.raises(OSError) as raised:
            ImageManager().load(str(path))
        assert str(raised.value) == (
            f"{path.name} is damaged or is not a picture format LocalSR can read."
        ), path.name
        assert raised.value.__cause__ is not None, path.name


def test_a_real_input_output_failure_keeps_its_own_message(tmp_path):
    with pytest.raises(FileNotFoundError, match="missing.png"):
        ImageManager().load(str(tmp_path / "missing.png"))
    with pytest.raises(OSError, match="Is a directory"):
        ImageManager().load(str(tmp_path))


def test_a_camera_raw_file_libraw_cannot_read_gives_the_same_sentence(tmp_path, monkeypatch):
    path = tmp_path / "broken.NEF"
    path.write_bytes(b"not raw")

    class LibRawFileUnsupportedError(Exception):
        pass

    def imread(_path):
        raise LibRawFileUnsupportedError(b"Unsupported file format or not RAW file")

    monkeypatch.setitem(
        sys.modules,
        "rawpy",
        SimpleNamespace(
            imread=imread,
            LibRawError=LibRawFileUnsupportedError,
            ColorSpace=SimpleNamespace(sRGB=object()),
        ),
    )
    with pytest.warns(RuntimeWarning), pytest.raises(OSError) as raised:
        ImageManager().load(str(path))
    assert str(raised.value) == "broken.NEF is damaged or is not a picture format LocalSR can read."
    assert isinstance(raised.value.__cause__, LibRawFileUnsupportedError)


@pytest.mark.filterwarnings("ignore:Corrupt EXIF data")
def test_the_macos_decoder_drops_the_manual_pointer_and_a_broken_tiff_is_damage(
    tmp_path, monkeypatch
):
    from localsr.core import image_io

    source = tmp_path / "x.heic"
    source.write_bytes(b"x")

    def failing(_argv, **_kwargs):
        return subprocess.CompletedProcess([], 1, "", "Error 4: no such image\nTry 'sips --help'")

    monkeypatch.setattr(image_io.subprocess, "run", failing)
    with pytest.raises(OSError) as raised:
        image_io._decode_with_macos(str(source))
    assert str(raised.value) == "macOS could not decode x.heic: Error 4: no such image"

    def broken_tiff(argv, **_kwargs):
        Path(argv[argv.index("--out") + 1]).write_bytes(b"II*\x00broken")
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(image_io.subprocess, "run", broken_tiff)
    with pytest.raises(OSError, match="the file looks damaged") as raised:
        image_io._decode_with_macos(str(source))
    assert "BytesIO" not in str(raised.value)


def test_sixteen_bit_grey_profile_preserves_shadow_detail(tmp_path):
    path = tmp_path / "linear16.png"
    Image.fromarray(np.array([[0, 128, 256]], dtype=np.uint16)).save(
        path, icc_profile=_grey_profile(1.0)
    )
    data = ImageManager().load(str(path))
    assert not data["has_invalid_icc"]
    pixels = data["tensor"][0, 0].mul(255).round().tolist()
    assert pixels[0] == 0 and pixels[1] >= 2 and pixels[2] > pixels[1]


@pytest.mark.skipif(sys.platform != "darwin", reason="HEIC is encoded by macOS")
def test_heic_export_preserves_colour_metadata_and_quality(tmp_path):
    rng = np.random.default_rng(94)
    pixels = rng.integers(0, 256, (96, 128, 3), dtype=np.uint8)
    manager = ImageManager()
    sizes = []
    for quality in (20, 95):
        path = tmp_path / f"quality-{quality}.heic"
        manager.save_from_writer(
            pixels.transpose(2, 0, 1),
            str(path),
            "heic",
            quality,
            True,
            None,
            {315: "LocalSR test"},
            1,
        )
        sizes.append(path.stat().st_size)
        decoded = open_image(path)
        assert decoded.size == (128, 96)
        assert decoded.info.get("icc_profile")
        assert decoded.getexif().get(315) == "LocalSR test"
        if quality == 95:
            error = np.abs(np.asarray(decoded.convert("RGB"), dtype=int) - pixels.astype(int))
            assert error.mean() < 55
    assert sizes[1] > sizes[0]


def test_heic_export_needs_macos(tmp_path, monkeypatch):
    monkeypatch.setattr("localsr.core.image_io.sys.platform", "linux")
    path = tmp_path / "out.heic"
    with pytest.raises(ValueError, match="needs macOS"):
        ImageManager().save_from_writer(
            np.zeros((3, 8, 8), dtype=np.uint8), str(path), "heic", 90, True, None, {}, 1
        )
    assert not path.exists() and not list(tmp_path.iterdir())


@pytest.mark.skipif(sys.platform != "darwin", reason="HEIC is encoded by macOS")
def test_heic_export_keeps_transparency(tmp_path):
    rng = np.random.default_rng(95)
    pixels = rng.integers(0, 256, (3, 64, 64), dtype=np.uint8)
    manager = ImageManager()
    manager.alpha_channel = Image.fromarray(
        np.repeat(np.array([0, 255], dtype=np.uint8), 32)[:, None].repeat(64, axis=1)
    )
    path = tmp_path / "alpha.heic"
    manager.save_from_writer(pixels, str(path), "heic", 90, True, None, {}, 1)
    alpha = np.asarray(open_image(path).convert("RGBA"))[..., 3]
    assert alpha[:24].max() <= 8 and alpha[40:].min() >= 247


def test_an_image_job_refuses_heic_before_any_inference_without_macos(monkeypatch):
    from unittest.mock import MagicMock

    from localsr.worker.image_job import ImageJobRunner

    monkeypatch.setattr("localsr.core.image_io.sys.platform", "win32")
    engine = MagicMock()
    runner = ImageJobRunner(
        engine=engine,
        model_adapter=MagicMock(),
        cancel_event=MagicMock(),
        emit=MagicMock(),
        emit_preview=MagicMock(),
    )
    with pytest.raises(ValueError, match="needs macOS"):
        runner.run("job", {"output_format": "heic"})
    engine.release_models_except.assert_not_called()


def _save_jpeg_with_source_tags(path, tags, fmt="jpg"):
    manager = ImageManager()
    manager.save_from_writer(
        np.full((3, 8, 8), 120, dtype=np.uint8), str(path), fmt, 90, True, None, tags, 1
    )
    return manager


def test_a_source_tag_too_long_for_a_jpeg_is_left_out_and_the_save_says_so(tmp_path):
    tags = {315: "a" * 120_000, 33432: "(c) 2026 Someone", 306: "2026:11:10 09:00:00"}
    manager = _save_jpeg_with_source_tags(tmp_path / "out.jpg", tags)
    exif = Image.open(tmp_path / "out.jpg").getexif()
    assert exif.get(315) is None
    assert exif[33432] == "(c) 2026 Someone" and exif[306] == "2026:11:10 09:00:00"
    assert exif[274] == 1
    assert manager.notes == [
        "The photo's artist text was too long to fit a JPEG file and was left out."
    ]


def test_the_same_long_tag_is_kept_where_the_format_has_room_for_it(tmp_path):
    for fmt in ("png", "tif", "webp"):
        manager = _save_jpeg_with_source_tags(tmp_path / f"out.{fmt}", {315: "a" * 120_000}, fmt)
        assert Image.open(tmp_path / f"out.{fmt}").getexif().get(315) == "a" * 120_000
        assert manager.notes == []


def test_a_jpeg_with_small_tags_keeps_all_of_them_without_a_note(tmp_path):
    manager = _save_jpeg_with_source_tags(tmp_path / "out.jpg", {315: "Artist"})
    assert Image.open(tmp_path / "out.jpg").getexif()[315] == "Artist"
    assert manager.notes == []


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs named pipes")
def test_a_pipe_is_refused_as_a_source_instead_of_waiting_for_a_writer(tmp_path):
    import threading

    pipe = tmp_path / "source.png"
    os.mkfifo(pipe)
    outcome = []

    def load():
        try:
            ImageManager().load(str(pipe))
        except Exception as error:  # noqa: BLE001 - the test inspects it
            outcome.append(error)

    thread = threading.Thread(target=load, daemon=True)
    thread.start()
    thread.join(5)
    try:
        assert not thread.is_alive(), "load() is waiting for a writer on the pipe"
        assert isinstance(outcome[0], ValueError) and "pipe, device or socket" in str(outcome[0])
    finally:
        if thread.is_alive():
            # Release the blocked reader so the thread ends.
            os.close(os.open(pipe, os.O_WRONLY))
            thread.join(5)


@pytest.mark.parametrize("name", ["anim.gif", "anim.webp", "anim.png", "pages.tif"])
def test_a_file_with_several_frames_uses_the_first_and_says_so(tmp_path, name):
    first = Image.new("RGB", (16, 12), (255, 0, 0))
    second = Image.new("RGB", (16, 12), (0, 0, 255))
    path = tmp_path / name
    first.save(path, save_all=True, append_images=[second], lossless=True)
    manager = ImageManager()
    data = manager.load(str(path))
    assert data["tensor"][:, 0, 0].mul(255).round().tolist()[0] > 200
    assert manager.notes == [f"{name} has 2 frames or pages; only the first one was used."]


def test_a_single_picture_gets_no_frame_note(tmp_path):
    path = tmp_path / "one.png"
    Image.new("RGB", (4, 4)).save(path)
    manager = ImageManager()
    manager.load(str(path))
    assert manager.notes == []
