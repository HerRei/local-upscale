# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Hermes Reisner. Part of the MLX port of FlashVSR (https://github.com/OpenImagingLab/FlashVSR).

from typing import Tuple, Union

import mlx.core as mx
import numpy as np


def sinusoidal_embedding_1d(dim: int, position: mx.array) -> mx.array:
    half = dim // 2
    inv_freq = np.power(10000.0, -np.arange(half, dtype=np.float64) / half)
    pos_np = np.asarray(position.astype(mx.float32), dtype=np.float64)
    sinusoid = np.outer(pos_np, inv_freq)
    emb = np.concatenate([np.cos(sinusoid), np.sin(sinusoid)], axis=1)
    return mx.array(emb).astype(position.dtype)


def precompute_freqs_cis(dim: int, end: int = 1024, theta: float = 10000.0) -> mx.array:
    """Precompute rotary cosine/sine pairs with float64 precision, returned as (end, dim // 2, 2) float32."""
    half = dim // 2
    exponents = np.arange(0, dim, 2, dtype=np.float64)[:half] / dim
    freqs = 1.0 / (theta**exponents)
    angles = np.outer(np.arange(end, dtype=np.float64), freqs)
    cos = np.cos(angles)
    sin = np.sin(angles)
    freqs_cs = np.stack([cos, sin], axis=-1).astype(np.float32)
    return mx.array(freqs_cs, dtype=mx.float32)


def precompute_freqs_cis_3d(
    dim: int, end: int = 1024, theta: float = 10000.0
) -> Tuple[mx.array, mx.array, mx.array]:
    f_freqs_cis = precompute_freqs_cis(dim - 2 * (dim // 3), end, theta)
    h_freqs_cis = precompute_freqs_cis(dim // 3, end, theta)
    w_freqs_cis = precompute_freqs_cis(dim // 3, end, theta)
    return f_freqs_cis, h_freqs_cis, w_freqs_cis


def build_freqs_3d(
    freqs_3d: Tuple[mx.array, mx.array, mx.array],
    f: int,
    h: int,
    w: int,
    f_start: int = 0,
) -> mx.array:
    """Assemble 3D RoPE cos/sin pairs of shape (f * h * w, 1, head_dim // 2, 2)."""
    f_freqs, h_freqs, w_freqs = freqs_3d
    f_slice = f_freqs[f_start : f_start + f].reshape(f, 1, 1, -1, 2)
    h_slice = h_freqs[:h].reshape(1, h, 1, -1, 2)
    w_slice = w_freqs[:w].reshape(1, 1, w, -1, 2)

    f_part = mx.broadcast_to(f_slice, (f, h, w, f_slice.shape[3], 2))
    h_part = mx.broadcast_to(h_slice, (f, h, w, h_slice.shape[3], 2))
    w_part = mx.broadcast_to(w_slice, (f, h, w, w_slice.shape[3], 2))

    combined = mx.concatenate([f_part, h_part, w_part], axis=3)
    return combined.reshape(f * h * w, 1, -1, 2)


def rope_apply(
    x: mx.array,
    freqs: Union[mx.array, Tuple[mx.array, mx.array]],
    num_heads: int,
) -> mx.array:
    b, s, d_total = x.shape
    head_dim = d_total // num_heads
    x_f = x.astype(mx.float32).reshape(b, s, num_heads, head_dim // 2, 2)
    x_re = x_f[..., 0]
    x_im = x_f[..., 1]

    if isinstance(freqs, tuple):
        cos = freqs[0].astype(mx.float32)
        sin = freqs[1].astype(mx.float32)
    elif mx.issubdtype(freqs.dtype, mx.complexfloating):
        cos = mx.real(freqs).astype(mx.float32)
        sin = mx.imag(freqs).astype(mx.float32)
    else:
        freqs_f = freqs.astype(mx.float32)
        cos = freqs_f[..., 0]
        sin = freqs_f[..., 1]

    out_re = x_re * cos - x_im * sin
    out_im = x_re * sin + x_im * cos
    out = mx.stack([out_re, out_im], axis=-1).reshape(b, s, d_total)
    return out.astype(x.dtype)
