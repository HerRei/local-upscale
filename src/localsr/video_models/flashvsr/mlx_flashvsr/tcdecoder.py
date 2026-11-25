# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Hermes Reisner. Part of the MLX port of FlashVSR (https://github.com/OpenImagingLab/FlashVSR).
# Port of examples/WanVSR/utils/TCDecoder.py, which is based on TAEHV by Ollin Boer Bohan (MIT, see LICENSES/TAEHV-MIT.txt).

from collections import namedtuple
from pathlib import Path
from typing import List, Optional, Sequence, Tuple, Union

import mlx.core as mx
import mlx.nn as nn

from .convert import load_checkpoint_state_dict, load_torch_state_dict, resolve_dtype

DecoderResult = namedtuple("DecoderResult", ("frame", "memory"))
TWorkItem = namedtuple("TWorkItem", ("input_tensor", "block_index"))


class _Conv2d(nn.Conv2d):
    """Conv2d that avoids MLX Metal bfloat16 conv2d accumulation drift on larger feature maps."""

    def __call__(self, x: mx.array) -> mx.array:
        if x.dtype == mx.bfloat16:
            kh, kw = self.weight.shape[1], self.weight.shape[2]
            if kh == 1 and kw == 1 and self.stride == (1, 1) and self.padding == (0, 0):
                y = x @ self.weight.reshape(self.weight.shape[0], self.weight.shape[3]).T
                if "bias" in self:
                    y = y + self.bias
                return y
            y = mx.conv2d(
                x.astype(mx.float32),
                self.weight.astype(mx.float32),
                stride=self.stride,
                padding=self.padding,
                dilation=self.dilation,
                groups=self.groups,
            )
            if "bias" in self:
                y = y + self.bias.astype(mx.float32)
            return y.astype(mx.bfloat16)
        return super().__call__(x)


class IdentityConv2d(_Conv2d):
    """Same-shape Conv2d initialized to identity (Dirac)."""

    def __init__(self, C: int, kernel_size: int = 3, bias: bool = False):
        pad = kernel_size // 2
        super().__init__(C, C, kernel_size, padding=pad, bias=bias)
        eye = mx.eye(C, dtype=mx.float32).reshape(C, 1, 1, C)
        if pad > 0:
            self.weight = mx.pad(eye, [(0, 0), (pad, pad), (pad, pad), (0, 0)])
        else:
            self.weight = eye
        if bias:
            self.bias = mx.zeros((C,), dtype=mx.float32)


def conv(n_in: int, n_out: int, **kwargs) -> _Conv2d:
    return _Conv2d(n_in, n_out, 3, padding=1, **kwargs)


class Clamp(nn.Module):
    def __call__(self, x: mx.array) -> mx.array:
        return mx.tanh(x / 3.0) * 3.0


class MemBlock(nn.Module):
    def __init__(self, n_in: int, n_out: int):
        super().__init__()
        self.conv = nn.Sequential(
            conv(n_in * 2, n_out),
            nn.ReLU(),
            conv(n_out, n_out),
            nn.ReLU(),
            conv(n_out, n_out),
        )
        self.skip = _Conv2d(n_in, n_out, 1, bias=False) if n_in != n_out else nn.Identity()
        self.act = nn.ReLU()

    def __call__(self, x: mx.array, past: mx.array) -> mx.array:
        return self.act(self.conv(mx.concatenate([x, past], axis=-1)) + self.skip(x))


class TPool(nn.Module):
    """Temporal pooling across `stride` consecutive frames in NHWC layout."""

    def __init__(self, n_f: int, stride: int):
        super().__init__()
        self.stride = stride
        self.conv = _Conv2d(n_f * stride, n_f, 1, bias=False)

    def __call__(self, x: mx.array) -> mx.array:
        nt, h, w, c = x.shape
        x_grouped = (
            x.reshape(nt // self.stride, self.stride, h, w, c)
            .transpose(0, 2, 3, 1, 4)
            .reshape(nt // self.stride, h, w, self.stride * c)
        )
        return self.conv(x_grouped)


class TGrow(nn.Module):
    """Temporal upsampling by `stride` in NHWC layout."""

    def __init__(self, n_f: int, stride: int):
        super().__init__()
        self.stride = stride
        self.conv = _Conv2d(n_f, n_f * stride, 1, bias=False)

    def __call__(self, x: mx.array) -> mx.array:
        nt, h, w, c = x.shape
        y = self.conv(x)
        return (
            y.reshape(nt, h, w, self.stride, c)
            .transpose(0, 3, 1, 2, 4)
            .reshape(nt * self.stride, h, w, c)
        )


class PixelShuffle3d(nn.Module):
    """TAEHV 3D pixel shuffle from (B, C, F, H, W) to (B, F // ff, C * ff * hh * ww, H // hh, W // ww)."""

    def __init__(self, ff: int, hh: int, ww: int):
        super().__init__()
        self.ff = ff
        self.hh = hh
        self.ww = ww

    def __call__(self, x: mx.array) -> mx.array:
        b, c, f_tot, h_tot, w_tot = x.shape
        rem = f_tot % self.ff
        if rem != 0:
            first_frame = mx.repeat(x[:, :, 0:1, :, :], self.ff - rem, axis=2)
            x = mx.concatenate([first_frame, x], axis=2)
            f_tot = x.shape[2]
        f = f_tot // self.ff
        h = h_tot // self.hh
        w = w_tot // self.ww
        x = x.reshape(b, c, f, self.ff, h, self.hh, w, self.ww)
        # Target NTCHW: (b, f, c, ff, hh, ww, h, w)
        x = x.transpose(0, 2, 1, 3, 5, 7, 4, 6)
        return x.reshape(b, f, c * self.ff * self.hh * self.ww, h, w)


def apply_model_with_memblocks(
    model: Union[nn.Sequential, Sequence[nn.Module]],
    x: mx.array,
    parallel: bool,
    show_progress_bar: bool = False,
    mem: Optional[List] = None,
) -> Tuple[mx.array, Optional[List]]:
    """Apply a sequential decoder with MemBlock, TPool, and TGrow to an NTCHW tensor."""
    del show_progress_bar
    assert x.ndim == 5, f"TAEHV operates on NTCHW tensors, but got {x.ndim}-dim tensor"
    layers = model.layers if isinstance(model, nn.Sequential) else list(model)
    n, t, c, h, w = x.shape

    if parallel:
        # Convert NTCHW -> (N*T, H, W, C) for MLX Conv2d / Upsample
        x_nhwc = x.transpose(0, 1, 3, 4, 2).reshape(n * t, h, w, c)
        for b in layers:
            if isinstance(b, MemBlock):
                nt, h_cur, w_cur, c_cur = x_nhwc.shape
                t_cur = nt // n
                _x = x_nhwc.reshape(n, t_cur, h_cur, w_cur, c_cur)
                mem_x = mx.pad(_x, [(0, 0), (1, 0), (0, 0), (0, 0), (0, 0)])[:, :t_cur].reshape(
                    x_nhwc.shape
                )
                x_nhwc = b(x_nhwc, mem_x)
            else:
                x_nhwc = b(x_nhwc)
        nt, h_out, w_out, c_out = x_nhwc.shape
        t_out = nt // n
        out_ntchw = x_nhwc.reshape(n, t_out, h_out, w_out, c_out).transpose(0, 1, 4, 2, 3)
        return out_ntchw, mem

    if mem is None:
        mem = [None] * len(layers)

    x_nhwc = x.transpose(0, 1, 3, 4, 2)  # (N, T, H, W, C)
    work_queue: List[TWorkItem] = [TWorkItem(x_nhwc[:, idx, :, :, :], 0) for idx in range(t)]
    out: List[mx.array] = []

    while work_queue:
        xt, i = work_queue.pop(0)
        if i == len(layers):
            mx.eval(xt, *[m for m in mem if isinstance(m, mx.array)])
            out.append(xt)
            continue

        b = layers[i]
        if isinstance(b, MemBlock):
            past = mx.zeros_like(xt) if mem[i] is None else mem[i]
            xt_new = b(xt, past)
            mem[i] = xt
            work_queue.insert(0, TWorkItem(xt_new, i + 1))
        elif isinstance(b, TPool):
            if mem[i] is None:
                mem[i] = []
            mem[i].append(xt)
            if len(mem[i]) > b.stride:
                raise ValueError("TPool internal state invalid.")
            if len(mem[i]) == b.stride:
                xt_in = mx.stack(mem[i], axis=1).reshape(
                    n * b.stride, xt.shape[1], xt.shape[2], xt.shape[3]
                )
                xt_pooled = b(xt_in)
                mem[i] = []
                work_queue.insert(0, TWorkItem(xt_pooled, i + 1))
            else:
                mx.eval(*mem[i])
        elif isinstance(b, TGrow):
            xt_grown = b(xt).reshape(n, b.stride, xt.shape[1], xt.shape[2], xt.shape[3])
            for s in range(b.stride - 1, -1, -1):
                work_queue.insert(0, TWorkItem(xt_grown[:, s, :, :, :], i + 1))
        else:
            xt_next = b(xt)
            work_queue.insert(0, TWorkItem(xt_next, i + 1))

    out_ntchw = mx.stack(out, axis=1).transpose(0, 1, 4, 2, 3)
    return out_ntchw, mem


class _MemBox:
    """Opaque holder so temporal memory tensors are not registered as MLX module parameters."""

    __slots__ = ("mem",)

    def __init__(self, mem: List):
        self.mem = mem


class TAEHV(nn.Module):
    """Decoder-only TAEHV matching examples/WanVSR/utils/TCDecoder.py."""

    image_channels = 3

    def __init__(
        self,
        checkpoint_path: Optional[Union[str, Path]] = None,
        decoder_time_upscale: Tuple[bool, bool] = (True, True),
        decoder_space_upscale: Tuple[bool, bool, bool] = (True, True, True),
        channels: Sequence[int] = (256, 128, 64, 64),
        latent_channels: int = 16,
        dtype: Union[str, mx.Dtype] = mx.float32,
    ):
        super().__init__()
        self.latent_channels = latent_channels
        n_f = list(channels)
        self.frames_to_trim = 2 ** sum(decoder_time_upscale) - 1

        base_decoder = nn.Sequential(
            Clamp(),
            conv(self.latent_channels, n_f[0]),
            nn.ReLU(),
            MemBlock(n_f[0], n_f[0]),
            MemBlock(n_f[0], n_f[0]),
            MemBlock(n_f[0], n_f[0]),
            nn.Upsample(scale_factor=2 if decoder_space_upscale[0] else 1),
            TGrow(n_f[0], 1),
            conv(n_f[0], n_f[1], bias=False),
            MemBlock(n_f[1], n_f[1]),
            MemBlock(n_f[1], n_f[1]),
            MemBlock(n_f[1], n_f[1]),
            nn.Upsample(scale_factor=2 if decoder_space_upscale[1] else 1),
            TGrow(n_f[1], 2 if decoder_time_upscale[0] else 1),
            conv(n_f[1], n_f[2], bias=False),
            MemBlock(n_f[2], n_f[2]),
            MemBlock(n_f[2], n_f[2]),
            MemBlock(n_f[2], n_f[2]),
            nn.Upsample(scale_factor=2 if decoder_space_upscale[2] else 1),
            TGrow(n_f[2], 2 if decoder_time_upscale[1] else 1),
            conv(n_f[2], n_f[3], bias=False),
            nn.ReLU(),
            conv(n_f[3], TAEHV.image_channels),
        )

        self.decoder = self._apply_identity_deepen(base_decoder, how_many_each=1, k=3)
        self.pixel_shuffle = PixelShuffle3d(4, 8, 8)

        target_dtype = resolve_dtype(dtype)
        if target_dtype != mx.float32:
            self.set_dtype(target_dtype)

        if checkpoint_path is not None:
            sd = load_checkpoint_state_dict(checkpoint_path)
            sd = self.patch_tgrow_layers(sd)
            load_torch_state_dict(self, sd, dtype=target_dtype, strict=False)

        self._mem_box = _MemBox([None] * len(self.decoder.layers))

    def __setattr__(self, name: str, value):
        if name == "mem":
            self._mem_box.mem = value
        else:
            super().__setattr__(name, value)

    @property
    def mem(self) -> List:
        return self._mem_box.mem

    @staticmethod
    def _apply_identity_deepen(
        decoder: nn.Sequential, how_many_each: int = 1, k: int = 3
    ) -> nn.Sequential:
        """Return a new Sequential where every nn.ReLU is followed by how_many_each*(IdentityConv2d(k)+ReLU)."""
        new_layers: List[nn.Module] = []
        for b in decoder.layers:
            new_layers.append(b)
            if isinstance(b, nn.ReLU):
                c_out = None
                if len(new_layers) >= 2 and isinstance(new_layers[-2], nn.Conv2d):
                    c_out = int(new_layers[-2].weight.shape[0])
                elif len(new_layers) >= 2 and isinstance(new_layers[-2], MemBlock):
                    c_out = int(new_layers[-2].conv.layers[-1].weight.shape[0])
                if c_out is not None:
                    for _ in range(how_many_each):
                        new_layers.append(IdentityConv2d(c_out, kernel_size=k, bias=False))
                        new_layers.append(nn.ReLU())
        return nn.Sequential(*new_layers)

    def patch_tgrow_layers(self, sd: dict) -> dict:
        """Trim TGrow weights to match this decoder's out_channels if checkpoint is wider."""
        sd = dict(sd)
        for i, layer in enumerate(self.decoder.layers):
            if isinstance(layer, TGrow):
                out_ch = int(layer.conv.weight.shape[0])
                for key in (f"decoder.{i}.conv.weight", f"decoder.layers.{i}.conv.weight"):
                    if key in sd and sd[key].shape[0] > out_ch:
                        sd[key] = sd[key][-out_ch:]
        return sd

    def decode_video(
        self,
        x: mx.array,
        parallel: bool = True,
        show_progress_bar: bool = False,
        cond: Optional[mx.array] = None,
    ) -> mx.array:
        """Decode a sequence of frames from NTCHW latents to NTCHW RGB in ~[0, 1]."""
        trim_flag = self.mem[-8] is None
        param_dtype = self.decoder.layers[1].weight.dtype
        if x.dtype != param_dtype:
            x = x.astype(param_dtype)

        if cond is not None:
            if cond.dtype != x.dtype:
                cond = cond.astype(x.dtype)
            x = mx.concatenate([self.pixel_shuffle(cond), x], axis=2)

        x, self.mem = apply_model_with_memblocks(
            self.decoder, x, parallel, show_progress_bar=show_progress_bar, mem=self.mem
        )

        if trim_flag:
            return x[:, self.frames_to_trim :]
        return x

    def __call__(self, *args, **kwargs):
        raise NotImplementedError("Decoder-only model: call decode_video(...) instead.")

    def clean_mem(self):
        self.mem = [None] * len(self.decoder.layers)


def build_tcdecoder(
    new_channels: Sequence[int] = (512, 256, 128, 128),
    device: Optional[str] = None,
    dtype: Union[str, mx.Dtype] = mx.bfloat16,
    new_latent_channels: Optional[int] = None,
) -> TAEHV:
    """Build the FlashVSR TCDecoder matching examples/WanVSR/utils/TCDecoder.py."""
    del device
    latent_channels = 16 if new_latent_channels is None else new_latent_channels
    big = TAEHV(
        checkpoint_path=None,
        channels=list(new_channels),
        latent_channels=latent_channels,
        dtype=dtype,
    )
    big.clean_mem()
    return big
