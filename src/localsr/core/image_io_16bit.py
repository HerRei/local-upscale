"""16-bit PNG/TIFF codecs and EXIF orientation for ImageManager."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import tifffile

_PNG_SIG = b"\x89PNG\r\n\x1a\n"
_PNG_CHANNELS = {0: 1, 2: 3, 4: 2, 6: 4}
# 8-byte PNG signature + 4-byte length + 4-byte "IHDR" + 13-byte body + 4-byte CRC.
_IHDR_END = 33


def apply_exif_orientation(arr: np.ndarray, orientation: int) -> np.ndarray:
    """Apply EXIF orientation 1..8 to an ``(H, W)`` or ``(H, W, C)`` array."""
    if orientation == 2:
        return np.ascontiguousarray(arr[:, ::-1])
    if orientation == 3:
        return np.ascontiguousarray(arr[::-1, ::-1])
    if orientation == 4:
        return np.ascontiguousarray(arr[::-1, :])
    if orientation == 5:
        return np.ascontiguousarray(np.swapaxes(arr, 0, 1))
    if orientation == 6:
        return np.ascontiguousarray(np.rot90(arr, k=3))
    if orientation == 7:
        return np.ascontiguousarray(np.swapaxes(arr[::-1, ::-1], 0, 1))
    if orientation == 8:
        return np.ascontiguousarray(np.rot90(arr, k=1))
    return arr


def read_png_16(path: str | Path) -> tuple[np.ndarray, int] | None:
    """Decode a 16-bit PNG (grey, RGB, grey+alpha, or RGBA) without 8-bit truncation.

    Returns ``(pixels_uint16, colour_type)`` or ``None`` when the file is not a 16-bit PNG
    of one of those colour types (letting Pillow handle 8-bit and indexed PNGs).
    """
    with open(path, "rb") as file:
        header = file.read(29)
    if len(header) < 29 or header[:8] != _PNG_SIG:
        return None
    length, kind = struct.unpack(">I4s", header[8:16])
    if kind != b"IHDR" or length < 13:
        return None
    _, _, depth, colour, comp, filt, interlace = struct.unpack(">IIBBBBB", header[16:29])
    if (
        depth != 16
        or colour not in _PNG_CHANNELS
        or comp != 0
        or filt != 0
        or interlace not in (0, 1)
    ):
        return None

    # Use np.fromfile so non-ASCII Windows paths work without cv2.imread encoding issues.
    raw = np.fromfile(path, dtype=np.uint8)
    decoded = cv2.imdecode(raw, cv2.IMREAD_UNCHANGED)
    if decoded is None:
        raise OSError("OpenCV could not decode 16-bit PNG.")
    if decoded.dtype != np.uint16:
        raise OSError(f"Expected 16-bit PNG pixels, got {decoded.dtype}.")

    if colour == 0:
        if decoded.ndim == 3 and decoded.shape[2] == 1:
            decoded = decoded[:, :, 0]
        if decoded.ndim != 2:
            raise OSError(f"Unexpected shape for 16-bit grey PNG: {decoded.shape}")
        return np.ascontiguousarray(decoded), 0
    if colour == 2:
        if decoded.ndim != 3 or decoded.shape[2] != 3:
            raise OSError(f"Unexpected shape for 16-bit RGB PNG: {decoded.shape}")
        return np.ascontiguousarray(cv2.cvtColor(decoded, cv2.COLOR_BGR2RGB)), 2
    if colour == 4:
        # OpenCV expands PNG grey+alpha (colour type 4) to 4-channel BGRA [G, G, G, A].
        if decoded.ndim == 3 and decoded.shape[2] == 4:
            return np.ascontiguousarray(np.stack((decoded[:, :, 0], decoded[:, :, 3]), axis=-1)), 4
        if decoded.ndim == 3 and decoded.shape[2] == 2:
            return np.ascontiguousarray(decoded), 4
        raise OSError(f"Unexpected shape for 16-bit grey+alpha PNG: {decoded.shape}")
    if decoded.ndim != 3 or decoded.shape[2] != 4:
        raise OSError(f"Unexpected shape for 16-bit RGBA PNG: {decoded.shape}")
    return np.ascontiguousarray(cv2.cvtColor(decoded, cv2.COLOR_BGRA2RGBA)), 6


def _png_chunk(tag: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    )


def write_png_16(
    destination: Path,
    pixels: np.ndarray,
    *,
    icc_profile: bytes | None = None,
    exif_bytes: bytes | None = None,
) -> None:
    """Write a 16-bit PNG (grey, RGB, or RGBA) via OpenCV with optional iCCP and eXIf."""
    arr = np.asarray(pixels, dtype=np.uint16)
    if arr.ndim == 2:
        encode_input = np.ascontiguousarray(arr)
    elif arr.ndim == 3 and arr.shape[2] == 3:
        encode_input = cv2.cvtColor(np.ascontiguousarray(arr), cv2.COLOR_RGB2BGR)
    elif arr.ndim == 3 and arr.shape[2] == 4:
        encode_input = cv2.cvtColor(np.ascontiguousarray(arr), cv2.COLOR_RGBA2BGRA)
    else:
        raise ValueError(f"Unsupported 16-bit PNG array shape: {arr.shape}")

    ok, encoded = cv2.imencode(".png", encode_input)
    if not ok:
        raise OSError("OpenCV could not encode 16-bit PNG.")
    png_bytes = encoded.tobytes()

    extra = bytearray()
    if icc_profile:
        extra.extend(_png_chunk(b"iCCP", b"ICC Profile\x00\x00" + zlib.compress(icc_profile)))
    if exif_bytes:
        raw_exif = exif_bytes[6:] if exif_bytes.startswith(b"Exif\x00\x00") else exif_bytes
        if raw_exif:
            extra.extend(_png_chunk(b"eXIf", raw_exif))

    with open(destination, "wb") as file:
        if extra:
            file.write(png_bytes[:_IHDR_END])
            file.write(extra)
            file.write(png_bytes[_IHDR_END:])
        else:
            file.write(png_bytes)


def read_tiff_16(path: str | Path, sample_bits: int = 16) -> np.ndarray:
    """Read the first page of a high-bit-depth integer TIFF via tifffile as uint16."""
    with tifffile.TiffFile(path) as tf:
        arr = tf.pages[0].asarray()
    if arr.ndim == 3 and arr.shape[2] == 1:
        arr = arr[:, :, 0]
    if arr.ndim not in (2, 3) or (arr.ndim == 3 and arr.shape[2] not in (2, 3, 4)):
        raise OSError("Unsupported TIFF channel layout.")
    if np.issubdtype(arr.dtype, np.floating):
        raise OSError("Floating-point colour TIFF is not supported.")
    if arr.dtype != np.uint16 or sample_bits < 16:
        max_val = float((1 << max(1, min(16, sample_bits))) - 1)
        scaled = np.clip(np.rint(arr.astype(np.float64) * (65535.0 / max_val)), 0, 65535)
        return scaled.astype(np.uint16)
    return arr


def write_tiff_16(
    destination: Path,
    pixels: np.ndarray,
    *,
    icc_profile: bytes | None = None,
    exif_dict: dict[int, Any] | None = None,
) -> None:
    """Write a 16-bit RGB or RGBA deflated TIFF with optional ICC and allow-listed EXIF."""
    arr = np.ascontiguousarray(pixels, dtype=np.uint16)
    has_alpha = arr.ndim == 3 and arr.shape[2] == 4
    extratags: list[tuple[int, int | str, int, Any, bool]] = []
    if exif_dict:
        for tag, value in exif_dict.items():
            if tag == 274:
                extratags.append((274, "H", 1, int(value), False))
            elif tag in (40962, 40963):
                extratags.append((tag, "I", 1, int(value), False))
            elif tag in (306, 315, 33432):
                ascii_text = str(value).encode("ascii", "replace").decode("ascii")
                extratags.append((tag, "s", 0, ascii_text, False))
    tifffile.imwrite(
        destination,
        arr,
        photometric="rgb",
        extrasamples=(["unassalpha"] if has_alpha else None),
        compression="adobe_deflate",
        metadata=None,
        software=False,
        iccprofile=icc_profile or None,
        extratags=extratags or None,
    )
