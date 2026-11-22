# Copyright (c) 2023 piddnad / DDColor authors. Apache-2.0 License (see NOTICE.md).
# Adapted from piddnad/DDColor (basicsr/archs/ddcolor_arch_utils/unet.py).
# Modified for LocalSR: inference-only helpers retained.

import collections
from enum import Enum

import torch
import torch.nn as nn
from torch.nn import functional as F

NormType = Enum("NormType", "Batch BatchZero Weight Spectral")


class Hook:
    feature: torch.Tensor | None = None

    def __init__(self, module: nn.Module):
        self.hook = module.register_forward_hook(self.hook_fn)

    def hook_fn(self, _module: nn.Module, _input: object, output: object) -> None:
        if isinstance(output, torch.Tensor):
            self.feature = output
        elif isinstance(output, collections.OrderedDict):
            self.feature = output["out"]


class SelfAttention(nn.Module):
    """Self attention layer for nd."""

    def __init__(self, n_channels: int):
        super().__init__()
        self.query = conv1d(n_channels, n_channels // 8)
        self.key = conv1d(n_channels, n_channels // 8)
        self.value = conv1d(n_channels, n_channels)
        self.gamma = nn.Parameter(torch.tensor([0.0]))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        size = x.size()
        x = x.view(*size[:2], -1)
        f, g, h = self.query(x), self.key(x), self.value(x)
        beta = F.softmax(torch.bmm(f.permute(0, 2, 1).contiguous(), g), dim=1)
        o = self.gamma * torch.bmm(h, beta) + x
        return o.view(*size).contiguous()


def batchnorm_2d(nf: int, norm_type: NormType = NormType.Batch) -> nn.BatchNorm2d:
    bn = nn.BatchNorm2d(nf)
    with torch.no_grad():
        bn.bias.fill_(1e-3)
        bn.weight.fill_(0.0 if norm_type == NormType.BatchZero else 1.0)
    return bn


def init_default(m: nn.Module, func=nn.init.kaiming_normal_) -> nn.Module:
    if func:
        if hasattr(m, "weight") and isinstance(m.weight, torch.Tensor):
            func(m.weight)
        if hasattr(m, "bias") and isinstance(m.bias, torch.Tensor):
            m.bias.data.fill_(0.0)
    return m


def icnr(x: torch.Tensor, scale: int = 2, init=nn.init.kaiming_normal_) -> None:
    ni, nf, h, w = x.shape
    ni2 = int(ni / (scale**2))
    k = init(torch.zeros([ni2, nf, h, w])).transpose(0, 1)
    k = k.contiguous().view(ni2, nf, -1)
    k = k.repeat(1, 1, scale**2)
    k = k.contiguous().view([nf, ni, h, w]).transpose(0, 1)
    x.data.copy_(k)


def conv1d(
    ni: int, no: int, ks: int = 1, stride: int = 1, padding: int = 0, bias: bool = False
) -> nn.Module:
    conv = nn.Conv1d(ni, no, ks, stride=stride, padding=padding, bias=bias)
    nn.init.kaiming_normal_(conv.weight)
    if bias and conv.bias is not None:
        conv.bias.data.zero_()
    return nn.utils.spectral_norm(conv)


def custom_conv_layer(
    ni: int,
    nf: int,
    ks: int = 3,
    stride: int = 1,
    padding: int | None = None,
    bias: bool | None = None,
    is_1d: bool = False,
    norm_type: NormType = NormType.Batch,
    use_activ: bool = True,
    transpose: bool = False,
    init=nn.init.kaiming_normal_,
    self_attention: bool = False,
    extra_bn: bool = False,
) -> nn.Sequential:
    if padding is None:
        padding = (ks - 1) // 2 if not transpose else 0
    bn = norm_type in (NormType.Batch, NormType.BatchZero) or extra_bn
    if bias is None:
        bias = not bn
    conv_func = nn.ConvTranspose2d if transpose else nn.Conv1d if is_1d else nn.Conv2d
    conv = init_default(
        conv_func(ni, nf, kernel_size=ks, bias=bias, stride=stride, padding=padding),
        init,
    )
    if norm_type == NormType.Weight:
        conv = nn.utils.weight_norm(conv)
    elif norm_type == NormType.Spectral:
        conv = nn.utils.spectral_norm(conv)
    layers: list[nn.Module] = [conv]
    if use_activ:
        layers.append(nn.ReLU(True))
    if bn:
        layers.append((nn.BatchNorm1d if is_1d else nn.BatchNorm2d)(nf))
    if self_attention:
        layers.append(SelfAttention(nf))
    return nn.Sequential(*layers)


class CustomPixelShuffle_ICNR(nn.Module):
    """Upsample by `scale` from `ni` to `nf` using PixelShuffle and ICNR init."""

    def __init__(
        self,
        ni: int,
        nf: int,
        scale: int = 2,
        blur: bool = True,
        norm_type: NormType = NormType.Spectral,
        extra_bn: bool = False,
    ):
        super().__init__()
        self.conv = custom_conv_layer(
            ni, nf * (scale**2), ks=1, use_activ=False, norm_type=norm_type, extra_bn=extra_bn
        )
        icnr(self.conv[0].weight)
        self.shuf = nn.PixelShuffle(scale)
        self.do_blur = blur
        self.pad = nn.ReplicationPad2d((1, 0, 1, 0))
        self.blur = nn.AvgPool2d(2, stride=1)
        self.relu = nn.ReLU(True)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.shuf(self.relu(self.conv(x)))
        return self.blur(self.pad(x)) if self.do_blur else x


class UnetBlockWide(nn.Module):
    """Quasi-UNet block using CustomPixelShuffle_ICNR upsampling."""

    def __init__(
        self,
        up_in_c: int,
        x_in_c: int,
        n_out: int,
        hook: Hook,
        blur: bool = False,
        self_attention: bool = False,
        norm_type: NormType = NormType.Spectral,
    ):
        super().__init__()
        self.hook = hook
        up_out = n_out
        self.shuf = CustomPixelShuffle_ICNR(
            up_in_c, up_out, blur=blur, norm_type=norm_type, extra_bn=True
        )
        self.bn = batchnorm_2d(x_in_c)
        ni = up_out + x_in_c
        self.conv = custom_conv_layer(
            ni, n_out, norm_type=norm_type, self_attention=self_attention, extra_bn=True
        )
        self.relu = nn.ReLU()

    def forward(self, up_in: torch.Tensor) -> torch.Tensor:
        s = self.hook.feature
        assert s is not None
        up_out = self.shuf(up_in)
        cat_x = self.relu(torch.cat([up_out, self.bn(s)], dim=1))
        return self.conv(cat_x)
