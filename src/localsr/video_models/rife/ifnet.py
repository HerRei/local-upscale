# Copyright (c) 2021 hzwer. MIT License (see NOTICE.md in this directory).
#
# IFNet from Practical-RIFE v4.26 (train_log/IFNet_HDv3.py and model/warplayer.py of the
# official 4.26 release, https://github.com/hzwer/Practical-RIFE). v4.25 uses the same
# network and the same files.
#
# Modified for LocalSR: inference only (the training branches, the unused conv_bn helper
# and the module-level CUDA device are removed); the warp grid is built per call
# instead of cached in a global dict and sampled in FP32 (as in HolyWu/vs-rife); the encoder
# features of a frame pair can be passed in, so several timesteps reuse them; IFNet.forward
# downsamples each block input on its own instead of concatenating them at full resolution
# first. Bilinear resizing works per channel, so the frames are the same (bit for bit on
# Apple GPUs, within float rounding on the CPU), and a 4320 x 5760 frame no longer needs
# 1.4 to 3 GiB blocks that a 16 GB Mac's graphics memory cannot hold next to the models.

import torch
import torch.nn as nn
import torch.nn.functional as F


def conv(in_planes, out_planes, kernel_size=3, stride=1, padding=1, dilation=1):
    return nn.Sequential(
        nn.Conv2d(
            in_planes,
            out_planes,
            kernel_size=kernel_size,
            stride=stride,
            padding=padding,
            dilation=dilation,
            bias=True,
        ),
        nn.LeakyReLU(0.2, True),
    )


class Head(nn.Module):
    def __init__(self):
        super().__init__()
        self.cnn0 = nn.Conv2d(3, 16, 3, 2, 1)
        self.cnn1 = nn.Conv2d(16, 16, 3, 1, 1)
        self.cnn2 = nn.Conv2d(16, 16, 3, 1, 1)
        self.cnn3 = nn.ConvTranspose2d(16, 4, 4, 2, 1)
        self.relu = nn.LeakyReLU(0.2, True)

    def forward(self, x):
        x = self.relu(self.cnn0(x))
        x = self.relu(self.cnn1(x))
        x = self.relu(self.cnn2(x))
        return self.cnn3(x)


class ResConv(nn.Module):
    def __init__(self, c, dilation=1):
        super().__init__()
        self.conv = nn.Conv2d(c, c, 3, 1, dilation, dilation=dilation, groups=1)
        self.beta = nn.Parameter(torch.ones((1, c, 1, 1)), requires_grad=True)
        self.relu = nn.LeakyReLU(0.2, True)

    def forward(self, x):
        return self.relu(self.conv(x) * self.beta + x)


class IFBlock(nn.Module):
    def __init__(self, in_planes, c=64):
        super().__init__()
        self.conv0 = nn.Sequential(
            conv(in_planes, c // 2, 3, 2, 1),
            conv(c // 2, c, 3, 2, 1),
        )
        self.convblock = nn.Sequential(*(ResConv(c) for _ in range(8)))
        self.lastconv = nn.Sequential(nn.ConvTranspose2d(c, 4 * 13, 4, 2, 1), nn.PixelShuffle(2))

    def low(self, inputs, flow=None, scale=1):
        """``forward`` before its final upsampling, from inputs already downsampled by ``scale``.

        ``forward`` concatenates its inputs and then downsamples them. Bilinear resizing
        treats every channel on its own, so downsampling each input first gives the same
        values without the full-resolution concatenation.
        """
        if flow is not None:
            inputs = [
                *inputs,
                F.interpolate(flow, scale_factor=1.0 / scale, mode="bilinear", align_corners=False)
                * 1.0
                / scale,
            ]
        feat = self.conv0(torch.cat(inputs, 1))
        feat = self.convblock(feat)
        return self.lastconv(feat)

    def forward(self, x, flow=None, scale=1):
        x = F.interpolate(x, scale_factor=1.0 / scale, mode="bilinear", align_corners=False)
        if flow is not None:
            flow = (
                F.interpolate(flow, scale_factor=1.0 / scale, mode="bilinear", align_corners=False)
                * 1.0
                / scale
            )
            x = torch.cat((x, flow), 1)
        feat = self.conv0(x)
        feat = self.convblock(feat)
        tmp = self.lastconv(feat)
        tmp = F.interpolate(tmp, scale_factor=scale, mode="bilinear", align_corners=False)
        flow = tmp[:, :4] * scale
        mask = tmp[:, 4:5]
        feat = tmp[:, 5:]
        return flow, mask, feat


class IFNet(nn.Module):
    def __init__(self):
        super().__init__()
        self.block0 = IFBlock(7 + 8, c=192)
        self.block1 = IFBlock(8 + 4 + 8 + 8, c=128)
        self.block2 = IFBlock(8 + 4 + 8 + 8, c=96)
        self.block3 = IFBlock(8 + 4 + 8 + 8, c=64)
        self.block4 = IFBlock(8 + 4 + 8 + 8, c=32)
        self.encode = Head()

    @staticmethod
    def _sampler(image, flow):
        """A function that samples channels of ``image`` along ``flow``.

        Sampled in FP32, as vs-rife does: half-precision grid coordinates in [-1, 1] are
        only good to about a pixel across a 4K frame. The sample grid is built in
        grid_sample's (N, H, W, 2) layout from the flow plus the base coordinates (the
        published grid + flow), so no full-resolution base grid is kept between calls.
        """
        dtype = image.dtype
        flow = flow.float()
        height, width = flow.shape[2], flow.shape[3]
        across = flow[:, 0] / ((image.shape[3] - 1.0) / 2.0)
        across.add_(torch.linspace(-1.0, 1.0, width, device=flow.device).view(1, 1, width))
        down = flow[:, 1] / ((image.shape[2] - 1.0) / 2.0)
        down.add_(torch.linspace(-1.0, 1.0, height, device=flow.device).view(1, height, 1))
        sample = torch.stack([across, down], -1)
        del across, down

        def sampled(planes):
            return F.grid_sample(
                input=planes.float(),
                grid=sample,
                mode="bilinear",
                padding_mode="border",
                align_corners=True,
            ).to(dtype)

        return sampled

    def warp(self, image, flow, factor=None):
        """``image`` sampled along ``flow``; resized by ``factor`` channel by channel if given.

        Sampling and bilinear resizing treat every channel on its own, so the resized
        result equals resizing the whole warped image, without holding it.
        """
        sampled = self._sampler(image, flow)
        if factor is None:
            return sampled(image)
        return torch.cat([_resize(sampled(plane), factor) for plane in image.split(1, 1)], 1)

    def forward(self, img0, img1, timestep, scale_list, f0=None, f1=None):
        """Return the frame at ``timestep`` (a scalar in (0, 1)) between img0 and img1.

        The same computation as Practical-RIFE's IFNet.forward, ordered so that only a few
        full-resolution planes exist at once: every block input is made and downsampled
        one plane at a time, only the last block's warped frames are kept at full
        resolution, and the flow update and the final blend work in place.
        """
        if f0 is None:
            f0 = self.encode(img0[:, :3])
        if f1 is None:
            f1 = self.encode(img1[:, :3])
        blocks = [self.block0, self.block1, self.block2, self.block3, self.block4]
        full_timestep = (img0[:, :1].clone() * 0 + 1) * timestep
        timesteps = [_resize(full_timestep, 1.0 / scale) for scale in scale_list[: len(blocks)]]
        del full_timestep
        flow = None
        mask = None
        tmp = None
        low_img0 = low_img1 = None
        previous_scale = 1
        for i, block in enumerate(blocks):
            scale = scale_list[i]
            if flow is None:
                inputs = [
                    _resize(img0[:, :3], 1.0 / scale),
                    _resize(img1[:, :3], 1.0 / scale),
                    _resize(f0, 1.0 / scale),
                    _resize(f1, 1.0 / scale),
                    timesteps[i],
                ]
                tmp = block.low(inputs, None, scale=scale)
                flow = _resize(tmp[:, :4], scale).mul_(scale)
            else:
                # The previous block's features, at full resolution in the original, are
                # only ever downsampled for this block.
                feat = torch.cat(
                    [
                        _resize(_resize(plane, previous_scale), 1.0 / scale)
                        for plane in tmp[:, 5:].split(1, 1)
                    ],
                    1,
                )
                inputs = [
                    low_img0,
                    low_img1,
                    self.warp(f0, flow[:, :2], 1.0 / scale),
                    self.warp(f1, flow[:, 2:4], 1.0 / scale),
                    timesteps[i],
                    _resize(mask, 1.0 / scale),
                    feat,
                ]
                tmp = block.low(inputs, flow, scale=scale)
                del inputs, feat
                # flow + upsampled update, one plane at a time and in place.
                for plane in range(4):
                    flow[:, plane : plane + 1].add_(
                        _resize(tmp[:, plane : plane + 1], scale).mul_(scale)
                    )
            mask = _resize(tmp[:, 4:5], scale)
            previous_scale = scale
            if i + 1 < len(blocks):
                # Before the last block the warped frames only feed the next block.
                following = 1.0 / scale_list[i + 1]
                low_img0 = self.warp(img0, flow[:, :2], following)[:, :3]
                low_img1 = self.warp(img1, flow[:, 2:4], following)[:, :3]
        # warped_img0 * mask + warped_img1 * (1 - mask), with warped_img1 made one plane
        # at a time and added in place.
        mask = torch.sigmoid(mask)
        merged = self.warp(img0, flow[:, :2]).mul_(mask)
        inverse = 1 - mask
        del mask
        sampled = self._sampler(img1, flow[:, 2:4])
        for plane in range(merged.shape[1]):
            merged[:, plane : plane + 1].add_(sampled(img1[:, plane : plane + 1]).mul_(inverse))
        return merged


def _resize(tensor, factor):
    return F.interpolate(tensor, scale_factor=factor, mode="bilinear", align_corners=False)
