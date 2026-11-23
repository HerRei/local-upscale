# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Hermes Reisner. Part of the MLX port of FlashVSR (https://github.com/OpenImagingLab/FlashVSR).

from typing import List, Optional, Tuple

import mlx.core as mx
import mlx.nn as nn

from .dit import WAN_1_3B_CONFIG, DiTBlock, Head
from .rope import build_freqs_3d, precompute_freqs_cis_3d, sinusoidal_embedding_1d


class WanModel(nn.Module):
    """MLX port of the FlashVSR Wan 2.1 1.3B DiT backbone (without image branch)."""

    def __init__(
        self,
        dim: int = WAN_1_3B_CONFIG["dim"],
        in_dim: int = WAN_1_3B_CONFIG["in_dim"],
        ffn_dim: int = WAN_1_3B_CONFIG["ffn_dim"],
        out_dim: int = WAN_1_3B_CONFIG["out_dim"],
        text_dim: int = WAN_1_3B_CONFIG["text_dim"],
        freq_dim: int = WAN_1_3B_CONFIG["freq_dim"],
        eps: float = WAN_1_3B_CONFIG["eps"],
        patch_size: Tuple[int, int, int] = WAN_1_3B_CONFIG["patch_size"],
        num_heads: int = WAN_1_3B_CONFIG["num_heads"],
        num_layers: int = WAN_1_3B_CONFIG["num_layers"],
        head_dim: Optional[int] = None,
        has_image_input: bool = False,
    ):
        super().__init__()
        if head_dim is not None and head_dim != dim // num_heads:
            raise ValueError(
                f"head_dim ({head_dim}) must equal dim // num_heads ({dim // num_heads})"
            )
        self.dim = dim
        self.in_dim = in_dim
        self.ffn_dim = ffn_dim
        self.out_dim = out_dim
        self.text_dim = text_dim
        self.freq_dim = freq_dim
        self.eps = eps
        self.patch_size = tuple(patch_size)
        self.num_heads = num_heads
        self.num_layers = num_layers
        self.has_image_input = has_image_input

        self.patch_embedding = nn.Conv3d(
            in_dim, dim, kernel_size=self.patch_size, stride=self.patch_size
        )

        self.text_embedding = nn.Sequential(
            nn.Linear(text_dim, dim),
            nn.GELU(approx="tanh"),
            nn.Linear(dim, dim),
        )
        self.time_embedding = nn.Sequential(
            nn.Linear(freq_dim, dim),
            nn.SiLU(),
            nn.Linear(dim, dim),
        )
        self.time_projection = nn.Sequential(
            nn.SiLU(),
            nn.Linear(dim, dim * 6),
        )

        self.blocks = [DiTBlock(dim, num_heads, ffn_dim, eps) for _ in range(num_layers)]
        self.head = Head(dim, out_dim, self.patch_size, eps)

        head_dim = dim // num_heads
        # Tuple of arrays is excluded from nn.Module.parameters() in MLX.
        self.freqs = precompute_freqs_cis_3d(head_dim)
        self._cross_kv_initialized = False

    def clear_cross_kv(self):
        for blk in self.blocks:
            blk.cross_attn.clear_cache()
        self._cross_kv_initialized = False

    def reinit_cross_kv(self, new_context: mx.array):
        ctx_txt = self.text_embedding(new_context)
        for blk in self.blocks:
            blk.cross_attn.init_cache(ctx_txt)
        self._cross_kv_initialized = True

    def patchify(self, x: mx.array) -> Tuple[mx.array, Tuple[int, int, int]]:
        # Input is (B, C, F, H, W); MLX Conv3d expects (B, F, H, W, C).
        x_nhwc = x.transpose(0, 2, 3, 4, 1)
        out = self.patch_embedding(x_nhwc)
        b, f, h, w, c = out.shape
        return out.reshape(b, f * h * w, c), (f, h, w)

    def unpatchify(self, x: mx.array, grid_size: Tuple[int, int, int]) -> mx.array:
        f, h, w = grid_size
        px, py, pz = self.patch_size
        b = x.shape[0]
        c = x.shape[2] // (px * py * pz)
        x = x.reshape(b, f, h, w, px, py, pz, c)
        x = x.transpose(0, 7, 1, 4, 2, 5, 3, 6)
        return x.reshape(b, c, f * px, h * py, w * pz)

    def __call__(
        self,
        x: mx.array,
        timestep: mx.array,
        context: Optional[mx.array] = None,
        LQ_latents: Optional[List[mx.array]] = None,
        train_img: bool = False,
        topk_ratio: float = 2.0,
        kv_ratio: float = 3.0,
        local_num: Optional[int] = None,
        is_full_block: bool = False,
        local_range: int = 9,
    ) -> mx.array:
        t = self.time_embedding(sinusoidal_embedding_1d(self.freq_dim, timestep))
        t_mod = self.time_projection(t).reshape(t.shape[0], 6, self.dim)

        x, (f, h, w) = self.patchify(x)

        win = (2, 8, 8)
        seqlen = f // win[0]
        if local_num is None:
            local_num = seqlen

        window_size = win[0] * h * w // 128
        square_num = window_size * window_size
        topk = min(max(int(square_num * topk_ratio), 1), int(square_num * seqlen) - 1)
        kv_len = min(max(int(window_size * kv_ratio), 1), int(window_size * seqlen) - 1)

        freqs = build_freqs_3d(self.freqs, f, h, w, f_start=0)

        for block_id, block in enumerate(self.blocks):
            if LQ_latents is not None and block_id < len(LQ_latents):
                x = x + LQ_latents[block_id]
            x = block(
                x,
                context,
                t_mod,
                freqs,
                f,
                h,
                w,
                local_num,
                topk,
                train_img,
                block_id,
                kv_len,
                is_full_block,
                False,
                None,
                None,
                local_range=local_range,
            )

        x = self.head(x, t)
        return self.unpatchify(x, (f, h, w))
