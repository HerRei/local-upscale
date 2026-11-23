# NERVE — Norm-free Efficient Restoration for Various Edge devices.
#
# Pure convolution (Conv-ReLU-Conv residual blocks, no normalization, no
# attention, no gating) with a 5x5 stem, a global residual and a bicubic input
# residual. Standard ops only, so it exports to dynamic-shape ONNX and converts
# to NCNN/CoreML without custom ops or fused/unfused pairs.
#
# Adapted from chaiNNer-org/spandrel#333 at commit
# 3429b70e043a96838b5f10d2fe275e56f69e5c69 (Apache-2.0, from Philip Hofmann /
# Phhofm/traiNNer-redux and huggingface.co/Phips/NERVE).

from __future__ import annotations

import math

import torch
from spandrel.util import store_hyperparameters
from torch import Tensor, nn
from torch.nn import functional as F  # noqa: N812


def _icnr_init(conv: nn.Conv2d, upscale: int) -> None:
    """ICNR initialization for a Conv2d that feeds a PixelShuffle(upscale)."""
    out_c = conv.out_channels
    sub = upscale * upscale
    assert out_c % sub == 0
    base_c = out_c // sub
    base = nn.init.kaiming_uniform_(conv.weight[:base_c].clone(), a=math.sqrt(5))
    with torch.no_grad():
        conv.weight.data.zero_()
        for i in range(sub):
            conv.weight.data[i::sub] = base
    if conv.bias is not None:
        nn.init.zeros_(conv.bias)


class NerveBlock(nn.Module):
    """Conv-ReLU-Conv residual block. No normalization, no shortcuts."""

    def __init__(self, dim: int) -> None:
        super().__init__()
        self.conv1 = nn.Conv2d(dim, dim, 3, padding=1, bias=False)
        self.conv2 = nn.Conv2d(dim, dim, 3, padding=1, bias=False)

    def forward(self, x: Tensor) -> Tensor:
        return x + self.conv2(F.relu(self.conv1(x)))


@store_hyperparameters()
class NERVE(nn.Module):
    """5x5 stem -> n_blocks residual blocks -> PixelShuffle head -> + bicubic(x)."""

    def __init__(
        self,
        *,
        in_ch: int = 3,
        out_ch: int = 3,
        dim: int = 64,
        n_blocks: int = 24,
        upscale: int = 4,
    ) -> None:
        super().__init__()
        self.upscale = upscale

        self.stem = nn.Conv2d(in_ch, dim, 5, padding=2, bias=False)
        self.blocks = nn.Sequential(*[NerveBlock(dim) for _ in range(n_blocks)])
        head = nn.Conv2d(dim, out_ch * upscale**2, 3, padding=1, bias=False)
        self.upsampler = nn.Sequential(head, nn.PixelShuffle(upscale))
        _icnr_init(head, upscale)

    def forward(self, x: Tensor) -> Tensor:
        feat = self.stem(x)
        out = feat + self.blocks(feat)
        out = self.upsampler(out)
        base = F.interpolate(x, scale_factor=self.upscale, mode="bicubic", align_corners=False)
        return out + base
