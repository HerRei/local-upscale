# SPDX-License-Identifier: Apache-2.0
# Based on TAEHV by Ollin Boer Bohan (MIT License, see ../LICENSES/TAEHV-MIT.txt) and
# OpenImagingLab FlashVSR (Apache-2.0).
# Modified for LocalSR: pruned unused Diffusers wrapper/stdout print, formatted for ruff.
"""Tiny AutoEncoder for Hunyuan Video (Decoder-only, pruned)."""

from collections import namedtuple

import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.nn.init as init
from einops import rearrange

TWorkItem = namedtuple("TWorkItem", ("input_tensor", "block_index"))


class IdentityConv2d(nn.Conv2d):
    """Same-shape Conv2d initialized to identity (Dirac)."""

    def __init__(self, C, kernel_size=3, bias=False):
        pad = kernel_size // 2
        super().__init__(C, C, kernel_size, padding=pad, bias=bias)
        with torch.no_grad():
            init.dirac_(self.weight)
            if self.bias is not None:
                self.bias.zero_()


def conv(n_in, n_out, **kwargs):
    return nn.Conv2d(n_in, n_out, 3, padding=1, **kwargs)


class Clamp(nn.Module):
    def forward(self, x):
        return torch.tanh(x / 3) * 3


class MemBlock(nn.Module):
    def __init__(self, n_in, n_out):
        super().__init__()
        self.conv = nn.Sequential(
            conv(n_in * 2, n_out),
            nn.ReLU(inplace=True),
            conv(n_out, n_out),
            nn.ReLU(inplace=True),
            conv(n_out, n_out),
        )
        self.skip = nn.Conv2d(n_in, n_out, 1, bias=False) if n_in != n_out else nn.Identity()
        self.act = nn.ReLU(inplace=True)

    def forward(self, x, past):
        return self.act(self.conv(torch.cat([x, past], 1)) + self.skip(x))


class TPool(nn.Module):
    def __init__(self, n_f, stride):
        super().__init__()
        self.stride = stride
        self.conv = nn.Conv2d(n_f * stride, n_f, 1, bias=False)

    def forward(self, x):
        _NT, C, H, W = x.shape
        return self.conv(x.reshape(-1, self.stride * C, H, W))


class TGrow(nn.Module):
    def __init__(self, n_f, stride):
        super().__init__()
        self.stride = stride
        self.conv = nn.Conv2d(n_f, n_f * stride, 1, bias=False)

    def forward(self, x):
        _NT, C, H, W = x.shape
        x = self.conv(x)
        return x.reshape(-1, C, H, W)


class PixelShuffle3d(nn.Module):
    def __init__(self, ff, hh, ww):
        super().__init__()
        self.ff = ff
        self.hh = hh
        self.ww = ww

    def forward(self, x):
        # x: (B, C, F, H, W)
        _B, _C, F_, _H, _W = x.shape
        if F_ % self.ff != 0:
            first_frame = x[:, :, 0:1, :, :].repeat(1, 1, self.ff - F_ % self.ff, 1, 1)
            x = torch.cat([first_frame, x], dim=2)
        return rearrange(
            x,
            "b c (f ff) (h hh) (w ww) -> b (c ff hh ww) f h w",
            ff=self.ff,
            hh=self.hh,
            ww=self.ww,
        ).transpose(1, 2)


def apply_model_with_memblocks(model, x, parallel, show_progress_bar, mem=None):
    """Apply a sequential model with memblocks to an NTCHW tensor."""
    assert x.ndim == 5, f"TAEHV operates on NTCHW tensors, but got {x.ndim}-dim tensor"
    N, T, C, H, W = x.shape
    if parallel:
        x = x.reshape(N * T, C, H, W)
        for b in model:
            if isinstance(b, MemBlock):
                NT, C, H, W = x.shape
                T = NT // N
                _x = x.reshape(N, T, C, H, W)
                mem = F.pad(_x, (0, 0, 0, 0, 0, 0, 1, 0), value=0)[:, :T].reshape(x.shape)
                x = b(x, mem)
            else:
                x = b(x)
        NT, C, H, W = x.shape
        T = NT // N
        x = x.view(N, T, C, H, W)
    else:
        del show_progress_bar
        out = []
        work_queue = [TWorkItem(xt, 0) for xt in x.reshape(N, T * C, H, W).chunk(T, dim=1)]
        while work_queue:
            xt, i = work_queue.pop(0)
            if i == len(model):
                out.append(xt)
            else:
                b = model[i]
                if isinstance(b, MemBlock):
                    if mem[i] is None:
                        xt_new = b(xt, xt * 0)
                        mem[i] = xt
                    else:
                        xt_new = b(xt, mem[i])
                        mem[i].copy_(xt)
                    work_queue.insert(0, TWorkItem(xt_new, i + 1))
                elif isinstance(b, TPool):
                    if mem[i] is None:
                        mem[i] = []
                    mem[i].append(xt)
                    if len(mem[i]) > b.stride:
                        raise ValueError("TPool internal state invalid.")
                    elif len(mem[i]) == b.stride:
                        N_, C_, H_, W_ = xt.shape
                        xt = b(torch.cat(mem[i], 1).view(N_ * b.stride, C_, H_, W_))
                        mem[i] = []
                        work_queue.insert(0, TWorkItem(xt, i + 1))
                elif isinstance(b, TGrow):
                    xt = b(xt)
                    _NT, C_, H_, W_ = xt.shape
                    for xt_next in reversed(xt.view(N, b.stride * C_, H_, W_).chunk(b.stride, 1)):
                        work_queue.insert(0, TWorkItem(xt_next, i + 1))
                else:
                    xt = b(xt)
                    work_queue.insert(0, TWorkItem(xt, i + 1))
        x = torch.stack(out, 1)
    return x, mem


class TAEHV(nn.Module):
    image_channels = 3

    def __init__(
        self,
        checkpoint_path="taehv.pth",
        decoder_time_upscale=(True, True),
        decoder_space_upscale=(True, True, True),
        channels=(256, 128, 64, 64),
        latent_channels=16,
    ):
        """Initialize TAEHV (decoder-only) with built-in deepening after every ReLU."""
        super().__init__()
        self.latent_channels = latent_channels
        n_f = list(channels)
        self.frames_to_trim = 2 ** sum(decoder_time_upscale) - 1

        base_decoder = nn.Sequential(
            Clamp(),
            conv(self.latent_channels, n_f[0]),
            nn.ReLU(inplace=True),
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
            nn.ReLU(inplace=True),
            conv(n_f[3], TAEHV.image_channels),
        )

        self.decoder = self._apply_identity_deepen(base_decoder, how_many_each=1, k=3)
        self.pixel_shuffle = PixelShuffle3d(4, 8, 8)
        self._rocm_cache_handles: list[torch.utils.hooks.RemovableHandle] = []

        if checkpoint_path is not None:
            self.load_state_dict(
                self.patch_tgrow_layers(
                    torch.load(checkpoint_path, map_location="cpu", weights_only=True)
                ),
                strict=False,
            )

        self.mem: list = [None] * len(self.decoder)

    @staticmethod
    def _apply_identity_deepen(decoder: nn.Sequential, how_many_each=1, k=3) -> nn.Sequential:
        """Insert how_many_each*(IdentityConv2d(k)+ReLU) after every nn.ReLU."""
        new_layers = []
        for b in decoder:
            new_layers.append(b)
            if isinstance(b, nn.ReLU):
                C = None
                if len(new_layers) >= 2 and isinstance(new_layers[-2], nn.Conv2d):
                    C = new_layers[-2].out_channels
                elif len(new_layers) >= 2 and isinstance(new_layers[-2], MemBlock):
                    C = new_layers[-2].conv[-1].out_channels
                if C is not None:
                    for _ in range(how_many_each):
                        new_layers.append(IdentityConv2d(C, kernel_size=k, bias=False))
                        new_layers.append(nn.ReLU(inplace=True))
        return nn.Sequential(*new_layers)

    def patch_tgrow_layers(self, sd):
        """Patch TGrow layers to use a smaller kernel if needed (decoder-only)."""
        new_sd = self.state_dict()
        for i, layer in enumerate(self.decoder):
            if isinstance(layer, TGrow):
                key = f"decoder.{i}.conv.weight"
                if key in sd and sd[key].shape[0] > new_sd[key].shape[0]:
                    sd[key] = sd[key][-new_sd[key].shape[0] :]
        return sd

    def decode_video(self, x, parallel=True, show_progress_bar=False, cond=None):
        """Decode a sequence of frames from NTCHW latents; returns NTCHW RGB in ~[0, 1]."""
        trim_flag = self.mem[-8] is None

        if cond is not None:
            x = torch.cat([self.pixel_shuffle(cond), x], dim=2)

        x, self.mem = apply_model_with_memblocks(
            self.decoder, x, parallel, show_progress_bar, mem=self.mem
        )

        if trim_flag:
            return x[:, self.frames_to_trim :]
        return x

    def forward(self, *args, **kwargs):
        raise NotImplementedError("Decoder-only model: call decode_video(...) instead.")

    def set_rocm_cache_release(self, enabled=True):
        """Release allocator cache at decoder block boundaries on ROCm."""
        for handle in getattr(self, "_rocm_cache_handles", []):
            handle.remove()
        self._rocm_cache_handles = []
        if enabled and torch.version.hip:
            for module in self.decoder.modules():
                if isinstance(module, MemBlock):
                    self._rocm_cache_handles.append(
                        module.register_forward_hook(
                            lambda _module, _inputs, _output: torch.cuda.empty_cache()
                        )
                    )

    def clean_mem(self):
        self.mem = [None] * len(self.decoder)


def build_tcdecoder(
    new_channels=(512, 256, 128, 128),
    device="cuda",
    dtype=torch.bfloat16,
    new_latent_channels=None,
):
    """Build the wider TCDecoder matching FlashVSR v1.1 Tiny."""
    if new_latent_channels is not None:
        big = (
            TAEHV(
                checkpoint_path=None,
                channels=new_channels,
                latent_channels=new_latent_channels,
            )
            .to(device)
            .to(dtype)
            .train()
        )
    else:
        big = TAEHV(checkpoint_path=None, channels=new_channels).to(device).to(dtype).train()

    big.clean_mem()
    return big
