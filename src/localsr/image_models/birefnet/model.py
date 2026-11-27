"""Inference-only general BiRefNet network."""

import torch
from torch import nn
from torch.nn import functional as F

from .components import BasicDecBlk, BasicLatBlk, SwinTransformer


def resize(x, size):
    return F.interpolate(x, size=size, mode="bilinear", align_corners=True)


class SimpleConvs(nn.Module):
    def __init__(self, in_channels, out_channels):
        super().__init__()
        self.conv1 = nn.Conv2d(in_channels, 64, 3, padding=1)
        self.conv_out = nn.Conv2d(64, out_channels, 3, padding=1)

    def forward(self, x):
        return self.conv_out(self.conv1(x))


class Decoder(nn.Module):
    def __init__(self, channels):
        super().__init__()
        c0, c1, c2, c3 = channels
        self.ipt_blk5 = SimpleConvs(3072, c0 // 8)
        self.ipt_blk4 = SimpleConvs(768, c0 // 8)
        self.ipt_blk3 = SimpleConvs(192, c1 // 8)
        self.ipt_blk2 = SimpleConvs(48, c2 // 8)
        self.ipt_blk1 = SimpleConvs(3, c3 // 8)
        self.decoder_block4 = BasicDecBlk(c0 + c0 // 8, c1)
        self.decoder_block3 = BasicDecBlk(c1 + c0 // 8, c2)
        self.decoder_block2 = BasicDecBlk(c2 + c1 // 8, c3)
        self.decoder_block1 = BasicDecBlk(c3 + c2 // 8, c3 // 2)
        self.conv_out1 = nn.Sequential(nn.Conv2d(c3 // 2 + c3 // 8, 1, 1))
        self.lateral_block4 = BasicLatBlk(c1, c1)
        self.lateral_block3 = BasicLatBlk(c2, c2)
        self.lateral_block2 = BasicLatBlk(c3, c3)
        for index, channels_in in ((4, c1), (3, c2), (2, c3)):
            setattr(
                self,
                f"gdt_convs_{index}",
                nn.Sequential(
                    nn.Conv2d(channels_in, 16, 3, padding=1),
                    nn.BatchNorm2d(16),
                    nn.ReLU(inplace=True),
                ),
            )
            setattr(self, f"gdt_convs_attn_{index}", nn.Sequential(nn.Conv2d(16, 1, 1)))

    def forward(self, features):
        x, x1, x2, x3, x4 = features

        def add_input(feature, block):
            factor = x.shape[-1] // feature.shape[-1]
            patches = F.pixel_unshuffle(x, factor)
            return torch.cat((feature, block(resize(patches, feature.shape[-2:]))), dim=1)

        p = self.decoder_block4(add_input(x4, self.ipt_blk5))
        p = p * self.gdt_convs_attn_4(self.gdt_convs_4(p)).sigmoid()
        p = resize(p, x3.shape[-2:]) + self.lateral_block4(x3)
        p = self.decoder_block3(add_input(p, self.ipt_blk4))
        p = p * self.gdt_convs_attn_3(self.gdt_convs_3(p)).sigmoid()
        p = resize(p, x2.shape[-2:]) + self.lateral_block3(x2)
        p = self.decoder_block2(add_input(p, self.ipt_blk3))
        p = p * self.gdt_convs_attn_2(self.gdt_convs_2(p)).sigmoid()
        p = resize(p, x1.shape[-2:]) + self.lateral_block2(x1)
        p = self.decoder_block1(add_input(p, self.ipt_blk2))
        p = resize(p, x.shape[-2:])
        return self.conv_out1(add_input(p, self.ipt_blk1))


class BiRefNet(nn.Module):
    def __init__(self, embed_dim=192, depths=(2, 2, 18, 2), heads=(6, 12, 24, 48), window_size=12):
        super().__init__()
        self.bb = SwinTransformer(
            embed_dim=embed_dim, depths=depths, num_heads=heads, window_size=window_size
        )
        channels = [embed_dim * 16, embed_dim * 8, embed_dim * 4, embed_dim * 2]
        self.squeeze_module = nn.Sequential(BasicDecBlk(sum(channels), channels[0]))
        self.decoder = Decoder(channels)

    def forward(self, x):
        full = self.bb(x)
        small = self.bb(resize(x, (x.shape[-2] // 2, x.shape[-1] // 2)))
        x1, x2, x3, x4 = [
            torch.cat((a, resize(b, a.shape[-2:])), dim=1)
            for a, b in zip(full, small, strict=False)
        ]
        del full, small
        x4 = torch.cat(
            (resize(x1, x4.shape[-2:]), resize(x2, x4.shape[-2:]), resize(x3, x4.shape[-2:]), x4),
            dim=1,
        )
        return self.decoder((x, x1, x2, x3, self.squeeze_module(x4)))
