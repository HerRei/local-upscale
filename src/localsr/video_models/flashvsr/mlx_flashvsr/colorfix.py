# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Hermes Reisner. Part of the MLX port of FlashVSR (https://github.com/OpenImagingLab/FlashVSR).

from typing import Literal, Optional, Tuple

import mlx.core as mx
import mlx.nn as nn


def _calc_mean_std(feat: mx.array, eps: float = 1e-5) -> Tuple[mx.array, mx.array]:
    assert feat.ndim == 4, "feat must be (N, C, H, W)"
    n, c = feat.shape[:2]
    flat = feat.astype(mx.float32).reshape(n, c, -1)
    var = mx.var(flat, axis=2, ddof=0) + eps
    std = mx.sqrt(var).reshape(n, c, 1, 1)
    mean = mx.mean(flat, axis=2).reshape(n, c, 1, 1)
    return mean, std


def adain(content_feat: mx.array, style_feat: mx.array) -> mx.array:
    assert content_feat.shape[:2] == style_feat.shape[:2], "AdaIN: N and C must match"
    style_mean, style_std = _calc_mean_std(style_feat)
    content_mean, content_std = _calc_mean_std(content_feat)
    normalized = (content_feat.astype(mx.float32) - content_mean) / content_std
    return (normalized * style_std + style_mean).astype(content_feat.dtype)


_adain = adain

_GAUSSIAN_3X3 = (
    (0.0625, 0.125, 0.0625),
    (0.125, 0.25, 0.125),
    (0.0625, 0.125, 0.0625),
)


def _wavelet_blur(x: mx.array, radius: int) -> mx.array:
    assert x.ndim == 4, "x must be (N, C, H, W)"
    c = x.shape[1]
    base = mx.array(_GAUSSIAN_3X3, dtype=x.dtype).reshape(1, 3, 3, 1)
    weight = mx.broadcast_to(base, (c, 3, 3, 1))
    x_nhwc = x.transpose(0, 2, 3, 1)
    x_pad = mx.pad(
        x_nhwc,
        [(0, 0), (radius, radius), (radius, radius), (0, 0)],
        mode="edge",
    )
    out_nhwc = mx.conv2d(x_pad, weight, stride=1, padding=0, dilation=radius, groups=c)
    return out_nhwc.transpose(0, 3, 1, 2)


def _wavelet_decompose(x: mx.array, levels: int = 5) -> Tuple[mx.array, mx.array]:
    assert x.ndim == 4, "x must be (N, C, H, W)"
    high = mx.zeros_like(x)
    low = x
    for i in range(levels):
        radius = 2**i
        blurred = _wavelet_blur(low, radius)
        high = high + (low - blurred)
        low = blurred
    return high, low


def _wavelet_reconstruct(content: mx.array, style: mx.array, levels: int = 5) -> mx.array:
    c_high, _ = _wavelet_decompose(content, levels=levels)
    _, s_low = _wavelet_decompose(style, levels=levels)
    return c_high + s_low


class ColorCorrectorWavelet(nn.Module):
    """Stateless video color corrector supporting wavelet and AdaIN modes."""

    def __init__(self, levels: int = 5):
        super().__init__()
        self.levels = levels

    @staticmethod
    def _flatten_time(x: mx.array) -> Tuple[mx.array, int, int]:
        assert x.ndim == 5, "input must be (B, C, f, H, W)"
        b, c, f, h, w = x.shape
        y = x.transpose(0, 2, 1, 3, 4).reshape(b * f, c, h, w)
        return y, b, f

    @staticmethod
    def _unflatten_time(y: mx.array, b: int, f: int) -> mx.array:
        bf, c, h, w = y.shape
        assert bf == b * f
        return y.reshape(b, f, c, h, w).transpose(0, 2, 1, 3, 4)

    def _apply_4d(
        self, hq4: mx.array, lq4: mx.array, method: Literal["wavelet", "adain"]
    ) -> mx.array:
        if method == "wavelet":
            return _wavelet_reconstruct(hq4, lq4, levels=self.levels)
        if method == "adain":
            return adain(hq4, lq4)
        raise ValueError(f"Unknown color correction method: {method!r}")

    def __call__(
        self,
        hq_image: mx.array,
        lq_image: mx.array,
        clip_range: Tuple[float, float] = (-1.0, 1.0),
        method: Literal["wavelet", "adain"] = "wavelet",
        chunk_size: Optional[int] = None,
    ) -> mx.array:
        assert hq_image.shape == lq_image.shape, "HQ and LQ shapes must match"
        assert hq_image.ndim == 5 and hq_image.shape[1] == 3, "input must be (B, 3, f, H, W)"

        f = hq_image.shape[2]
        if lq_image.dtype != hq_image.dtype:
            lq_image = lq_image.astype(hq_image.dtype)

        if chunk_size is None or chunk_size >= f:
            hq4, b, f_cur = self._flatten_time(hq_image)
            lq4, _, _ = self._flatten_time(lq_image)
            out4 = self._apply_4d(hq4, lq4, method)
            out4 = mx.clip(out4, clip_range[0], clip_range[1])
            return self._unflatten_time(out4, b, f_cur)

        outs = []
        for start in range(0, f, chunk_size):
            end = min(start + chunk_size, f)
            hq4, b_, f_ = self._flatten_time(hq_image[:, :, start:end])
            lq4, _, _ = self._flatten_time(lq_image[:, :, start:end])
            out4 = self._apply_4d(hq4, lq4, method)
            out4 = mx.clip(out4, clip_range[0], clip_range[1])
            out_chunk = self._unflatten_time(out4, b_, f_)
            mx.eval(out_chunk)
            outs.append(out_chunk)
        return mx.concatenate(outs, axis=2)


TorchColorCorrectorWavelet = ColorCorrectorWavelet
