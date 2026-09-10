"""BT.2100 transfer functions, SDR previews and experimental HDR detail adaptation.

All HDR processing uses float32 BT.2020. Only the explicitly selected SDR
conversion maps highlights/gamut and quantizes RGB to eight bits.
"""

from __future__ import annotations

import numpy as np

BT2020_TO_BT709 = np.array(
    [
        [1.660491, -0.587641, -0.072850],
        [-0.124550, 1.132900, -0.008350],
        [-0.018151, -0.100579, 1.118730],
    ],
    dtype=np.float32,
)
BT2020_LUMA = np.array([0.2627, 0.6780, 0.0593], dtype=np.float32)
BT709_LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)


def hdr_to_working_linear(rgb: np.ndarray, transfer: int) -> np.ndarray:
    """BT.2020 scene light for HLG, display light / 10000 nits for PQ."""
    signal = np.clip(np.asarray(rgb, dtype=np.float32), 0, 1)
    if transfer == 18:
        a = 0.17883277
        b = 1 - 4 * a
        c = 0.5 - a * np.log(4 * a)
        return np.where(signal <= 0.5, signal**2 / 3, (np.exp((signal - c) / a) + b) / 12)
    if transfer == 16:
        return hdr_to_linear_nits(signal, transfer) / 10000
    raise ValueError("HDR preservation supports BT.2020 HLG/PQ only.")


def working_linear_to_hdr(linear: np.ndarray, transfer: int) -> np.ndarray:
    """Inverse of hdr_to_working_linear; no SDR tone mapping or quantization."""
    light = np.clip(np.asarray(linear, dtype=np.float32), 0, 1)
    if transfer == 18:
        a = 0.17883277
        b = 1 - 4 * a
        c = 0.5 - a * np.log(4 * a)
        return np.where(
            light <= 1 / 12, np.sqrt(3 * light), a * np.log(np.maximum(12 * light - b, 1e-8)) + c
        ).astype(np.float32)
    if transfer == 16:
        m1, m2 = 2610 / 16384, 2523 / 32
        c1, c2, c3 = 3424 / 4096, 2413 / 128, 2392 / 128
        power = light**m1
        return ((c1 + c2 * power) / (1 + c3 * power)) ** m2
    raise ValueError("HDR preservation supports BT.2020 HLG/PQ only.")


def anchor_hdr_detail(source: np.ndarray, enhanced: np.ndarray, transfer: int) -> np.ndarray:
    """Conservative adaptation of SDR-trained model detail to the source HDR.

    Every expanded source pixel retains its mean linear BT.2020 RGB. Remove
    the model's block-average exposure/colour change, then limit the remaining
    detail uniformly across channels to fit the HDR signal range. This does
    not validate perceptual HDR quality; block-boundary artefacts remain a
    specific Labs acceptance concern. HLG is anchored in scene light, PQ in
    display light. Only floating-point HDR samples enter or leave this path.
    """
    if source.dtype != np.float32 or enhanced.dtype != np.float32:
        raise ValueError("HDR detail adaptation requires float32 RGB.")
    if not np.isfinite(source).all() or not np.isfinite(enhanced).all():
        raise ValueError("HDR model output contains non-finite samples.")
    h, w, channels = source.shape
    scale = enhanced.shape[0] // h
    if channels != 3 or scale < 1 or enhanced.shape != (h * scale, w * scale, 3):
        raise ValueError("HDR detail adaptation requires an integer RGB upscale.")
    # Work in strips so 4K -> 16K does not allocate several whole HDR frames.
    result = np.empty_like(enhanced)
    for y in range(0, h, 16):
        end = min(y + 16, h)
        target = hdr_to_working_linear(source[y:end], transfer)[:, None, :, None, :]
        blocks = hdr_to_working_linear(enhanced[y * scale : end * scale], transfer).reshape(
            end - y, scale, w, scale, 3
        )
        detail = blocks - blocks.mean(axis=(1, 3), keepdims=True)
        low = detail.min(axis=(1, 3), keepdims=True)
        high = detail.max(axis=(1, 3), keepdims=True)
        lower_limit = np.where(low < 0, target / np.maximum(-low, 1e-20), 1)
        upper_limit = np.where(high > 0, (1 - target) / np.maximum(high, 1e-20), 1)
        strength = np.minimum(1, np.minimum(lower_limit, upper_limit))
        strength = strength.min(axis=-1, keepdims=True)
        adapted = target + detail * strength
        result[y * scale : end * scale] = working_linear_to_hdr(
            adapted.reshape((end - y) * scale, w * scale, 3), transfer
        )
    return result


def hdr_to_linear_nits(rgb: np.ndarray, transfer: int) -> np.ndarray:
    """Decode BT.2100 HLG (1000-nit reference display) or ST 2084 PQ."""
    signal = np.clip(np.asarray(rgb, dtype=np.float32), 0, 1)
    if transfer == 18:
        a = 0.17883277
        b = 1 - 4 * a
        c = 0.5 - a * np.log(4 * a)
        scene = np.where(
            signal <= 0.5,
            signal**2 / 3,
            (np.exp((signal - c) / a) + b) / 12,
        )
        # Reference HLG OOTF: luminance-dependent system gamma 1.2. Applying
        # gamma independently to R/G/B would shift the source's hues.
        luminance = scene @ BT2020_LUMA
        return scene * np.power(luminance, 0.2)[..., None] * 1000
    if transfer == 16:
        m1, m2 = 2610 / 16384, 2523 / 32
        c1, c2, c3 = 3424 / 4096, 2413 / 128, 2392 / 128
        power = np.power(signal, 1 / m2)
        return 10000 * np.power(
            np.maximum(power - c1, 0) / np.maximum(c2 - c3 * power, 1e-7), 1 / m1
        )
    raise ValueError("HDR conversion supports HLG and PQ transfer functions only.")


def tone_map_to_sdr(rgb: np.ndarray, transfer: int, primaries: int) -> np.ndarray:
    """Return 8-bit BT.709 RGB; never apply an HDR curve to SDR samples."""
    if primaries != 9:
        raise ValueError(
            "This HDR video's colour primaries are not supported. "
            "HDR import currently supports BT.2020 HLG/PQ; convert this file to SDR first."
        )
    linear = hdr_to_linear_nits(rgb, transfer) @ BT2020_TO_BT709.T
    # Bring out-of-gamut negative channels toward neutral luminance before
    # compressing highlights, rather than independently clipping each channel.
    luminance = np.maximum(linear @ BT709_LUMA, 0)
    minimum = linear.min(axis=-1)
    saturation = np.minimum(1, luminance / np.maximum(luminance - minimum, 1e-6))
    linear = luminance[..., None] + saturation[..., None] * (linear - luminance[..., None])
    np.maximum(linear, 0, out=linear)
    linear /= 100  # SDR reference white, cd/m²
    peak = 10 if transfer == 18 else 100  # fixed 1000/10000-nit input reference
    maximum = linear.max(axis=-1)
    # Extended Reinhard maps the reference peak to SDR white and preserves
    # channel ratios. The curve never adapts to a frame histogram or crop.
    scale = (1 + maximum / (peak * peak)) / (1 + maximum)
    linear *= scale[..., None]
    np.clip(linear, 0, 1, out=linear)
    encoded = np.where(linear < 0.018, 4.5 * linear, 1.099 * linear**0.45 - 0.099)
    return np.rint(np.clip(encoded, 0, 1) * 255).astype(np.uint8)
