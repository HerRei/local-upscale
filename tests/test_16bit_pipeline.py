"""End-to-end 16-bit photo pipeline tests: I/O, validation, metadata, memory, and 8-bit parity."""

from __future__ import annotations

import io
import struct
import sys
import threading
import zlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import cv2
import numpy as np
import pytest
import tifffile
import torch
from PIL import Image, ImageCms
from test_image_io import _grey_profile

from localsr.core import disk_space
from localsr.core.image_io import ImageManager
from localsr.core.inference import InferenceEngine
from localsr.core.live_preview import encode_preview_jpeg
from localsr.core.model_adapter import NormalizedModelInfo
from localsr.core.output_writer import OutputWriter
from localsr.worker import image_batch
from localsr.worker.image_job import ImageJobRunner
from localsr.worker.request_validation import InvalidRequest, validate_image_job
from localsr.worker.server import _engine_features


def _srgb_bytes() -> bytes:
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def _encode_png(
    arr: np.ndarray,
    color_type: int,
    bit_depth: int = 16,
    *,
    interlace: int = 0,
    icc_profile: bytes | None = None,
    exif_bytes: bytes | None = None,
) -> bytes:
    """Minimal pure-Python PNG writer for test fixtures (supports 8/16-bit and Adam7)."""
    h, w = arr.shape[:2]
    channels = 1 if arr.ndim == 2 else arr.shape[2]
    dtype = ">u2" if bit_depth == 16 else "u1"
    adam7 = (
        (0, 0, 8, 8),
        (4, 0, 8, 8),
        (0, 4, 4, 8),
        (2, 0, 4, 4),
        (0, 2, 2, 4),
        (1, 0, 2, 2),
        (0, 1, 1, 2),
    )
    passes = adam7 if interlace else ((0, 0, 1, 1),)
    scanlines = bytearray()
    for x0, y0, dx, dy in passes:
        sub = arr[y0::dy, x0::dx]
        if sub.shape[0] == 0 or sub.shape[1] == 0:
            continue
        raw = np.ascontiguousarray(sub, dtype=dtype).view(np.uint8)
        row_bytes = sub.shape[1] * channels * (2 if bit_depth == 16 else 1)
        raw_2d = raw.reshape(sub.shape[0], row_bytes)
        for row in raw_2d:
            scanlines.append(0)
            scanlines.extend(row.tobytes())

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", w, h, bit_depth, color_type, 0, 0, interlace)
    parts = [b"\x89PNG\r\n\x1a\n", chunk(b"IHDR", ihdr)]
    if icc_profile:
        parts.append(chunk(b"iCCP", b"ICC Profile\x00\x00" + zlib.compress(icc_profile)))
    if exif_bytes:
        raw_exif = exif_bytes[6:] if exif_bytes.startswith(b"Exif\x00\x00") else exif_bytes
        parts.append(chunk(b"eXIf", raw_exif))
    parts.append(chunk(b"IDAT", zlib.compress(bytes(scanlines))))
    parts.append(chunk(b"IEND", b""))
    return b"".join(parts)


def _decode_png_16(data: bytes) -> tuple[int, int, int, int, np.ndarray, dict[bytes, bytes]]:
    """Verify a PNG's IHDR bit depth and decode its raw 16-bit sample array."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos = 8
    chunks: dict[bytes, list[bytes]] = {}
    while pos < len(data):
        length, tag = struct.unpack(">I4s", data[pos : pos + 8])
        body = data[pos + 8 : pos + 8 + length]
        crc = struct.unpack(">I", data[pos + 8 + length : pos + 12 + length])[0]
        assert (zlib.crc32(tag + body) & 0xFFFFFFFF) == crc
        chunks.setdefault(tag, []).append(body)
        pos += 12 + length
        if tag == b"IEND":
            break
    w, h, depth, colour, comp, filt, interlace = struct.unpack(">IIBBBBB", chunks[b"IHDR"][0])
    assert (comp, filt, interlace) == (0, 0, 0)
    assert depth == 16
    assert colour in (0, 2, 4, 6)
    decoded = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    assert decoded is not None and decoded.dtype == np.uint16
    if colour == 2:
        pixels = cv2.cvtColor(decoded, cv2.COLOR_BGR2RGB)
    elif colour == 6:
        pixels = cv2.cvtColor(decoded, cv2.COLOR_BGRA2RGBA)
    elif colour == 4:
        pixels = np.stack((decoded[:, :, 0], decoded[:, :, 3]), axis=-1)
    else:
        pixels = decoded
    extra = {k: b"".join(v) for k, v in chunks.items() if k not in (b"IHDR", b"IDAT", b"IEND")}
    return w, h, depth, colour, pixels, extra


class _Identity1xModel(torch.nn.Module):
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return x


def _identity_engine_and_adapter():
    info = NormalizedModelInfo("Identity", 1, 3, 3, True, False, 1, 1, "identity.pth", [])
    adapter = SimpleNamespace(
        inspect=lambda _path: info,
        load=lambda _path, _dev, _prec: (_Identity1xModel().eval(), info),
        release=lambda: None,
        status_callback=None,
        cancel_event=None,
        allow_unverified_checkpoints=True,
        approved_checkpoint=None,
    )
    engine = InferenceEngine(adapter)
    return engine, adapter, info


def _run_identity_job(
    tmp_path: Path, source_path: Path, output_path: Path, fmt: str, bit_depth: int
):
    engine, adapter, _ = _identity_engine_and_adapter()
    scratch = tmp_path / "scratch"
    scratch.mkdir(exist_ok=True)
    emitted = []
    runner = ImageJobRunner(
        engine=engine,
        model_adapter=adapter,
        cancel_event=threading.Event(),
        emit=emitted.append,
        emit_preview=lambda _pkt: None,
        preview_factory=lambda *a, **k: SimpleNamespace(
            close=lambda: None, submit=lambda **_: False
        ),
    )
    data = {
        "job_id": f"job-{bit_depth}",
        "image_path": str(source_path),
        "model_path": "identity.pth",
        "output_path": str(output_path),
        "scratch_directory": str(scratch),
        "output_temporary_directory": str(scratch),
        "output_format": fmt,
        "output_bit_depth": bit_depth,
        "device": "cpu",
        "tile_size": 64,
        "halo": 8,
        "precision": "fp32",
        "jpeg_quality": 98,
        "preserve_metadata": True,
        "safe_memory": False,
        "preview_enabled": False,
        "output_scale": 1,
    }
    validate_image_job(data)
    runner.run(data["job_id"], data)
    return emitted


def _sample_rgb16(h: int = 19, w: int = 23) -> np.ndarray:
    rng = np.random.default_rng(70)
    arr = rng.integers(0, 65536, (h, w, 3), dtype=np.uint16)
    # Include exact 16-bit step differences that 8-bit quantization would collapse.
    arr[0, 0] = (1000, 1001, 1002)
    arr[0, 1] = (32767, 32768, 32769)
    arr[0, 2] = (65533, 65534, 65535)
    return arr


@pytest.mark.parametrize("interlace", [0, 1])
def test_16bit_rgb_png_round_trip_preserves_values_within_one_16bit_step(tmp_path, interlace):
    src_arr = _sample_rgb16()
    exif = Image.new("RGB", (23, 19)).getexif()
    exif[315] = "Hermes"
    exif[316] = "private-host"
    src_path = tmp_path / f"in_{interlace}.png"
    src_path.write_bytes(
        _encode_png(
            src_arr,
            color_type=2,
            bit_depth=16,
            interlace=interlace,
            icc_profile=_srgb_bytes(),
            exif_bytes=exif.tobytes(),
        )
    )
    out_path = tmp_path / f"out_{interlace}.png"
    _run_identity_job(tmp_path, src_path, out_path, "png", 16)

    w, h, depth, colour, decoded, extra = _decode_png_16(out_path.read_bytes())
    assert (w, h, depth, colour) == (23, 19, 16, 2)
    assert np.abs(decoded.astype(np.int32) - src_arr.astype(np.int32)).max() <= 1
    # Metadata allow-list + ICC survived and Pillow reads both back from the written PNG.
    assert b"iCCP" in extra and b"eXIf" in extra
    with Image.open(out_path) as pil_out:
        assert pil_out.info.get("icc_profile") == _srgb_bytes()
        out_exif = pil_out.getexif()
        assert out_exif[274] == 1
        assert out_exif[315] == "Hermes"
        assert out_exif[40962] == 23
        assert out_exif[40963] == 19
        assert 316 not in out_exif


def test_16bit_rgb_tiff_round_trip_preserves_values_within_one_16bit_step(tmp_path):
    src_arr = _sample_rgb16(17, 29)
    src_path = tmp_path / "in.tif"
    tifffile.imwrite(
        src_path,
        src_arr,
        photometric="rgb",
        iccprofile=_srgb_bytes(),
        extratags=[(315, "s", 0, "TIFF Artist", False), (316, "s", 0, "secret-host", False)],
    )
    out_path = tmp_path / "out.tif"
    _run_identity_job(tmp_path, src_path, out_path, "tif", 16)

    with tifffile.TiffFile(out_path) as tf:
        page = tf.pages[0]
        assert page.bitspersample == 16
        assert page.samplesperpixel == 3
        decoded = page.asarray()
        assert decoded.dtype == np.uint16
        assert np.abs(decoded.astype(np.int32) - src_arr.astype(np.int32)).max() <= 1
        assert 34675 in page.tags
        assert page.tags[315].value == "TIFF Artist"
        assert 316 not in page.tags
        assert 305 not in page.tags  # no tifffile.py software leakage


def test_16bit_rgba_png_round_trip_preserves_rgb_and_16bit_alpha(tmp_path):
    rng = np.random.default_rng(71)
    src_arr = rng.integers(0, 65536, (15, 21, 4), dtype=np.uint16)
    src_arr[0, 0, 3] = 1234
    src_arr[0, 1, 3] = 1235
    src_path = tmp_path / "in_rgba.png"
    src_path.write_bytes(_encode_png(src_arr, color_type=6, bit_depth=16))

    out_path = tmp_path / "out_rgba.png"
    _run_identity_job(tmp_path, src_path, out_path, "png", 16)

    w, h, depth, colour, decoded, _ = _decode_png_16(out_path.read_bytes())
    assert (w, h, depth, colour) == (21, 15, 16, 6)
    assert np.abs(decoded.astype(np.int32) - src_arr.astype(np.int32)).max() <= 1


def test_16bit_grey_png_round_trip_preserves_grey_precision_and_strips_chroma(tmp_path):
    rng = np.random.default_rng(72)
    src_grey = rng.integers(0, 65536, (16, 20), dtype=np.uint16)
    src_grey[0, :3] = [1000, 1001, 1002]
    src_path = tmp_path / "in_grey.png"
    src_path.write_bytes(_encode_png(src_grey, color_type=0, bit_depth=16))

    out_path = tmp_path / "out_grey.png"
    _run_identity_job(tmp_path, src_path, out_path, "png", 16)

    w, h, depth, colour, decoded, _ = _decode_png_16(out_path.read_bytes())
    assert (w, h, depth, colour) == (20, 16, 16, 2)
    assert (decoded[..., 0] == decoded[..., 1]).all()
    assert (decoded[..., 1] == decoded[..., 2]).all()
    assert np.abs(decoded[..., 0].astype(np.int32) - src_grey.astype(np.int32)).max() <= 1

    # Also verify a tinted model output on a 16-bit grey source keeps luma only without 8-bit collapse.
    manager = ImageManager()
    loaded = manager.load(str(src_path), bit_depth=16)
    assert loaded["grayscale"] is True
    tinted = np.full((3, 8, 8), 30000, dtype=np.uint16)
    tinted[0] = 30100
    tinted[2] = 29900
    tinted_out = tmp_path / "tinted_grey.png"
    manager.save_from_writer(
        tinted, str(tinted_out), "png", 98, False, None, {}, 1, output_bit_depth=16
    )
    _, _, _, _, tinted_decoded, _ = _decode_png_16(tinted_out.read_bytes())
    assert (tinted_decoded[..., 0] == tinted_decoded[..., 1]).all()
    assert 29950 < int(tinted_decoded[0, 0, 0]) < 30050


def _wide_rgb_profile(gamma: float = 1.0) -> bytes:
    """Minimal ICC v2 RGB profile with wide primaries and a custom gamma TRC."""

    def s15(value: float) -> bytes:
        return struct.pack(">i", round(value * 65536))

    def xyz_tag(x: float, y: float, z: float) -> bytes:
        return b"XYZ \0\0\0\0" + s15(x) + s15(y) + s15(z)

    white = s15(0.9642) + s15(1.0) + s15(0.8249)
    name = b"Wide Test RGB\0"
    trc = b"curv\0\0\0\0" + struct.pack(">IH", 1, round(gamma * 256)) + b"\0\0"
    tags = [
        (b"desc", b"desc\0\0\0\0" + struct.pack(">I", len(name)) + name + b"\0" * 78),
        (b"wtpt", b"XYZ \0\0\0\0" + white),
        (b"rXYZ", xyz_tag(0.6097, 0.3111, 0.0195)),
        (b"gXYZ", xyz_tag(0.2053, 0.6257, 0.0609)),
        (b"bXYZ", xyz_tag(0.1492, 0.0632, 0.7446)),
        (b"rTRC", trc),
        (b"gTRC", trc),
        (b"bTRC", trc),
        (b"cprt", b"text\0\0\0\0none\0\0\0\0"),
    ]
    offset = 128 + 4 + 12 * len(tags)
    table, body = struct.pack(">I", len(tags)), b""
    for signature, payload in tags:
        payload += b"\0" * (-len(payload) % 4)
        table += signature + struct.pack(">II", offset + len(body), len(payload))
        body += payload
    header = struct.pack(">I", offset + len(body)) + b"lcms" + bytes([2, 0x10, 0, 0])
    header += b"mntrRGB XYZ " + struct.pack(">6H", 2026, 11, 9, 0, 0, 0) + b"acspAPPL"
    header += b"\0" * 24 + white + b"lcms" + b"\0" * 44
    return header + table + body


def test_16bit_grey_profile_keeps_16bit_precision_and_profile(tmp_path):
    levels = np.array([[0, 1000, 1001, 32768, 65535]], dtype=np.uint16)
    grey_icc = _grey_profile(1.0)
    path = tmp_path / "linear_grey16.png"
    path.write_bytes(_encode_png(levels, color_type=0, bit_depth=16, icc_profile=grey_icc))
    data = ImageManager().load(str(path), bit_depth=16)
    assert data["has_invalid_icc"] is False
    assert data["icc_profile"] == grey_icc
    scaled = data["tensor"][0, 0].mul(65535.0).round().numpy().astype(np.int32)
    assert scaled.tolist() == [0, 1000, 1001, 32768, 65535]


def test_16bit_wide_gamut_icc_stays_unconverted_and_invalid_icc_warns(tmp_path):
    wide_icc = _wide_rgb_profile(1.0)
    src_arr = np.full((6, 8, 3), 20000, dtype=np.uint16)
    src_arr[0, 0] = (1000, 20000, 50000)
    src_png = tmp_path / "wide.png"
    src_png.write_bytes(_encode_png(src_arr, color_type=2, bit_depth=16, icc_profile=wide_icc))

    # 16-bit keeps pixels in the source colour space and preserves the source ICC profile unchanged.
    data16 = ImageManager().load(str(src_png), bit_depth=16)
    assert data16["has_invalid_icc"] is False
    assert data16["icc_profile"] == wide_icc
    loaded_u16 = data16["tensor"].permute(1, 2, 0).mul(65535.0).round().numpy().astype(np.uint16)
    assert np.array_equal(loaded_u16, src_arr)

    # 8-bit still converts to sRGB.
    data8 = ImageManager().load(str(src_png), bit_depth=8)
    assert data8["has_invalid_icc"] is False
    assert data8["icc_profile"] == _srgb_bytes()
    assert int(round(float(data8["tensor"][0, 1, 0]) * 255)) > 120

    # Round-trip embeds the original wide-gamut ICC unchanged in both PNG and TIFF.
    out_png = tmp_path / "wide_out.png"
    out_tif = tmp_path / "wide_out.tif"
    _run_identity_job(tmp_path, src_png, out_png, "png", 16)
    _run_identity_job(tmp_path, src_png, out_tif, "tif", 16)
    with Image.open(out_png) as pil_png, Image.open(out_tif) as pil_tif:
        assert pil_png.info.get("icc_profile") == wide_icc
        assert pil_tif.info.get("icc_profile") == wide_icc

    # Invalid and mismatched ICC profiles warn identically to 8-bit and are not embedded.
    bad_png = tmp_path / "bad_icc.png"
    bad_png.write_bytes(_encode_png(src_arr, color_type=2, bit_depth=16, icc_profile=b"not-icc"))
    with pytest.warns(RuntimeWarning, match="Invalid ICC profile"):
        bad_data = ImageManager().load(str(bad_png), bit_depth=16)
    assert bad_data["has_invalid_icc"] is True
    assert bad_data["icc_profile"] is None

    bad_out = tmp_path / "bad_out.png"
    with pytest.warns(RuntimeWarning, match="Invalid ICC profile"):
        _run_identity_job(tmp_path, bad_png, bad_out, "png", 16)
    with Image.open(bad_out) as pil_bad:
        assert "icc_profile" not in pil_bad.info

    grey_bad = tmp_path / "grey_srgb_mismatch.png"
    grey_bad.write_bytes(
        _encode_png(
            np.full((4, 4), 10000, dtype=np.uint16),
            color_type=0,
            bit_depth=16,
            icc_profile=_srgb_bytes(),
        )
    )
    with pytest.warns(RuntimeWarning, match="Invalid ICC profile"):
        mismatch_data = ImageManager().load(str(grey_bad), bit_depth=16)
    assert mismatch_data["has_invalid_icc"] is True
    assert mismatch_data["icc_profile"] is None


def test_16bit_raw_develops_at_output_bps_16_and_round_trips(tmp_path, monkeypatch):
    src_path = tmp_path / "sample.dng"
    Image.new("RGB", (12, 8)).save(src_path, format="TIFF")
    raw_rgb16 = _sample_rgb16(8, 12)
    captured_kwargs = {}

    class FakeRaw:
        sizes = SimpleNamespace(width=12, height=8, raw_width=12, raw_height=8)

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def postprocess(self, **kwargs):
            captured_kwargs.update(kwargs)
            return (
                raw_rgb16 if kwargs.get("output_bps") == 16 else (raw_rgb16 >> 8).astype(np.uint8)
            )

    monkeypatch.setitem(
        sys.modules,
        "rawpy",
        SimpleNamespace(imread=lambda _p: FakeRaw(), ColorSpace=SimpleNamespace(sRGB=object())),
    )
    out_path = tmp_path / "raw_out.tif"
    _run_identity_job(tmp_path, src_path, out_path, "tif", 16)
    assert captured_kwargs["output_bps"] == 16
    decoded = tifffile.imread(out_path)
    assert decoded.dtype == np.uint16
    assert np.abs(decoded.astype(np.int32) - raw_rgb16.astype(np.int32)).max() <= 1


def test_8bit_source_with_output_bit_depth_16_produces_16bit_container(tmp_path):
    src_path = tmp_path / "src8.png"
    arr8 = np.array([[[10, 128, 250], [0, 255, 64]]], dtype=np.uint8)
    Image.fromarray(arr8, mode="RGB").save(src_path)
    out_path = tmp_path / "out16.png"
    _run_identity_job(tmp_path, src_path, out_path, "png", 16)

    w, h, depth, colour, decoded, _ = _decode_png_16(out_path.read_bytes())
    assert (w, h, depth, colour) == (2, 1, 16, 2)
    assert np.array_equal(decoded, arr8.astype(np.uint16) * 257)


def test_default_8bit_output_is_byte_identical_across_formats(tmp_path):
    rng = np.random.default_rng(88)
    arr8 = rng.integers(0, 256, (16, 24, 3), dtype=np.uint8)
    src_path = tmp_path / "in8.png"
    Image.fromarray(arr8, mode="RGB").save(src_path)

    for fmt in ("png", "tif", "jpg"):
        out_omitted = tmp_path / f"omitted.{fmt}"
        out_explicit = tmp_path / f"explicit8.{fmt}"

        # Baseline using direct 8-bit ImageManager path without bit_depth arg.
        engine, adapter, _ = _identity_engine_and_adapter()
        scratch = tmp_path / f"scratch_{fmt}"
        scratch.mkdir()
        runner = ImageJobRunner(
            engine=engine,
            model_adapter=adapter,
            cancel_event=threading.Event(),
            emit=lambda _m: None,
            emit_preview=lambda _p: None,
            preview_factory=lambda *a, **k: SimpleNamespace(
                close=lambda: None, submit=lambda **_: False
            ),
        )
        base_data = {
            "job_id": f"j-{fmt}",
            "image_path": str(src_path),
            "model_path": "identity.pth",
            "output_path": str(out_omitted),
            "scratch_directory": str(scratch),
            "output_temporary_directory": str(scratch),
            "output_format": fmt,
            "device": "cpu",
            "tile_size": 64,
            "halo": 8,
            "precision": "fp32",
            "jpeg_quality": 95,
            "preserve_metadata": False,
            "safe_memory": False,
            "preview_enabled": False,
            "output_scale": 1,
        }
        runner.run(f"j-{fmt}-omitted", base_data)
        runner.run(
            f"j-{fmt}-8",
            {**base_data, "output_path": str(out_explicit), "output_bit_depth": 8},
        )
        assert out_omitted.read_bytes() == out_explicit.read_bytes(), fmt


@pytest.mark.parametrize("fmt", ["jpg", "webp", "heic"])
def test_16bit_is_refused_for_unsupported_formats_before_job_starts(tmp_path, fmt):
    request = {
        "job_id": "bad-depth",
        "image_path": str(tmp_path / "in.png"),
        "model_path": "m.pth",
        "output_path": str(tmp_path / f"out.{fmt}"),
        "output_format": fmt,
        "output_bit_depth": 16,
        "device": "cpu",
        "tile_size": 256,
        "halo": 16,
        "precision": "fp32",
        "jpeg_quality": 98,
        "preserve_metadata": True,
        "safe_memory": False,
    }
    with pytest.raises(InvalidRequest, match="16-bit"):
        validate_image_job(request)

    engine = MagicMock()
    runner = ImageJobRunner(
        engine=engine,
        model_adapter=MagicMock(),
        cancel_event=threading.Event(),
        emit=MagicMock(),
        emit_preview=MagicMock(),
    )
    with pytest.raises(ValueError, match="16-bit"):
        runner.run("bad-depth", request)
    engine.release_models_except.assert_not_called()


@pytest.mark.parametrize("bad_depth", [0, 10, 12, 32, "16", True, None])
def test_schema_validation_rejects_invalid_output_bit_depth(tmp_path, bad_depth):
    request = {
        "job_id": "bad-depth-val",
        "image_path": str(tmp_path / "in.png"),
        "model_path": "m.pth",
        "output_path": str(tmp_path / "out.png"),
        "output_format": "png",
        "output_bit_depth": bad_depth,
        "device": "cpu",
        "tile_size": 256,
        "halo": 16,
        "precision": "fp32",
        "jpeg_quality": 98,
        "preserve_metadata": True,
        "safe_memory": False,
    }
    with pytest.raises(InvalidRequest, match="output_bit_depth"):
        validate_image_job(request)


def test_engine_features_advertise_image_16bit():
    assert "image_16bit" in _engine_features()


def test_16bit_memory_and_disk_estimates_count_two_bytes_per_sample():
    need8 = disk_space.image_need(1000, 500, 4, 4, "png", alpha=False, bit_depth=8)
    need16 = disk_space.image_need(1000, 500, 4, 4, "png", alpha=False, bit_depth=16)
    assert need16.scratch == 2 * need8.scratch
    assert need16.encoded > need8.encoded

    assert image_batch.save_bytes(1000, 500, 4, 4, alpha=True, bit_depth=16) == 2 * (
        image_batch.save_bytes(1000, 500, 4, 4, alpha=True, bit_depth=8)
    )


def test_output_writer_and_live_preview_support_uint16(tmp_path):
    with OutputWriter((3, 8, 8), dtype=np.uint16, temporary_directory=tmp_path) as writer:
        assert writer.get_array().dtype == np.uint16
        tile = np.full((3, 8, 8), 32768, dtype=np.uint16)
        writer.write_tile(tile, 0, 0)
        assert int(writer.get_array()[0, 0, 0]) == 32768

    # Live preview must scale uint16 down to 8-bit rather than clipping >255 to white.
    dark16 = np.full((3, 64, 64), 1000, dtype=np.uint16)
    b64 = encode_preview_jpeg(dark16, 64, 80)
    import base64

    preview_im = Image.open(io.BytesIO(base64.b64decode(b64)))
    assert np.asarray(preview_im).mean() < 20


def test_16bit_rgba_tiff_and_output_scale_downsample_preserve_16bit_alpha(tmp_path):
    rng = np.random.default_rng(75)
    src_arr = rng.integers(0, 65536, (16, 20, 4), dtype=np.uint16)
    src_arr[0, 0, 3] = 1000
    src_arr[0, 1, 3] = 1001
    src_path = tmp_path / "in_rgba.tif"
    tifffile.imwrite(
        src_path,
        src_arr,
        photometric="rgb",
        extrasamples=["unassalpha"],
        iccprofile=_srgb_bytes(),
        extratags=[(315, "s", 0, "Alpha Artist", False)],
    )
    out_path = tmp_path / "out_rgba.tif"
    _run_identity_job(tmp_path, src_path, out_path, "tif", 16)

    with tifffile.TiffFile(out_path) as tf:
        page = tf.pages[0]
        assert page.bitspersample == 16
        assert page.samplesperpixel == 4
        decoded = page.asarray()
        assert np.abs(decoded.astype(np.int32) - src_arr.astype(np.int32)).max() <= 1
        assert page.tags[315].value == "Alpha Artist"

    # Also test preserve_metadata=False strips EXIF and ICC on both 16-bit PNG and TIFF.
    manager = ImageManager()
    manager.alpha_channel = Image.fromarray(np.full((8, 10), 40000, dtype=np.uint16))
    native_2x = np.full((3, 16, 20), 50000, dtype=np.uint16)
    scaled_png = tmp_path / "scaled_nometa.png"
    manager.save_from_writer(
        native_2x,
        str(scaled_png),
        "png",
        98,
        False,
        _srgb_bytes(),
        {315: "Hidden"},
        2,
        output_scale=1,
        output_bit_depth=16,
    )
    w, h, depth, colour, decoded_png, extra = _decode_png_16(scaled_png.read_bytes())
    assert (w, h, depth, colour) == (10, 8, 16, 6)
    assert int(decoded_png[0, 0, 0]) == 50000
    assert int(decoded_png[0, 0, 3]) == 40000
    assert b"iCCP" not in extra and b"eXIf" not in extra

    scaled_tif = tmp_path / "scaled_nometa.tif"
    manager.save_from_writer(
        native_2x,
        str(scaled_tif),
        "tif",
        98,
        False,
        _srgb_bytes(),
        {315: "Hidden"},
        2,
        output_scale=1,
        output_bit_depth=16,
    )
    with tifffile.TiffFile(scaled_tif) as tf:
        p = tf.pages[0]
        assert p.shape == (8, 10, 4)
        assert 34675 not in p.tags
        assert 315 not in p.tags
        assert 274 not in p.tags


def test_16bit_downscale_below_native_scale_with_alpha_matches_8bit_within_tolerance(tmp_path):
    # Model native scale=4 producing 24x32 RGB; source alpha is 1x (6x8); output_scale=2 -> 12x16.
    y_grad = np.linspace(0, 65535, 24, dtype=np.float64)[:, None]
    x_grad = np.linspace(0, 65535, 32, dtype=np.float64)[None, :]
    r16 = np.rint(0.6 * x_grad + 0.4 * y_grad).clip(0, 65535).astype(np.uint16)
    g16 = np.rint(0.3 * x_grad + 0.7 * (65535 - y_grad)).clip(0, 65535).astype(np.uint16)
    b16 = np.rint(0.5 * (65535 - x_grad) + 0.5 * y_grad).clip(0, 65535).astype(np.uint16)
    planes16 = np.stack((r16, g16, b16), axis=0)

    alpha16_src = np.linspace(1000, 64000, 6 * 8, dtype=np.uint16).reshape(6, 8)
    alpha8_src = np.clip(np.rint(alpha16_src / 257.0), 0, 255).astype(np.uint8)
    planes8 = np.clip(np.rint(planes16 / 257.0), 0, 255).astype(np.uint8)

    mgr16 = ImageManager()
    mgr16.alpha_channel = Image.fromarray(alpha16_src)
    out16_path = tmp_path / "downscaled_16.png"
    mgr16.save_from_writer(
        planes16,
        str(out16_path),
        "png",
        98,
        False,
        None,
        {},
        4,
        output_scale=2,
        output_bit_depth=16,
    )

    mgr8 = ImageManager()
    mgr8.alpha_channel = Image.fromarray(alpha8_src, mode="L")
    out8_path = tmp_path / "downscaled_8.png"
    mgr8.save_from_writer(
        planes8, str(out8_path), "png", 98, False, None, {}, 4, output_scale=2, output_bit_depth=8
    )

    w, h, depth, colour, decoded16, _ = _decode_png_16(out16_path.read_bytes())
    assert (w, h, depth, colour) == (16, 12, 16, 6)
    assert decoded16.dtype == np.uint16
    assert int(decoded16[..., :3].min()) >= 0 and int(decoded16[..., :3].max()) <= 65535
    assert int(decoded16[..., 3].min()) >= 0 and int(decoded16[..., 3].max()) <= 65535
    # Non-trivial 16-bit alpha smoothness check (not quantized to 8-bit steps).
    assert len(np.unique(decoded16[..., 3])) > len(np.unique(alpha8_src))

    with Image.open(out8_path) as im8:
        arr8_scaled = np.asarray(im8, dtype=np.int32) * 257
    assert np.abs(decoded16.astype(np.int32) - arr8_scaled).max() <= 514


def test_read_png_16_handles_filters_colour_types_adam7_and_non_ascii_paths(tmp_path):
    from localsr.core.image_io_16bit import read_png_16

    def paeth(a: int, b: int, c: int) -> int:
        p = a + b - c
        pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
        if pa <= pb and pa <= pc:
            return a
        return b if pb <= pc else c

    arr = np.array(
        [
            [[1000, 2000, 3000], [1100, 2200, 3300], [1250, 2400, 3600]],
            [[1500, 2500, 3500], [1650, 2700, 3800], [1800, 2900, 4100]],
            [[2000, 3000, 4000], [2100, 3200, 4300], [2200, 3400, 4600]],
            [[2500, 3500, 4500], [2600, 3700, 4800], [2700, 3900, 5100]],
            [[3000, 4000, 5000], [3100, 4200, 5300], [3200, 4400, 5600]],
        ],
        dtype=np.uint16,
    )
    h, w, c = arr.shape
    bpp = c * 2
    raw_rows = np.ascontiguousarray(arr, dtype=">u2").view(np.uint8).reshape(h, w * bpp)
    encoded_rows = bytearray()
    for y in range(h):
        ftype = y % 5
        cur = raw_rows[y]
        prev = raw_rows[y - 1] if y > 0 else np.zeros(w * bpp, dtype=np.uint8)
        encoded_rows.append(ftype)
        for i in range(w * bpp):
            a = int(cur[i - bpp]) if i >= bpp else 0
            b = int(prev[i])
            c_val = int(prev[i - bpp]) if i >= bpp else 0
            if ftype == 0:
                pred = 0
            elif ftype == 1:
                pred = a
            elif ftype == 2:
                pred = b
            elif ftype == 3:
                pred = (a + b) >> 1
            else:
                pred = paeth(a, b, c_val)
            encoded_rows.append((int(cur[i]) - pred) & 0xFF)

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    png_bytes = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 16, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(encoded_rows)))
        + chunk(b"IEND", b"")
    )
    path = tmp_path / "all_filters_café_16.png"
    path.write_bytes(png_bytes)
    decoded, colour = read_png_16(path)
    assert colour == 2
    assert np.array_equal(decoded, arr)

    # Colour type 4 (grey+alpha) both non-interlaced and Adam7 interlaced.
    rng = np.random.default_rng(79)
    ga = rng.integers(0, 65536, (11, 13, 2), dtype=np.uint16)
    for interlace in (0, 1):
        ga_path = tmp_path / f"ga_{interlace}.png"
        ga_path.write_bytes(_encode_png(ga, color_type=4, bit_depth=16, interlace=interlace))
        dec_ga, ct_ga = read_png_16(ga_path)
        assert ct_ga == 4
        assert np.array_equal(dec_ga, ga)

    # 8-bit PNG returns None (handled by Pillow).
    p8 = tmp_path / "eight.png"
    Image.new("RGB", (4, 4), (10, 20, 30)).save(p8)
    assert read_png_16(p8) is None
