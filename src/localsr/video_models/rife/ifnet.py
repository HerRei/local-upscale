# Copyright (c) 2021 hzwer. MIT License (see NOTICE.md in this directory).
#
# IFNet from Practical-RIFE v4.26 (train_log/IFNet_HDv3.py and model/warplayer.py of the
# official 4.26 release, https://github.com/hzwer/Practical-RIFE). v4.25 uses the same
# network and the same files.
#
# Modified for LocalSR: inference only (the training branches, the unused conv_bn helper
# and the module-level CUDA device are removed); the warp grid is cached per module
# instead of in a global dict and sampled in FP32 (as in HolyWu/vs-rife); the encoder
# features of a frame pair can be passed in, so several timesteps reuse them.

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
        self._grids: dict[tuple[str, tuple[int, ...]], torch.Tensor] = {}

    def warp(self, image, flow):
        # Sampled in FP32, as vs-rife does: half-precision grid coordinates in [-1, 1]
        # are only good to about a pixel across a 4K frame.
        dtype = image.dtype
        image, flow = image.float(), flow.float()
        key = (str(flow.device), tuple(flow.shape))
        grid = self._grids.get(key)
        if grid is None:
            horizontal = (
                torch.linspace(-1.0, 1.0, flow.shape[3], device=flow.device)
                .view(1, 1, 1, flow.shape[3])
                .expand(flow.shape[0], -1, flow.shape[2], -1)
            )
            vertical = (
                torch.linspace(-1.0, 1.0, flow.shape[2], device=flow.device)
                .view(1, 1, flow.shape[2], 1)
                .expand(flow.shape[0], -1, -1, flow.shape[3])
            )
            grid = torch.cat([horizontal, vertical], 1)
            self._grids = {key: grid}
        flow = torch.cat(
            [
                flow[:, 0:1, :, :] / ((image.shape[3] - 1.0) / 2.0),
                flow[:, 1:2, :, :] / ((image.shape[2] - 1.0) / 2.0),
            ],
            1,
        )
        sample = (grid + flow).permute(0, 2, 3, 1)
        return F.grid_sample(
            input=image, grid=sample, mode="bilinear", padding_mode="border", align_corners=True
        ).to(dtype)

    def forward(self, img0, img1, timestep, scale_list, f0=None, f1=None):
        """Return the frame at ``timestep`` (a scalar in (0, 1)) between img0 and img1."""
        timestep = (img0[:, :1].clone() * 0 + 1) * timestep
        if f0 is None:
            f0 = self.encode(img0[:, :3])
        if f1 is None:
            f1 = self.encode(img1[:, :3])
        warped_img0 = img0
        warped_img1 = img1
        flow = None
        mask = None
        feat = None
        block = [self.block0, self.block1, self.block2, self.block3, self.block4]
        for i in range(5):
            if flow is None:
                flow, mask, feat = block[i](
                    torch.cat((img0[:, :3], img1[:, :3], f0, f1, timestep), 1),
                    None,
                    scale=scale_list[i],
                )
            else:
                wf0 = self.warp(f0, flow[:, :2])
                wf1 = self.warp(f1, flow[:, 2:4])
                fd, m0, feat = block[i](
                    torch.cat(
                        (warped_img0[:, :3], warped_img1[:, :3], wf0, wf1, timestep, mask, feat), 1
                    ),
                    flow,
                    scale=scale_list[i],
                )
                mask = m0
                flow = flow + fd
            warped_img0 = self.warp(img0, flow[:, :2])
            warped_img1 = self.warp(img1, flow[:, 2:4])
        mask = torch.sigmoid(mask)
        return warped_img0 * mask + warped_img1 * (1 - mask)
