# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Hermes Reisner. Part of the MLX port of FlashVSR (https://github.com/OpenImagingLab/FlashVSR).

from typing import List, Optional, Tuple, Union

import mlx.core as mx
import mlx.nn as nn

CACHE_T = 2


class RMS_norm(nn.Module):
    """Channel-first or channel-last RMS normalization matching FlashVSR's Causal_LQ4x_Proj."""

    def __init__(
        self,
        dim: int,
        channel_first: bool = True,
        images: bool = True,
        bias: bool = False,
    ):
        super().__init__()
        broadcastable_dims = (1, 1, 1) if not images else (1, 1)
        shape = (dim, *broadcastable_dims) if channel_first else (dim,)

        self.channel_first = channel_first
        self.scale = dim**0.5
        self.gamma = mx.ones(shape)
        self._has_bias = bias
        if bias:
            self.bias = mx.zeros(shape)

    def __call__(self, x: mx.array) -> mx.array:
        axis = 1 if self.channel_first else -1
        x_f = x.astype(mx.float32)
        denom = mx.maximum(
            mx.sqrt(mx.sum(x_f * x_f, axis=axis, keepdims=True)),
            1e-12,
        )
        normed = (x_f / denom).astype(x.dtype)
        out = normed * self.scale * self.gamma.astype(x.dtype)
        if self._has_bias:
            out = out + self.bias.astype(x.dtype)
        return out


class CausalConv3d(nn.Conv3d):
    """Causal 3D convolution operating on (B, C, T, H, W) tensors."""

    def __init__(
        self,
        in_channels: int,
        out_channels: int,
        kernel_size: Union[int, Tuple[int, int, int]],
        stride: Union[int, Tuple[int, int, int]] = 1,
        padding: Union[int, Tuple[int, int, int]] = 0,
        dilation: Union[int, Tuple[int, int, int]] = 1,
        bias: bool = True,
    ):
        super().__init__(
            in_channels,
            out_channels,
            kernel_size,
            stride=stride,
            padding=padding,
            dilation=dilation,
            bias=bias,
        )
        # PyTorch F.pad order: (w_left, w_right, h_top, h_bottom, t_front, t_back)
        self._padding = (
            self.padding[2],
            self.padding[2],
            self.padding[1],
            self.padding[1],
            2 * self.padding[0],
            0,
        )
        self.padding = (0, 0, 0)

    def __call__(self, x: mx.array, cache_x: Optional[mx.array] = None) -> mx.array:
        pad_wl, pad_wr, pad_ht, pad_hb, pad_tf, pad_tb = self._padding
        if cache_x is not None and pad_tf > 0:
            x = mx.concatenate([cache_x.astype(x.dtype), x], axis=2)
            pad_tf -= cache_x.shape[2]
        if any(p > 0 for p in (pad_wl, pad_wr, pad_ht, pad_hb, pad_tf, pad_tb)):
            x = mx.pad(
                x,
                [(0, 0), (0, 0), (pad_tf, pad_tb), (pad_ht, pad_hb), (pad_wl, pad_wr)],
                mode="edge",
            )
        # MLX Conv3d expects (B, T, H, W, C)
        x_nhwc = x.transpose(0, 2, 3, 4, 1)
        y_nhwc = super().__call__(x_nhwc)
        return y_nhwc.transpose(0, 4, 1, 2, 3)


class PixelShuffle3d(nn.Module):
    def __init__(self, ff: int, hh: int, ww: int):
        super().__init__()
        self.ff = ff
        self.hh = hh
        self.ww = ww

    def __call__(self, x: mx.array) -> mx.array:
        b, c, f_tot, h_tot, w_tot = x.shape
        f = f_tot // self.ff
        h = h_tot // self.hh
        w = w_tot // self.ww
        x = x.reshape(b, c, f, self.ff, h, self.hh, w, self.ww)
        x = x.transpose(0, 1, 3, 5, 7, 2, 4, 6)
        return x.reshape(b, c * self.ff * self.hh * self.ww, f, h, w)


class Causal_LQ4x_Proj(nn.Module):
    """Streaming causal 4x spatial/temporal LQ feature projector for FlashVSR."""

    def __init__(self, in_dim: int, out_dim: int, layer_num: int = 30):
        super().__init__()
        self.ff = 1
        self.hh = 16
        self.ww = 16
        self.hidden_dim1 = 2048
        self.hidden_dim2 = 3072
        self.layer_num = layer_num

        self.pixel_shuffle = PixelShuffle3d(self.ff, self.hh, self.ww)

        self.conv1 = CausalConv3d(
            in_dim * self.ff * self.hh * self.ww,
            self.hidden_dim1,
            (4, 3, 3),
            stride=(2, 1, 1),
            padding=(1, 1, 1),
        )
        self.norm1 = RMS_norm(self.hidden_dim1, images=False)
        self.act1 = nn.SiLU()

        self.conv2 = CausalConv3d(
            self.hidden_dim1,
            self.hidden_dim2,
            (4, 3, 3),
            stride=(2, 1, 1),
            padding=(1, 1, 1),
        )
        self.norm2 = RMS_norm(self.hidden_dim2, images=False)
        self.act2 = nn.SiLU()

        self.linear_layers = [nn.Linear(self.hidden_dim2, out_dim) for _ in range(layer_num)]

        self.clip_idx = 0
        self.cache = {"conv1": None, "conv2": None}

    def clear_cache(self):
        self.cache = {"conv1": None, "conv2": None}
        self.clip_idx = 0

    def __call__(self, video: mx.array) -> List[mx.array]:
        self.clear_cache()
        t = video.shape[2]
        iter_ = 1 + (t - 1) // 4
        first_frame = mx.repeat(video[:, :, :1, :, :], 3, axis=2)
        video = mx.concatenate([first_frame, video], axis=2)

        out_x = []
        for i in range(iter_):
            x = self.pixel_shuffle(video[:, :, i * 4 : (i + 1) * 4, :, :])
            cache1_x = x[:, :, -CACHE_T:, :, :]
            x = self.conv1(x, self.cache["conv1"])
            self.cache["conv1"] = cache1_x
            x = self.norm1(x)
            x = self.act1(x)
            cache2_x = x[:, :, -CACHE_T:, :, :]
            if i == 0:
                self.cache["conv2"] = cache2_x
                continue
            x = self.conv2(x, self.cache["conv2"])
            self.cache["conv2"] = cache2_x
            x = self.norm2(x)
            x = self.act2(x)
            out_x.append(x)

        out_cat = mx.concatenate(out_x, axis=2)
        b, c, f, h, w = out_cat.shape
        out_tokens = out_cat.transpose(0, 2, 3, 4, 1).reshape(b, f * h * w, c)
        return [layer(out_tokens) for layer in self.linear_layers]

    def stream_forward(self, video_clip: mx.array) -> Optional[List[mx.array]]:
        if self.clip_idx == 0:
            first_frame = mx.repeat(video_clip[:, :, :1, :, :], 3, axis=2)
            video_clip = mx.concatenate([first_frame, video_clip], axis=2)
            x = self.pixel_shuffle(video_clip)
            cache1_x = x[:, :, -CACHE_T:, :, :]
            x = self.conv1(x, self.cache["conv1"])
            self.cache["conv1"] = cache1_x
            x = self.norm1(x)
            x = self.act1(x)
            cache2_x = x[:, :, -CACHE_T:, :, :]
            self.cache["conv2"] = cache2_x
            self.clip_idx += 1
            return None

        x = self.pixel_shuffle(video_clip)
        cache1_x = x[:, :, -CACHE_T:, :, :]
        x = self.conv1(x, self.cache["conv1"])
        self.cache["conv1"] = cache1_x
        x = self.norm1(x)
        x = self.act1(x)
        cache2_x = x[:, :, -CACHE_T:, :, :]
        x = self.conv2(x, self.cache["conv2"])
        self.cache["conv2"] = cache2_x
        x = self.norm2(x)
        x = self.act2(x)
        b, c, f, h, w = x.shape
        out_tokens = x.transpose(0, 2, 3, 4, 1).reshape(b, f * h * w, c)
        self.clip_idx += 1
        return [layer(out_tokens) for layer in self.linear_layers]
