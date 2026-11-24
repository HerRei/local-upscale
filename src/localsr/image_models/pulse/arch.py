"""PULSE, the network of the LocalSR x4 models.

The layout follows Philip Hofmann's PULSE specification (MIT, https://huggingface.co/Phips/PULSE). This file is an
independent reimplementation: a 3x3 stem, residual blocks of two bias-free 3x3 convolutions, a 3x3 head with pixel
shuffle, added to a fixed Magic Kernel Sharp upscale of the input. Only torch is needed.
"""

import math

import torch
from torch import nn
from torch.nn import functional as F


def mks(x):
    x = x.abs()
    return torch.where(
        x <= 0.5,
        17 / 16 - 7 / 4 * x.square(),
        torch.where(
            x <= 1.5,
            0.25 * (4 * x.square() - 11 * x + 7),
            torch.where(x <= 2.5, -0.125 * (x - 2.5).square(), 0.0),
        ),
    )


class MagicKernelSharp(nn.Module):
    def __init__(self, scale):
        super().__init__()
        self.scale = scale
        offsets = torch.arange(-2, 3, dtype=torch.float32)
        weights = torch.empty(3 * scale * scale, 1, 5, 5)
        for c in range(3):
            for py in range(scale):
                for px in range(scale):
                    y = mks((py + 0.5) / scale - 0.5 - offsets)
                    x = mks((px + 0.5) / scale - 0.5 - offsets)
                    weights[c * scale * scale + py * scale + px, 0] = torch.outer(y, x)
        self.register_buffer("weight", weights, persistent=False)

    def forward(self, x):
        if self.scale == 1:
            return x
        return F.pixel_shuffle(
            F.conv2d(F.pad(x, (2, 2, 2, 2), mode="replicate"), self.weight, groups=3),
            self.scale,
        )


class PulseBlock(nn.Module):
    def __init__(self, dim):
        super().__init__()
        self.conv1 = nn.Conv2d(dim, dim, 3, padding=1, bias=False)
        self.conv2 = nn.Conv2d(dim, dim, 3, padding=1, bias=False)

    def forward(self, x):
        return x + self.conv2(F.relu(self.conv1(x)))


class PULSE(nn.Module):
    def __init__(self, dim=48, scale=4, n_blocks=8):
        super().__init__()
        if scale not in (1, 2, 3, 4) or dim < 1 or n_blocks < 1:
            raise ValueError("PULSE requires scale 1/2/3/4 and positive dim/n_blocks")
        self.scale = scale
        self.stem = nn.Conv2d(3, dim, 3, padding=1, bias=False)
        self.body = nn.Sequential(*(PulseBlock(dim) for _ in range(n_blocks)))
        self.head = nn.Sequential(
            nn.Conv2d(dim, 3 * scale**2, 3, padding=1),
            nn.PixelShuffle(scale),
        )
        self.base = MagicKernelSharp(scale)
        self.reset_parameters()

    def reset_parameters(self):
        for module in self.modules():
            if isinstance(module, nn.Conv2d):
                nn.init.kaiming_normal_(module.weight, mode="fan_in", nonlinearity="relu")
                if module.bias is not None:
                    nn.init.zeros_(module.bias)
        with torch.no_grad():
            weight = self.head[0].weight
            base = torch.empty_like(weight[:3])
            nn.init.kaiming_uniform_(base, a=math.sqrt(5))
            for phase in range(self.scale**2):
                weight[phase :: self.scale**2].copy_(base)
            weight.mul_(1e-3)

    def forward(self, x):
        feat = self.stem(x)
        feat = feat + self.body(feat)
        return self.base(x) + self.head(feat)
