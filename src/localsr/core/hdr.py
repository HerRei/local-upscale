"""Explicit HLG/PQ BT.2020 to SDR BT.709 conversion for SDR-trained models.

The BT.2100 transfer functions keep floating-point precision until the final
SDR quantization. A fixed extended-Reinhard curve avoids frame-dependent
exposure changes. This is SDR conversion, not HDR or Dolby Vision mastering.
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
