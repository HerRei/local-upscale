# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 ModelScope / DiffSynth-Studio and OpenImagingLab FlashVSR authors.
# Modified for LocalSR: pruned CUDA block_sparse_attn, flash_attn/sageattn, training forward,
# and state-dict converters; routed CPU sparse attention to reference_attention.

import math
from typing import Optional, Tuple

import torch
import torch.nn as nn
import torch.nn.functional as F
from einops import rearrange

from .rocm_attention import reference_attention, sparse_attention

WAN_1_3B_CONFIG = {
    "dim": 1536,
    "in_dim": 16,
    "ffn_dim": 8960,
    "out_dim": 16,
    "text_dim": 4096,
    "freq_dim": 256,
    "eps": 1e-6,
    "patch_size": (1, 2, 2),
    "num_heads": 12,
    "num_layers": 30,
}


@torch.no_grad()
def build_local_block_mask_shifted_vec_normal_slide(
    block_h: int,
    block_w: int,
    win_h: int = 6,
    win_w: int = 6,
    include_self: bool = True,
    device=None,
) -> torch.Tensor:
    device = device or torch.device("cpu")
    H, W = block_h, block_w
    r = torch.arange(H, device=device)
    c = torch.arange(W, device=device)
    YY, XX = torch.meshgrid(r, c, indexing="ij")
    r_all = YY.reshape(-1)
    c_all = XX.reshape(-1)
    r_half = win_h // 2
    c_half = win_w // 2
    start_r = r_all - r_half
    end_r = start_r + win_h - 1
    start_c = c_all - c_half
    end_c = start_c + win_w - 1
    in_row = (r_all[None, :] >= start_r[:, None]) & (r_all[None, :] <= end_r[:, None])
    in_col = (c_all[None, :] >= start_c[:, None]) & (c_all[None, :] <= end_c[:, None])
    mask = in_row & in_col
    if not include_self:
        mask.fill_diagonal_(False)
    return mask


class WindowPartition3D:
    """Partition / reverse-partition helpers for 5-D tensors (B, F, H, W, C)."""

    @staticmethod
    def partition(x: torch.Tensor, win: Tuple[int, int, int]):
        B, F_, H, W, C = x.shape
        wf, wh, ww = win
        assert F_ % wf == 0 and H % wh == 0 and W % ww == 0, "Dims must divide by window size."
        x = x.view(B, F_ // wf, wf, H // wh, wh, W // ww, ww, C)
        x = x.permute(0, 1, 3, 5, 2, 4, 6, 7).contiguous()
        return x.view(-1, wf * wh * ww, C)

    @staticmethod
    def reverse(windows: torch.Tensor, win: Tuple[int, int, int], orig: Tuple[int, int, int]):
        F_, H, W = orig
        wf, wh, ww = win
        nf, nh, nw = F_ // wf, H // wh, W // ww
        B = windows.size(0) // (nf * nh * nw)
        x = windows.view(B, nf, nh, nw, wf, wh, ww, -1)
        x = x.permute(0, 1, 4, 2, 5, 3, 6, 7).contiguous()
        return x.view(B, F_, H, W, -1)


@torch.no_grad()
def generate_draft_block_mask(
    batch_size,
    nheads,
    seqlen,
    q_w,
    k_w,
    topk=10,
    local_attn_mask=None,
    _force_grouped=False,
):
    assert batch_size == 1, "Only batch_size=1 supported for now"
    assert local_attn_mask is not None, "local_attn_mask must be provided"
    avgpool_q = torch.mean(q_w, dim=1)
    avgpool_k = torch.mean(k_w, dim=1)
    avgpool_q = rearrange(avgpool_q, "s (h d) -> s h d", h=nheads)
    avgpool_k = rearrange(avgpool_k, "s (h d) -> s h d", h=nheads)
    q_heads = avgpool_q.permute(1, 0, 2)
    k_heads = avgpool_k.permute(1, 0, 2)
    D = avgpool_q.shape[-1]
    if nheads > 1 and (
        _force_grouped or (torch.version.hip and q_heads.shape[1] * k_heads.shape[1] > 1000000)
    ):
        masks = []
        group_size = q_w.shape[0] // seqlen
        for head in range(nheads):
            groups = []
            for group in range(seqlen):
                groups.append(
                    generate_draft_block_mask(
                        batch_size,
                        1,
                        1,
                        q_w[
                            group * group_size : (group + 1) * group_size,
                            :,
                            head * D : (head + 1) * D,
                        ],
                        k_w[..., head * D : (head + 1) * D],
                        topk=topk,
                        local_attn_mask=local_attn_mask,
                    )
                )
            masks.append(torch.cat(groups, dim=2))
        return torch.cat(masks, dim=1)
    scores = torch.einsum("hld,hmd->hlm", q_heads, k_heads) / math.sqrt(D)

    repeat_head = scores.shape[0]
    repeat_len = scores.shape[1] // local_attn_mask.shape[0]
    repeat_num = scores.shape[2] // local_attn_mask.shape[1]
    local_attn_mask = local_attn_mask.unsqueeze(1).unsqueeze(0).repeat(repeat_len, 1, repeat_num, 1)
    local_attn_mask = rearrange(local_attn_mask, "x a y b -> (x a) (y b)")
    local_attn_mask = local_attn_mask.unsqueeze(0).repeat(repeat_head, 1, 1)
    local_attn_mask = torch.where(local_attn_mask, 0.0, -float("inf"))
    scores = scores + local_attn_mask

    attn_map = torch.softmax(scores, dim=-1)
    attn_map = rearrange(attn_map, "h (it s1) s2 -> (h it) s1 s2", it=seqlen)
    loop_num, s1, s2 = attn_map.shape
    flat = attn_map.reshape(loop_num, -1)
    apply_topk = min(flat.shape[1] - 1, topk)
    thresholds = torch.topk(flat, k=apply_topk + 1, dim=1, largest=True).values[:, -1]
    thresholds = thresholds.unsqueeze(1)
    mask_new = (flat > thresholds).reshape(loop_num, s1, s2)
    mask_new = rearrange(mask_new, "(h it) s1 s2 -> h (it s1) s2", it=seqlen)
    return mask_new.unsqueeze(0).repeat(batch_size, 1, 1, 1)


def flash_attention(
    q: torch.Tensor,
    k: torch.Tensor,
    v: torch.Tensor,
    num_heads: int,
    attention_mask=None,
):
    q_h = rearrange(q, "b s (n d) -> b n s d", n=num_heads)
    k_h = rearrange(k, "b s (n d) -> b n s d", n=num_heads)
    v_h = rearrange(v, "b s (n d) -> b n s d", n=num_heads)
    if attention_mask is not None:
        if torch.version.hip:
            x = sparse_attention(q_h, k_h, v_h, attention_mask)
        elif q.device.type == "cpu":
            x = reference_attention(q_h, k_h, v_h, attention_mask)
        else:
            raise RuntimeError(
                "FlashVSR sparse attention requires AMD ROCm (torch.version.hip) or CPU reference."
            )
    else:
        x = F.scaled_dot_product_attention(q_h, k_h, v_h)
    return rearrange(x, "b n s d -> b s (n d)", n=num_heads)


def modulate(x: torch.Tensor, shift: torch.Tensor, scale: torch.Tensor):
    return x * (1 + scale) + shift


def sinusoidal_embedding_1d(dim, position):
    sinusoid = torch.outer(
        position.type(torch.float64),
        torch.pow(
            10000,
            -torch.arange(dim // 2, dtype=torch.float64, device=position.device).div(dim // 2),
        ),
    )
    x = torch.cat([torch.cos(sinusoid), torch.sin(sinusoid)], dim=1)
    return x.to(position.dtype)


def precompute_freqs_cis_3d(dim: int, end: int = 1024, theta: float = 10000.0):
    f_freqs_cis = precompute_freqs_cis(dim - 2 * (dim // 3), end, theta)
    h_freqs_cis = precompute_freqs_cis(dim // 3, end, theta)
    w_freqs_cis = precompute_freqs_cis(dim // 3, end, theta)
    return f_freqs_cis, h_freqs_cis, w_freqs_cis


def precompute_freqs_cis(dim: int, end: int = 1024, theta: float = 10000.0):
    freqs = 1.0 / (theta ** (torch.arange(0, dim, 2, device="cpu")[: (dim // 2)].double() / dim))
    freqs = torch.outer(torch.arange(end, device=freqs.device), freqs)
    return torch.polar(torch.ones_like(freqs), freqs)


def rope_apply(x, freqs, num_heads):
    x = rearrange(x, "b s (n d) -> b s n d", n=num_heads)
    x_out = torch.view_as_complex(
        x.to(torch.float64).reshape(x.shape[0], x.shape[1], x.shape[2], -1, 2)
    )
    x_out = torch.view_as_real(x_out * freqs).flatten(2)
    return x_out.to(x.dtype)


class RMSNorm(nn.Module):
    def __init__(self, dim, eps=1e-5):
        super().__init__()
        self.eps = eps
        self.weight = nn.Parameter(torch.ones(dim))

    def norm(self, x):
        return x * torch.rsqrt(x.pow(2).mean(dim=-1, keepdim=True) + self.eps)

    def forward(self, x):
        dtype = x.dtype
        return self.norm(x.float()).to(dtype) * self.weight


class AttentionModule(nn.Module):
    def __init__(self, num_heads):
        super().__init__()
        self.num_heads = num_heads

    def forward(self, q, k, v, attention_mask=None):
        return flash_attention(
            q=q, k=k, v=v, num_heads=self.num_heads, attention_mask=attention_mask
        )


class SelfAttention(nn.Module):
    def __init__(self, dim: int, num_heads: int, eps: float = 1e-6):
        super().__init__()
        self.dim = dim
        self.num_heads = num_heads
        self.head_dim = dim // num_heads

        self.q = nn.Linear(dim, dim)
        self.k = nn.Linear(dim, dim)
        self.v = nn.Linear(dim, dim)
        self.o = nn.Linear(dim, dim)
        self.norm_q = RMSNorm(dim, eps=eps)
        self.norm_k = RMSNorm(dim, eps=eps)

        self.attn = AttentionModule(self.num_heads)
        self.local_attn_mask = None
        self.local_attn_mask_h = 0
        self.local_attn_mask_w = 0
        self.local_range = 0

    def forward(
        self,
        x,
        freqs,
        f=None,
        h=None,
        w=None,
        local_num=None,
        topk=None,
        train_img=False,
        block_id=None,
        kv_len=None,
        is_full_block=False,
        is_stream=False,
        pre_cache_k=None,
        pre_cache_v=None,
        local_range=9,
    ):
        del local_num, train_img, block_id, is_full_block
        B, L, D = x.shape
        if is_stream and pre_cache_k is not None and pre_cache_v is not None:
            assert f == 2, "f must be 2"
        if is_stream and (pre_cache_k is None or pre_cache_v is None):
            assert f == 6, "start f must be 6"
        assert L == f * h * w, "Sequence length mismatch with provided (f,h,w)."

        q = self.norm_q(self.q(x))
        k = self.norm_k(self.k(x))
        v = self.v(x)
        q = rope_apply(q, freqs, self.num_heads)
        k = rope_apply(k, freqs, self.num_heads)

        win = (2, 8, 8)
        q = q.view(B, f, h, w, D)
        k = k.view(B, f, h, w, D)
        v = v.view(B, f, h, w, D)

        q_w = WindowPartition3D.partition(q, win)
        k_w = WindowPartition3D.partition(k, win)
        v_w = WindowPartition3D.partition(v, win)

        seqlen = f // win[0]
        one_len = k_w.shape[0] // B // seqlen
        if pre_cache_k is not None and pre_cache_v is not None:
            k_w = torch.cat([pre_cache_k, k_w], dim=0)
            v_w = torch.cat([pre_cache_v, v_w], dim=0)

        block_n = q_w.shape[0] // B
        block_s = q_w.shape[1]
        block_n_kv = k_w.shape[0] // B

        reorder_q = rearrange(
            q_w,
            "(b block_n) (block_s) d -> b (block_n block_s) d",
            block_n=block_n,
            block_s=block_s,
        )
        reorder_k = rearrange(
            k_w,
            "(b block_n) (block_s) d -> b (block_n block_s) d",
            block_n=block_n_kv,
            block_s=block_s,
        )
        reorder_v = rearrange(
            v_w,
            "(b block_n) (block_s) d -> b (block_n block_s) d",
            block_n=block_n_kv,
            block_s=block_s,
        )

        if (
            self.local_attn_mask is None
            or self.local_attn_mask_h != h // 8
            or self.local_attn_mask_w != w // 8
            or self.local_range != local_range
        ):
            self.local_attn_mask = build_local_block_mask_shifted_vec_normal_slide(
                h // 8,
                w // 8,
                local_range,
                local_range,
                include_self=True,
                device=k_w.device,
            )
            self.local_attn_mask_h = h // 8
            self.local_attn_mask_w = w // 8
            self.local_range = local_range
        attention_mask = generate_draft_block_mask(
            B,
            self.num_heads,
            seqlen,
            q_w,
            k_w,
            topk=topk,
            local_attn_mask=self.local_attn_mask,
        )

        x = self.attn(reorder_q, reorder_k, reorder_v, attention_mask)

        cur_block_n, _cur_block_s, _ = k_w.shape
        cache_num = cur_block_n // one_len
        if cache_num > kv_len:
            cache_k = k_w[one_len:, :, :]
            cache_v = v_w[one_len:, :, :]
        else:
            cache_k = k_w
            cache_v = v_w

        x = rearrange(
            x, "b (block_n block_s) d -> (b block_n) (block_s) d", block_n=block_n, block_s=block_s
        )
        x = WindowPartition3D.reverse(x, win, (f, h, w))
        x = x.view(B, f * h * w, D)

        if is_stream:
            return self.o(x), cache_k, cache_v
        return self.o(x)


class CrossAttention(nn.Module):
    """Text-context CrossAttention with persistent KV cache."""

    def __init__(self, dim: int, num_heads: int, eps: float = 1e-6):
        super().__init__()
        self.dim = dim
        self.num_heads = num_heads
        self.head_dim = dim // num_heads

        self.q = nn.Linear(dim, dim)
        self.k = nn.Linear(dim, dim)
        self.v = nn.Linear(dim, dim)
        self.o = nn.Linear(dim, dim)

        self.norm_q = RMSNorm(dim, eps=eps)
        self.norm_k = RMSNorm(dim, eps=eps)

        self.attn = AttentionModule(self.num_heads)

        self.cache_k: Optional[torch.Tensor] = None
        self.cache_v: Optional[torch.Tensor] = None

    @torch.no_grad()
    def init_cache(self, ctx: torch.Tensor):
        self.cache_k = self.norm_k(self.k(ctx))
        self.cache_v = self.v(ctx)

    def clear_cache(self):
        self.cache_k = None
        self.cache_v = None

    def forward(self, x: torch.Tensor, y: Optional[torch.Tensor] = None, is_stream: bool = False):
        del y, is_stream
        q = self.norm_q(self.q(x))
        assert self.cache_k is not None and self.cache_v is not None
        x = self.attn(q, self.cache_k, self.cache_v)
        return self.o(x)


class GateModule(nn.Module):
    def __init__(self):
        super().__init__()

    def forward(self, x, gate, residual):
        return x + gate * residual


class DiTBlock(nn.Module):
    def __init__(self, dim: int, num_heads: int, ffn_dim: int, eps: float = 1e-6):
        super().__init__()
        self.dim = dim
        self.num_heads = num_heads
        self.ffn_dim = ffn_dim

        self.self_attn = SelfAttention(dim, num_heads, eps)
        self.cross_attn = CrossAttention(dim, num_heads, eps)

        self.norm1 = nn.LayerNorm(dim, eps=eps, elementwise_affine=False)
        self.norm2 = nn.LayerNorm(dim, eps=eps, elementwise_affine=False)
        self.norm3 = nn.LayerNorm(dim, eps=eps)
        self.ffn = nn.Sequential(
            nn.Linear(dim, ffn_dim),
            nn.GELU(approximate="tanh"),
            nn.Linear(ffn_dim, dim),
        )
        self.modulation = nn.Parameter(torch.randn(1, 6, dim) / dim**0.5)
        self.gate = GateModule()

    def forward(
        self,
        x,
        context,
        t_mod,
        freqs,
        f,
        h,
        w,
        local_num=None,
        topk=None,
        train_img=False,
        block_id=None,
        kv_len=None,
        is_full_block=False,
        is_stream=False,
        pre_cache_k=None,
        pre_cache_v=None,
        local_range=9,
    ):
        shift_msa, scale_msa, gate_msa, shift_mlp, scale_mlp, gate_mlp = (
            self.modulation.to(dtype=t_mod.dtype, device=t_mod.device) + t_mod
        ).chunk(6, dim=1)
        input_x = modulate(self.norm1(x), shift_msa, scale_msa)
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
            is_stream=is_stream,
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


class Head(nn.Module):
    def __init__(self, dim: int, out_dim: int, patch_size: Tuple[int, int, int], eps: float):
        super().__init__()
        self.dim = dim
        self.patch_size = patch_size
        self.norm = nn.LayerNorm(dim, eps=eps, elementwise_affine=False)
        self.head = nn.Linear(dim, out_dim * math.prod(patch_size))
        self.modulation = nn.Parameter(torch.randn(1, 2, dim) / dim**0.5)

    def forward(self, x, t_mod):
        shift, scale = (self.modulation.to(dtype=t_mod.dtype, device=t_mod.device) + t_mod).chunk(
            2, dim=1
        )
        return self.head(self.norm(x) * (1 + scale) + shift)


class WanModel(torch.nn.Module):
    def __init__(
        self,
        dim: int,
        in_dim: int,
        ffn_dim: int,
        out_dim: int,
        text_dim: int,
        freq_dim: int,
        eps: float,
        patch_size: Tuple[int, int, int],
        num_heads: int,
        num_layers: int,
        has_image_input: bool = False,
    ):
        super().__init__()
        del has_image_input
        self.dim = dim
        self.freq_dim = freq_dim
        self.patch_size = patch_size

        self.patch_embedding = nn.Conv3d(in_dim, dim, kernel_size=patch_size, stride=patch_size)

        self.text_embedding = nn.Sequential(
            nn.Linear(text_dim, dim),
            nn.GELU(approximate="tanh"),
            nn.Linear(dim, dim),
        )
        self.time_embedding = nn.Sequential(
            nn.Linear(freq_dim, dim),
            nn.SiLU(),
            nn.Linear(dim, dim),
        )
        self.time_projection = nn.Sequential(nn.SiLU(), nn.Linear(dim, dim * 6))

        self.blocks = nn.ModuleList(
            [DiTBlock(dim, num_heads, ffn_dim, eps) for _ in range(num_layers)]
        )
        self.head = Head(dim, out_dim, patch_size, eps)

        head_dim = dim // num_heads
        self.freqs = precompute_freqs_cis_3d(head_dim)
        self.LQ_proj_in: Optional[nn.Module] = None
        self._cross_kv_initialized = False

    def clear_cross_kv(self):
        for blk in self.blocks:
            blk.cross_attn.clear_cache()
        self._cross_kv_initialized = False

    @torch.no_grad()
    def reinit_cross_kv(self, new_context: torch.Tensor):
        ctx_txt = self.text_embedding(new_context)
        for blk in self.blocks:
            blk.cross_attn.init_cache(ctx_txt)
        self._cross_kv_initialized = True

    def patchify(self, x: torch.Tensor):
        x = self.patch_embedding(x)
        grid_size = x.shape[2:]
        x = rearrange(x, "b c f h w -> b (f h w) c").contiguous()
        return x, grid_size

    def unpatchify(self, x: torch.Tensor, grid_size: Tuple[int, int, int]):
        return rearrange(
            x,
            "b (f h w) (x y z c) -> b c (f x) (h y) (w z)",
            f=grid_size[0],
            h=grid_size[1],
            w=grid_size[2],
            x=self.patch_size[0],
            y=self.patch_size[1],
            z=self.patch_size[2],
        )
