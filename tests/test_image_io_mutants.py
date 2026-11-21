"""Colour-pipeline and macOS-codec tests aimed at mutants that the older tests missed.

Images that Pillow cannot save in every mode are handed to ``ImageManager.load`` through
a patched ``open_image``; the macOS ``sips`` calls are replaced by fakes, so these run
everywhere.
"""

import io
import subprocess
import sys
import warnings
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image, ImageCms, JpegImagePlugin
from test_image_io import _grey_profile

from localsr.core import image_io
from localsr.core.image_io import ImageManager, _narrow_wide_grey, require_output_format


def _srgb_bytes() -> bytes:
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def _srgb_encode(linear: float) -> float:
    return 12.92 * linear if linear <= 0.0031308 else 1.055 * linear ** (1 / 2.4) - 0.055


def _load(monkeypatch, image, *, bits=None):
    """Load ``image`` as if it had been read from disk, with a declared sample depth."""
    if bits is not None:
        image.tag_v2 = {258: bits}
        image.info["exif"] = b""  # keeps getexif() away from the TIFF-only path
    monkeypatch.setattr(image_io, "open_image", lambda _path: image)
    return ImageManager().load("memory.png")


def _channels(data):
    return data["tensor"].mul(255).round().numpy()


# --- wide grey scaling -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("array", "bits", "expected"),
    [
        # An I image that only holds 8-bit values is taken as it is.
        (np.array([[0, 1, 128, 255]], dtype=np.int32), 16, [0, 1, 128, 255]),
        # One value above 255 makes it 16-bit: 255 is then almost black.
        (np.array([[0, 255, 256, 65535]], dtype=np.int32), 16, [0, 1, 1, 255]),
        (np.array([[0, 32768, 65535]], dtype=np.int32), 16, [0, 128, 255]),
        # 256 is the smallest value that makes an I image 16-bit.
        (np.array([[0, 256]], dtype=np.int32), 16, [0, 1]),
        # I;16 is never taken as 8-bit, however small its values are.
        (np.array([[0, 255, 256, 65535]], dtype=np.uint16), 16, [0, 1, 1, 255]),
        (np.array([[0, 1, 128, 255]], dtype=np.uint16), 16, [0, 0, 0, 1]),
        # 12-bit samples span 0..4095.
        (np.array([[0, 2048, 4095]], dtype=np.uint16), 12, [0, 128, 255]),
        (np.array([[0, 1, 2047, 4095]], dtype=np.uint16), 12, [0, 0, 127, 255]),
        (np.array([[0, 128, 255]], dtype=np.uint16), 8, [0, 128, 255]),
    ],
    ids=[
        "I-8bit",
        "I-16bit",
        "I-mid",
        "I-256",
        "I;16-low",
        "I;16-tiny",
        "12bit",
        "12bit-edge",
        "8bit-I;16",
    ],
)
def test_wide_grey_values_scale_to_exact_8_bit_levels(array, bits, expected):
    narrowed = _narrow_wide_grey(Image.fromarray(array), bits)
    assert narrowed.mode == "L"
    assert np.asarray(narrowed)[0].tolist() == expected


def test_the_narrowed_image_keeps_the_source_info():
    image = Image.fromarray(np.array([[0, 65535]], dtype=np.uint16))
    image.info["icc_profile"] = b"profile"
    assert _narrow_wide_grey(image).info["icc_profile"] == b"profile"


@pytest.mark.parametrize("mode", ["I;16", "I;16L", "I;16B", "I;16N", "I", "L", "1", "LA"])
def test_each_grey_mode_loads_as_neutral_grey_rgb(monkeypatch, mode):
    ramp = np.array([[0, 64, 128, 255]] * 2)
    if mode in ("I;16", "I;16L", "I;16B", "I;16N"):
        byte_order = ">u2" if mode == "I;16B" else "<u2"
        raw = (ramp * 257).astype(byte_order).tobytes()
        image = Image.frombytes(mode, (4, 2), raw)
        expected = ramp
    elif mode == "I":
        image = Image.fromarray((ramp * 257).astype(np.int32))
        expected = ramp
    elif mode == "1":
        image = Image.fromarray(ramp > 100).convert("1")
        expected = np.where(ramp > 100, 255, 0)
    elif mode == "L":
        image = Image.fromarray(ramp.astype(np.uint8))
        expected = ramp
    else:
        pair = np.stack([ramp, np.full_like(ramp, 255)], axis=-1).astype(np.uint8)
        image = Image.fromarray(pair, "LA").convert(mode)
        expected = ramp
    data = _load(monkeypatch, image)
    pixels = _channels(data)
    assert data["grayscale"] is True
    assert (pixels[0] == pixels[1]).all() and (pixels[1] == pixels[2]).all()
    assert np.abs(pixels[0] - expected).max() <= 1


# --- the ICC pipeline --------------------------------------------------------------------


LEVELS = [0, 17, 34, 136, 255]


def _reference_through_the_profile(monkeypatch):
    """The 8-bit grey path through the same profile: what each wide path must equal."""
    image = Image.fromarray(np.array([LEVELS], dtype=np.uint8))
    image.info["icc_profile"] = _grey_profile(1.0)
    return _channels(_load(monkeypatch, image))[0, 0]


@pytest.mark.parametrize(
    ("make", "bits"),
    [
        (lambda: Image.fromarray(np.array([[v * 257 for v in LEVELS]], dtype=np.uint16)), None),
        (lambda: Image.fromarray(np.array([[v * 273 // 17 for v in LEVELS]], dtype=np.uint16)), 12),
        (lambda: Image.fromarray(np.array([LEVELS], dtype=np.int32)), None),
        (lambda: Image.fromarray(np.array([[v * 257 for v in LEVELS]], dtype=np.int32)), None),
    ],
    ids=["16bit", "12bit", "I-8bit-values", "I-16bit-values"],
)
def test_wide_grey_with_a_profile_matches_the_same_levels_in_8_bit(monkeypatch, make, bits):
    expected = _reference_through_the_profile(monkeypatch)
    assert expected.tolist() == sorted(expected.tolist()) and expected[-1] == 255
    image = make()
    image.info["icc_profile"] = _grey_profile(1.0)
    data = _load(monkeypatch, image, bits=bits)
    assert data["has_invalid_icc"] is False
    assert np.abs(_channels(data)[0, 0] - expected).max() <= 1, _channels(data)[0, 0]


@pytest.mark.parametrize("bits", [None, 12])
def test_a_wide_grey_with_a_profile_that_does_not_fit_is_read_without_it(monkeypatch, bits):
    values = np.array([[0, 2048, 4095]] if bits == 12 else [[0, 32768, 65535]], dtype=np.uint16)
    image = Image.fromarray(values)
    image.info["icc_profile"] = _srgb_bytes()  # an RGB profile cannot convert grey samples
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        data = _load(monkeypatch, image, bits=bits)
    assert data["has_invalid_icc"] is True
    assert any("Invalid ICC profile" in str(item.message) for item in caught)
    assert _channels(data)[0, 0].tolist() == [0, 128, 255]


def test_a_broken_icc_profile_is_reported_and_the_pixels_stay(monkeypatch):
    image = Image.new("RGB", (3, 2), (10, 120, 250))
    image.info["icc_profile"] = b"this is not a profile"
    with pytest.warns(RuntimeWarning, match="Invalid ICC profile"):
        data = _load(monkeypatch, image)
    assert data["has_invalid_icc"] is True
    assert data["icc_profile"] is None
    assert _channels(data)[:, 0, 0].tolist() == [10, 120, 250]


def test_a_valid_profile_marks_the_output_as_srgb(monkeypatch):
    image = Image.new("RGB", (3, 2), (10, 120, 250))
    image.info["icc_profile"] = _srgb_bytes()
    data = _load(monkeypatch, image)
    assert data["has_invalid_icc"] is False
    assert data["icc_profile"]
    assert np.abs(_channels(data)[:, 0, 0] - [10, 120, 250]).max() <= 1


def _palette(transparent: bool):
    image = Image.new("P", (4, 2), 1)
    image.putpalette([0, 0, 0, 200, 40, 40] + [0] * 762)
    image.putpixel((0, 0), 0)
    if transparent:
        image.info["transparency"] = 0
    return image


@pytest.mark.parametrize(
    "make",
    [
        lambda: _palette(False),
        lambda: _palette(True),
        lambda: _palette(True).convert("PA"),
        lambda: Image.new("RGBA", (4, 2), (200, 40, 40, 77)),
    ],
    ids=["P", "P-transparent", "PA", "RGBA"],
)
def test_palette_and_alpha_images_convert_through_their_profile(monkeypatch, make):
    image = make()
    image.info["icc_profile"] = _srgb_bytes()
    data = _load(monkeypatch, image)
    assert data["has_invalid_icc"] is False
    assert np.abs(_channels(data)[:, 1, 3] - [200, 40, 40]).max() <= 1
    if image.mode in ("PA", "RGBA") or "transparency" in image.info:
        alpha = np.asarray(data["alpha_image"])
        expected = 77 if image.mode == "RGBA" else 255
        assert alpha[1, 3] == expected
        if image.mode != "RGBA":
            assert alpha[0, 0] == 0
    else:
        assert data["alpha_image"] is None


def test_a_grey_profile_converts_one_bit_and_grey_alpha(monkeypatch):
    bilevel = Image.fromarray(np.array([[0, 255]] * 2, dtype=np.uint8)).convert("1")
    bilevel.info["icc_profile"] = _grey_profile(1.0)
    data = _load(monkeypatch, bilevel)
    assert data["has_invalid_icc"] is False
    assert _channels(data)[0, 0].tolist() == [0, 255]

    pair = np.stack([np.full((2, 2), 128), np.array([[0, 255], [90, 255]])], axis=-1)
    grey_alpha = Image.fromarray(pair.astype(np.uint8), "LA")
    grey_alpha.info["icc_profile"] = _grey_profile(1.0)
    data = _load(monkeypatch, grey_alpha)
    assert data["has_invalid_icc"] is False
    assert np.asarray(data["alpha_image"]).tolist() == [[0, 255], [90, 255]]
    assert abs(float(_channels(data)[0, 0, 0]) - 188) <= 2


# --- grey or colour ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pixel", "grey"),
    [
        ((90, 90, 90), True),
        ((90, 91, 90), False),
        ((90, 90, 91), False),
        ((91, 90, 90), False),
        ((255, 0, 0), False),
        ((0, 255, 0), False),
        ((0, 0, 255), False),
    ],
)
def test_only_an_image_with_three_equal_channels_counts_as_grey(monkeypatch, pixel, grey):
    image = Image.new("RGB", (3, 3), (50, 50, 50))
    image.putpixel((2, 1), pixel)
    image.putpixel((0, 0), (50, 50, 50))
    other = Image.new("RGB", (3, 3), pixel)
    assert _load(monkeypatch, other)["grayscale"] is grey
    # One odd pixel among grey ones is enough to make the image colour.
    if not grey:
        assert _load(monkeypatch, image)["grayscale"] is False


# --- RAW metadata fallback and the EXIF allow-list ----------------------------------------


def test_a_raw_file_whose_metadata_cannot_be_read_is_still_developed(tmp_path, monkeypatch):
    path = tmp_path / "DSCF0002.RAF"
    path.write_bytes(b"not a TIFF")
    raw = SimpleNamespace(
        postprocess=lambda **_kw: np.full((4, 6, 3), 90, dtype=np.uint8),
        sizes=SimpleNamespace(width=6, height=4, flip=0),
    )

    class Raw:
        def __enter__(self):
            return raw

        def __exit__(self, *_args):
            return False

    monkeypatch.setitem(
        sys.modules,
        "rawpy",
        SimpleNamespace(imread=lambda _p: Raw(), ColorSpace=SimpleNamespace(sRGB=object())),
    )
    with pytest.warns(RuntimeWarning, match="Could not read RAW metadata"):
        data = ImageManager().load(str(path))
    assert data["tensor"].shape == (3, 4, 6)
    assert data["safe_exif"] == {}
    assert data["icc_profile"]


def test_a_file_that_is_not_an_image_is_an_error_and_not_a_blank_photo(tmp_path):
    path = tmp_path / "broken.png"
    path.write_bytes(b"not an image")
    with pytest.raises(OSError, match="broken.png is damaged or is not a picture format"):
        ImageManager().load(str(path))


def test_only_the_allow_listed_exif_tags_are_carried(tmp_path):
    image = Image.new("RGB", (8, 8))
    exif = image.getexif()
    allowed = {306: "2026:11:10 09:00:00", 315: "Artist", 33432: "Copyright"}
    for tag, value in {
        **allowed,
        270: "Description",
        274: 6,
        271: "Make",
        272: "Model",
        305: "Software",
        316: "Host computer",
        33434: 1,
        42033: "serial",
    }.items():
        exif[tag] = value
    path = tmp_path / "photo.jpg"
    image.save(path, exif=exif)
    assert ImageManager().load(str(path))["safe_exif"] == allowed


# --- save --------------------------------------------------------------------------------


def _writer(width=8, height=6):
    rng = np.random.default_rng(7)
    return rng.integers(0, 256, (3, height, width), dtype=np.uint8)


def test_save_defaults_keep_profile_and_metadata_and_the_real_dimensions(tmp_path):
    out = tmp_path / "out.png"
    pixels = _writer(16, 12)
    ImageManager().save(
        pixels, str(out), "png", icc_profile=_srgb_bytes(), safe_exif={315: "Someone"}, scale=2
    )
    with Image.open(out) as saved:
        assert saved.size == (16, 12)
        assert saved.info.get("icc_profile") == _srgb_bytes()
        exif = saved.getexif()
        assert exif[315] == "Someone"
        assert exif[274] == 1
        assert exif[40962] == 16 and exif[40963] == 12
        np.testing.assert_array_equal(np.asarray(saved), pixels.transpose(1, 2, 0))


def test_a_reduced_output_scale_resizes_and_tags_the_new_size(tmp_path):
    out = tmp_path / "out.png"
    ImageManager().save(_writer(16, 12), str(out), "png", scale=4, output_scale=2)
    with Image.open(out) as saved:
        assert saved.size == (8, 6)
        assert saved.getexif()[40962] == 8 and saved.getexif()[40963] == 6


@pytest.mark.parametrize("output_scale", [0, -1, 5])
def test_an_output_scale_outside_one_to_native_is_refused(tmp_path, output_scale):
    out = tmp_path / "out.png"
    with pytest.raises(ValueError, match="between 1 and"):
        ImageManager().save(_writer(), str(out), "png", scale=4, output_scale=output_scale)
    assert list(tmp_path.iterdir()) == []
    for allowed in (1, 4):
        allowed_out = tmp_path / f"out-{allowed}.png"
        ImageManager().save(_writer(), str(allowed_out), "png", scale=4, output_scale=allowed)


@pytest.mark.parametrize("fmt", ["png", "tif", "tiff", "webp", "heic", "PNG", "TIFF"])
def test_alpha_survives_the_formats_that_can_hold_it(tmp_path, monkeypatch, fmt):
    manager = ImageManager()
    alpha = np.tile(np.array([0, 255, 90, 0, 255, 90], dtype=np.uint8), (4, 1))
    manager.alpha_channel = Image.fromarray(alpha)
    pixels = _writer(6, 4)
    out = tmp_path / f"out.{fmt}"
    if fmt.lower() == "heic":
        seen = {}

        def fake_encode(image, destination, quality, **kwargs):
            seen["mode"] = image.mode
            seen["alpha"] = np.asarray(image.getchannel("A")).tolist()
            destination.write_bytes(b"heic")

        monkeypatch.setattr(image_io, "_encode_with_macos", fake_encode)
        manager.save(pixels, str(out), fmt, scale=1)
        assert seen["mode"] == "RGBA"
        assert seen["alpha"] == alpha.tolist()
        return
    manager.save(pixels, str(out), fmt, quality=100, scale=1)
    with Image.open(out) as saved:
        assert saved.mode == "RGBA"
        got = np.asarray(saved.getchannel("A"))
        assert got[0, 0] == 0 and got[0, 1] == 255 and got[0, 2] == 90


def test_jpeg_has_no_alpha_and_keeps_full_colour_resolution(tmp_path):
    manager = ImageManager()
    manager.alpha_channel = Image.new("L", (6, 4), 0)
    for name in ("a.jpg", "b.jpeg", "c.JPG"):
        out = tmp_path / name
        manager.save(_writer(6, 4), str(out), name.rsplit(".", 1)[1], quality=90, scale=1)
        with Image.open(out) as saved:
            assert saved.format == "JPEG" and saved.mode == "RGB"
            assert JpegImagePlugin.get_sampling(saved) == 0


def _direct(fmt, pixels, **options):
    buffer = io.BytesIO()
    Image.fromarray(pixels.transpose(1, 2, 0), "RGB").save(buffer, format=fmt, **options)
    return buffer.getvalue()


@pytest.mark.parametrize("quality", [10, 75])
def test_jpeg_and_webp_are_written_with_the_requested_quality(tmp_path, quality):
    pixels = _writer(32, 24)
    for fmt, name, options in (
        ("JPEG", "q.jpg", {"quality": quality, "subsampling": 0}),
        ("WEBP", "q.webp", {"quality": quality, "method": 6}),
    ):
        out = tmp_path / name
        ImageManager().save(
            pixels, str(out), name.split(".")[1], quality=quality, preserve_metadata=False, scale=1
        )
        assert out.read_bytes() == _direct(fmt, pixels, **options)


def test_tiff_is_lossless_and_deflate_compressed(tmp_path):
    pixels = np.zeros((3, 40, 40), dtype=np.uint8)
    pixels[:, 10:30, 10:30] = 200
    for name in ("a.tif", "b.tiff"):
        out = tmp_path / name
        ImageManager().save(pixels, str(out), name.split(".")[1], preserve_metadata=False, scale=1)
        with Image.open(out) as saved:
            assert saved.format == "TIFF"
            assert saved.info["compression"] == "tiff_adobe_deflate"
            np.testing.assert_array_equal(np.asarray(saved), pixels.transpose(1, 2, 0))


# --- the macOS codecs, with sips replaced -------------------------------------------------


def _fake_sips(monkeypatch, handler):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return handler(argv)

    monkeypatch.setattr(image_io.subprocess, "run", run)
    return calls


def _result(code=0, out="", err=""):
    return subprocess.CompletedProcess([], code, out, err)


def _write_tiff(image, **options):
    def handler(argv):
        image.save(argv[argv.index("--out") + 1], format="TIFF", **options)
        return _result()

    return handler


def test_system_decoded_extensions_are_opened_through_the_macos_decoder(tmp_path, monkeypatch):
    monkeypatch.setattr(image_io, "is_system_decoded", lambda path: str(path).endswith(".heic"))
    monkeypatch.setattr(image_io, "_decode_with_macos", lambda path: f"decoded {path}")
    assert image_io.open_image("a.heic") == "decoded a.heic"
    Image.new("RGB", (2, 2)).save(tmp_path / "b.png")
    assert image_io.open_image(tmp_path / "b.png").size == (2, 2)


def test_the_macos_decoder_calls_sips_with_the_file_and_a_tiff_target(tmp_path, monkeypatch):
    source = tmp_path / "IMG_0001.HEIC"
    source.write_bytes(b"x")
    calls = _fake_sips(monkeypatch, _write_tiff(Image.new("RGB", (5, 4), (1, 2, 3))))
    image = image_io._decode_with_macos(str(source))
    argv, kwargs = calls[0]
    assert argv[:5] == ["/usr/bin/sips", "-s", "format", "tiff", str(source)]
    assert argv[5] == "--out" and argv[6].endswith("decoded.tiff") and len(argv) == 7
    assert kwargs["timeout"] == 300 and kwargs["capture_output"] is True
    assert image.size == (5, 4) and image.getpixel((0, 0)) == (1, 2, 3)


@pytest.mark.parametrize(
    "handler",
    [
        lambda argv: _result(1, "", "Error: bad\nsecond line"),
        lambda argv: _result(0),
        lambda argv: _result(2),
    ],
    ids=["failed", "no-output", "failed-no-message"],
)
def test_the_macos_decoder_refuses_a_failed_run(tmp_path, monkeypatch, handler):
    source = tmp_path / "broken.heic"
    source.write_bytes(b"x")
    _fake_sips(monkeypatch, handler)
    with pytest.raises(OSError, match="macOS could not decode broken.heic"):
        image_io._decode_with_macos(str(source))


def test_a_failed_sips_run_does_not_hand_over_its_partial_file(tmp_path, monkeypatch):
    source = tmp_path / "half.heic"
    source.write_bytes(b"x")

    def handler(argv):
        Image.new("RGB", (4, 4)).save(argv[argv.index("--out") + 1], format="TIFF")
        return _result(1, "", "Error: partial")

    _fake_sips(monkeypatch, handler)
    with pytest.raises(OSError, match="Error: partial"):
        image_io._decode_with_macos(str(source))


def test_the_macos_decoder_keeps_real_transparency_and_the_profile(tmp_path, monkeypatch):
    source = tmp_path / "alpha.heic"
    source.write_bytes(b"x")
    ramp = np.zeros((4, 4, 4), dtype=np.uint8)
    ramp[..., :3] = (30, 60, 90)
    ramp[..., 3] = np.linspace(0, 255, 16, dtype=np.uint8).reshape(4, 4)
    exif = Image.new("RGB", (1, 1)).getexif()
    exif[315] = "Artist"
    _fake_sips(
        monkeypatch,
        _write_tiff(Image.fromarray(ramp, "RGBA"), icc_profile=_srgb_bytes(), exif=exif),
    )
    image = image_io._decode_with_macos(str(source))
    assert image.mode == "RGBA"
    assert np.asarray(image.getchannel("A")).tolist() == ramp[..., 3].tolist()
    assert image.info["icc_profile"] == _srgb_bytes()


@pytest.mark.parametrize("lowest", [255, 254])
def test_only_a_fully_opaque_alpha_is_dropped_from_the_macos_decode(tmp_path, monkeypatch, lowest):
    source = tmp_path / "photo.heic"
    source.write_bytes(b"x")
    pixels = np.zeros((3, 3, 4), dtype=np.uint8)
    pixels[..., :3] = (200, 100, 50)
    pixels[..., 3] = 255
    pixels[0, 0, 3] = lowest
    exif = Image.new("RGB", (1, 1)).getexif()
    exif[315] = "Artist"
    _fake_sips(
        monkeypatch,
        _write_tiff(Image.fromarray(pixels, "RGBA"), icc_profile=_srgb_bytes(), exif=exif),
    )
    image = image_io._decode_with_macos(str(source))
    assert image.mode == ("RGB" if lowest == 255 else "RGBA")
    if lowest == 255:
        assert image.info["icc_profile"] == _srgb_bytes()
        assert image.getexif().get(315) == "Artist"
        assert image.getpixel((0, 0)) == (200, 100, 50)


def test_the_macos_decoder_reports_a_timeout_or_a_missing_sips(tmp_path, monkeypatch):
    source = tmp_path / "slow.heic"
    source.write_bytes(b"x")
    for error in (subprocess.TimeoutExpired("sips", 300), FileNotFoundError("sips")):

        def run(*_a, error=error, **_k):
            raise error

        monkeypatch.setattr(image_io.subprocess, "run", run)
        with pytest.raises(OSError, match="macOS could not decode slow.heic"):
            image_io._decode_with_macos(str(source))


def test_heic_output_is_refused_off_macos_and_everything_else_is_allowed(monkeypatch):
    monkeypatch.setattr(image_io.sys, "platform", "linux")
    for name in ("heic", "HEIC", "Heic"):
        with pytest.raises(ValueError, match="needs macOS"):
            require_output_format(name)
    for name in ("png", "jpg", "jpeg", "tif", "tiff", "webp", "bmp"):
        require_output_format(name)
    monkeypatch.setattr(image_io.sys, "platform", "darwin")
    for name in ("heic", "png"):
        require_output_format(name)


def test_the_macos_encoder_writes_a_lossless_tiff_and_runs_sips(tmp_path, monkeypatch):
    monkeypatch.setattr(image_io.sys, "platform", "darwin")
    destination = tmp_path / "out.heic"
    pixels = _writer(10, 8).transpose(1, 2, 0)
    exif = Image.new("RGB", (1, 1)).getexif()
    exif[315] = "Artist"
    seen = {}

    def handler(argv):
        source = argv[argv.index("--out") - 1]
        with Image.open(source) as tiff:
            seen["format"] = tiff.format
            seen["compression"] = tiff.info["compression"]
            seen["icc"] = tiff.info.get("icc_profile")
            seen["artist"] = tiff.getexif().get(315)
            seen["pixels"] = np.asarray(tiff).copy()
        seen["parent"] = str(source)
        open(argv[argv.index("--out") + 1], "wb").write(b"heic bytes")
        return _result()

    calls = _fake_sips(monkeypatch, handler)
    image_io._encode_with_macos(
        Image.fromarray(pixels, "RGB"), destination, 90, icc_profile=_srgb_bytes(), exif=exif
    )
    argv, kwargs = calls[0]
    assert argv[:6] == ["/usr/bin/sips", "-s", "format", "heic", "-s", "formatOptions"]
    assert argv[6] == "90" and argv[8:] == ["--out", str(destination)]
    assert argv[7].endswith("source.tiff") and argv[7] == seen["parent"]
    assert kwargs["timeout"] == 300
    assert seen["format"] == "TIFF" and seen["compression"] == "tiff_adobe_deflate"
    assert seen["icc"] == _srgb_bytes() and seen["artist"] == "Artist"
    np.testing.assert_array_equal(seen["pixels"], pixels)
    assert destination.read_bytes() == b"heic bytes"
    # The scratch folder is gone, and nothing else was left beside the output.
    assert [path.name for path in tmp_path.iterdir()] == ["out.heic"]


@pytest.mark.parametrize(
    ("quality", "expected"), [(0, "1"), (1, "1"), (100, "100"), (150, "100"), (55.7, "55")]
)
def test_the_macos_encoder_clamps_the_quality(tmp_path, monkeypatch, quality, expected):
    monkeypatch.setattr(image_io.sys, "platform", "darwin")

    def handler(argv):
        open(argv[argv.index("--out") + 1], "wb").write(b"x")
        return _result()

    calls = _fake_sips(monkeypatch, handler)
    image_io._encode_with_macos(Image.new("RGB", (4, 4)), tmp_path / "q.heic", quality)
    assert calls[0][0][6] == expected


@pytest.mark.parametrize(
    ("make", "message"),
    [
        (
            lambda path: (path.write_bytes(b"partial"), _result(1, "", "Error: no encoder"))[1],
            "no encoder",
        ),
        (lambda path: _result(0), "could not write"),
        (lambda path: (path.write_bytes(b""), _result(0))[1], "could not write"),
    ],
    ids=["failed-with-partial-file", "no-output", "empty-output"],
)
def test_the_macos_encoder_refuses_a_failed_or_empty_result(tmp_path, monkeypatch, make, message):
    monkeypatch.setattr(image_io.sys, "platform", "darwin")
    destination = tmp_path / "bad.heic"
    _fake_sips(monkeypatch, lambda argv: make(destination))
    with pytest.raises(OSError, match=message):
        image_io._encode_with_macos(Image.new("RGB", (4, 4)), destination, 90)


def test_the_macos_encoder_reports_a_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(image_io.sys, "platform", "darwin")

    def run(*_a, **_k):
        raise subprocess.TimeoutExpired("sips", 300)

    monkeypatch.setattr(image_io.subprocess, "run", run)
    with pytest.raises(OSError, match="could not write the HEIC image"):
        image_io._encode_with_macos(Image.new("RGB", (4, 4)), tmp_path / "t.heic", 90)


# --- what the ICC step hands to the colour transform -------------------------------------


@pytest.mark.parametrize(
    ("array", "bits", "expected"),
    [
        (np.array([[0, 2048, 4095]], dtype=np.uint16), 12, [0, 32776, 65535]),
        (np.array([[0, 1, 255]], dtype=np.uint8).astype(np.int32), None, [0, 257, 65535]),
        (np.array([[0, 256, 65535]], dtype=np.int32), None, [0, 256, 65535]),
        (np.array([[0, 128, 32768, 65535]], dtype=np.uint16), None, [0, 128, 32768, 65535]),
    ],
    ids=["12bit", "I-8bit-values", "I-16bit-values", "16bit"],
)
def test_wide_grey_is_scaled_to_16_bit_before_the_profile_is_applied(
    monkeypatch, array, bits, expected
):
    seen = {}
    real = image_io._to_srgb

    def spy(image, source, srgb):
        seen["mode"] = image.mode
        seen["values"] = np.asarray(image).astype(np.int64)[0].tolist()
        return real(image, source, srgb)

    monkeypatch.setattr(image_io, "_to_srgb", spy)
    image = Image.fromarray(array)
    image.info["icc_profile"] = _grey_profile(1.0)
    _load(monkeypatch, image, bits=bits)
    assert seen["mode"] == "I;16"
    assert seen["values"] == expected


@pytest.mark.parametrize("mode", ["LA", "PA"])
def test_grey_and_palette_alpha_without_a_profile_is_kept(monkeypatch, mode):
    if mode == "LA":
        pairs = np.stack([np.full((2, 2), 90), np.array([[0, 255], [90, 255]])], axis=-1)
        image = Image.fromarray(pairs.astype(np.uint8), "LA")
    else:
        image = _palette(True).convert("PA")
        image.putalpha(Image.fromarray(np.array([[0, 255, 90, 255]] * 2, dtype=np.uint8)))
    data = _load(monkeypatch, image)
    assert data["alpha_image"] is not None
    assert np.asarray(data["alpha_image"])[0, :2].tolist() == [0, 255]


def test_the_macos_encoder_refuses_before_it_creates_anything_off_macos(tmp_path, monkeypatch):
    monkeypatch.setattr(image_io.sys, "platform", "linux")
    calls = _fake_sips(monkeypatch, lambda argv: _result())
    with pytest.raises(ValueError, match="needs macOS"):
        image_io._encode_with_macos(Image.new("RGB", (4, 4)), tmp_path / "x.heic", 90)
    assert calls == [] and list(tmp_path.iterdir()) == []


def test_the_defaults_of_save_are_native_scale_and_high_quality(tmp_path):
    manager = ImageManager()
    manager.alpha_channel = Image.new("L", (16, 12), 200)
    pixels = _writer(16, 12)
    out = tmp_path / "default.png"
    manager.save(pixels, str(out), "png")
    with Image.open(out) as saved:
        assert saved.mode == "RGBA" and saved.size == (16, 12)
    jpeg = tmp_path / "default.jpg"
    ImageManager().save(pixels, str(jpeg), "jpg", preserve_metadata=False)
    assert jpeg.read_bytes() == _direct("JPEG", pixels, quality=98, subsampling=0)


def test_transparency_follows_a_reduced_output_scale(tmp_path):
    manager = ImageManager()
    manager.alpha_channel = Image.new("L", (8, 6), 200)
    out = tmp_path / "half.png"
    manager.save(_writer(32, 24), str(out), "png", scale=4, output_scale=2)
    with Image.open(out) as saved:
        assert saved.size == (16, 12) and saved.getchannel("A").getextrema() == (200, 200)


def test_the_heic_encoder_gets_the_requested_quality(tmp_path, monkeypatch):
    seen = {}

    def fake_encode(image, destination, quality, **kwargs):
        seen["quality"] = quality
        destination.write_bytes(b"heic")

    monkeypatch.setattr(image_io, "_encode_with_macos", fake_encode)
    ImageManager().save(_writer(), str(tmp_path / "q.heic"), "heic", quality=77)
    assert seen["quality"] == 77
