# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Hermes Reisner. Part of the MLX port of FlashVSR (https://github.com/OpenImagingLab/FlashVSR).

import math
from typing import Optional, Tuple

import mlx.core as mx
import mlx.nn as nn

from .attention import CrossAttention, SelfAttention

WAN_1_3B_CONFIG = {
    "dim": 1536,
    "num_heads": 12,
    "head_dim": 128,
    "ffn_dim": 8960,
    "num_layers": 30,
    "eps": 1e-6,
    "patch_size": (1, 2, 2),
    "in_dim": 16,
    "out_dim": 16,
    "text_dim": 4096,
    "freq_dim": 256,
}


def modulate(x: mx.array, shift: mx.array, scale: mx.array) -> mx.array:
    return x * (1 + scale) + shift


class GateModule(nn.Module):
    def __init__(self):
        super().__init__()

    def __call__(self, x: mx.array, gate: mx.array, residual: mx.array) -> mx.array:
        return x + gate * residual


class DiTBlock(nn.Module):
    def __init__(self, dim: int, num_heads: int, ffn_dim: int, eps: float = 1e-6):
        super().__init__()
        self.dim = dim
        self.num_heads = num_heads
        self.ffn_dim = ffn_dim

        self.self_attn = SelfAttention(dim, num_heads, eps)
        self.cross_attn = CrossAttention(dim, num_heads, eps)

        self.norm1 = nn.LayerNorm(dim, eps=eps, affine=False)
        self.norm2 = nn.LayerNorm(dim, eps=eps, affine=False)
        self.norm3 = nn.LayerNorm(dim, eps=eps, affine=True)
        self.ffn = nn.Sequential(
            nn.Linear(dim, ffn_dim),
            nn.GELU(approx="tanh"),
            nn.Linear(ffn_dim, dim),
        )
        self.modulation = mx.random.normal((1, 6, dim)) / (dim**0.5)
        self.gate = GateModule()

    def __call__(
        self,
        x: mx.array,
        context: Optional[mx.array],
        t_mod: mx.array,
        freqs: mx.array,
        f: int,
        h: int,
        w: int,
        local_num: Optional[int] = None,
        topk: Optional[int] = None,
        train_img: bool = False,
        block_id: Optional[int] = None,
        kv_len: Optional[int] = None,
        is_full_block: bool = False,
        is_stream: bool = False,
        pre_cache_k: Optional[mx.array] = None,
        pre_cache_v: Optional[mx.array] = None,
        local_range: int = 9,
    ):
        mod = self.modulation.astype(t_mod.dtype) + t_mod
        shift_msa, scale_msa, gate_msa, shift_mlp, scale_mlp, gate_mlp = mx.split(mod, 6, axis=1)
        input_x = modulate(self.norm1(x), shift_msa, scale_msa)

        if is_stream:
            self_attn_output, self_attn_cache_k, self_attn_cache_v = self.self_attn(
                input_x,
                freqs,
                f,
                h,
                w,
                local_num,
                topk,
                train_img,
                block_id,
                kv_len=kv_len,
                is_full_block=is_full_block,
                is_stream=True,
                pre_cache_k=pre_cache_k,
                pre_cache_v=pre_cache_v,
                local_range=local_range,
            )
        else:
            self_attn_output = self.self_attn(
                input_x,
                freqs,
                f,
                h,
                w,
                local_num,
                topk,
                train_img,
                block_id,
                kv_len=kv_len,
                is_full_block=is_full_block,
                is_stream=False,
                pre_cache_k=pre_cache_k,
                pre_cache_v=pre_cache_v,
                local_range=local_range,
            )

        x = self.gate(x, gate_msa, self_attn_output)
        x = x + self.cross_attn(self.norm3(x), context, is_stream=is_stream)
        input_x = modulate(self.norm2(x), shift_mlp, scale_mlp)
        x = self.gate(x, gate_mlp, self.ffn(input_x))

        if is_stream:
            return x, self_attn_cache_k, self_attn_cache_v
        return x


class MLP(nn.Module):
    def __init__(self, in_dim: int, out_dim: int, has_pos_emb: bool = False):
        super().__init__()
        self.proj = nn.Sequential(
            nn.LayerNorm(in_dim),
            nn.Linear(in_dim, in_dim),
            nn.GELU(),
            nn.Linear(in_dim, out_dim),
            nn.LayerNorm(out_dim),
        )
        self.has_pos_emb = has_pos_emb
        if has_pos_emb:
            self.emb_pos = mx.zeros((1, 514, 1280))

    def __call__(self, x: mx.array) -> mx.array:
        if self.has_pos_emb:
            x = x + self.emb_pos.astype(x.dtype)
        return self.proj(x)


class Head(nn.Module):
    def __init__(self, dim: int, out_dim: int, patch_size: Tuple[int, int, int], eps: float):
        super().__init__()
        self.dim = dim
        self.patch_size = patch_size
        self.norm = nn.LayerNorm(dim, eps=eps, affine=False)
        self.head = nn.Linear(dim, out_dim * math.prod(patch_size))
        self.modulation = mx.random.normal((1, 2, dim)) / (dim**0.5)

    def __call__(self, x: mx.array, t_mod: mx.array) -> mx.array:
        mod = self.modulation.astype(t_mod.dtype) + t_mod
        shift, scale = mx.split(mod, 2, axis=1)
        return self.head(self.norm(x) * (1 + scale) + shift)
