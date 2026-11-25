# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Hermes Reisner. Part of the MLX port of FlashVSR (https://github.com/OpenImagingLab/FlashVSR).

from typing import Optional

import mlx.core as mx
import mlx.nn as nn

from .block_sparse_attn import block_sparse_attention_metal
from .masks import (
    WindowPartition3D,
    build_local_block_mask_shifted_vec_normal_slide,
    generate_draft_block_mask,
)
from .rope import rope_apply

_BLOCK_SIZE = 128
_MAX_MASK_ELEMENTS = 64 * 1024 * 1024  # 64 MB per boolean token-mask chunk
_BLOCK_SPARSE_BACKEND = "metal"  # "metal" (default when shape/dtype supported) or "dense"


def _expand_block_mask(
    block_mask: mx.array,
    q_len: int,
    kv_len: int,
) -> mx.array:
    """Expand a (B, H, n_qb, n_kb) block mask to (B, H, q_len, kv_len) boolean token mask."""
    b, h, n_qb, n_kb = block_mask.shape
    expanded = mx.broadcast_to(
        block_mask[:, :, :, None, :, None],
        (b, h, n_qb, _BLOCK_SIZE, n_kb, _BLOCK_SIZE),
    ).reshape(b, h, n_qb * _BLOCK_SIZE, n_kb * _BLOCK_SIZE)
    return expanded[:, :, :q_len, :kv_len]


def _zero_empty_rows(out: mx.array, block_mask: mx.array, q_len: int) -> mx.array:
    """Zero out query rows whose block mask row has no allowed key blocks."""
    b, h, n_qb, _ = block_mask.shape
    valid_blocks = mx.any(block_mask, axis=-1)
    valid_tokens = mx.broadcast_to(valid_blocks[:, :, :, None], (b, h, n_qb, _BLOCK_SIZE)).reshape(
        b, h, n_qb * _BLOCK_SIZE
    )[:, :, :q_len]
    return mx.where(valid_tokens[..., None], out, mx.zeros((), dtype=out.dtype))


def _block_sparse_attention_dense(
    q: mx.array,
    k: mx.array,
    v: mx.array,
    mask: mx.array,
) -> mx.array:
    """Dense-mask fallback computing block-sparse attention via expanded token masks."""
    b, h, l_q, d = q.shape
    l_kv = k.shape[2]
    n_qb = (l_q + _BLOCK_SIZE - 1) // _BLOCK_SIZE
    n_kb = (l_kv + _BLOCK_SIZE - 1) // _BLOCK_SIZE
    expected = (b, h, n_qb, n_kb)
    if mask.shape != expected or mask.dtype != mx.bool_:
        raise ValueError(f"Expected boolean block mask {expected}, got {mask.shape} {mask.dtype}")

    scale = d**-0.5
    full_elements = b * h * l_q * l_kv

    if full_elements <= _MAX_MASK_ELEMENTS:
        token_mask = _expand_block_mask(mask, l_q, l_kv)
        out = mx.fast.scaled_dot_product_attention(q, k, v, scale=scale, mask=token_mask)
        return _zero_empty_rows(out, mask, l_q)

    # Chunk across heads and, if needed, query blocks to bound token-mask memory.
    head_group = max(1, _MAX_MASK_ELEMENTS // max(1, b * l_q * l_kv))
    head_outputs = []
    for h0 in range(0, h, head_group):
        h1 = min(h, h0 + head_group)
        gh = h1 - h0
        q_h = q[:, h0:h1]
        k_h = k[:, h0:h1]
        v_h = v[:, h0:h1]
        m_h = mask[:, h0:h1]

        qb_step = max(1, _MAX_MASK_ELEMENTS // max(1, b * gh * _BLOCK_SIZE * l_kv))
        if qb_step >= n_qb:
            token_mask = _expand_block_mask(m_h, l_q, l_kv)
            sub_out = mx.fast.scaled_dot_product_attention(
                q_h, k_h, v_h, scale=scale, mask=token_mask
            )
            sub_out = _zero_empty_rows(sub_out, m_h, l_q)
            mx.eval(sub_out)
            head_outputs.append(sub_out)
        else:
            q_chunks = []
            for qb0 in range(0, n_qb, qb_step):
                qb1 = min(n_qb, qb0 + qb_step)
                q_start = qb0 * _BLOCK_SIZE
                q_end = min(l_q, qb1 * _BLOCK_SIZE)
                sub_q_len = q_end - q_start
                sub_mask = m_h[:, :, qb0:qb1, :]
                token_mask = _expand_block_mask(sub_mask, sub_q_len, l_kv)
                sub_out = mx.fast.scaled_dot_product_attention(
                    q_h[:, :, q_start:q_end, :],
                    k_h,
                    v_h,
                    scale=scale,
                    mask=token_mask,
                )
                sub_out = _zero_empty_rows(sub_out, sub_mask, sub_q_len)
                mx.eval(sub_out)
                q_chunks.append(sub_out)
            head_outputs.append(mx.concatenate(q_chunks, axis=2))

    return mx.concatenate(head_outputs, axis=1)


def _supports_metal_block_sparse(q: mx.array, k: mx.array, v: mx.array) -> bool:
    return (
        q.dtype in (mx.bfloat16, mx.float16)
        and k.dtype == q.dtype
        and v.dtype == q.dtype
        and q.shape[3] == _BLOCK_SIZE
        and k.shape[3] == _BLOCK_SIZE
        and v.shape[3] == _BLOCK_SIZE
        and q.shape[2] % _BLOCK_SIZE == 0
        and k.shape[2] % _BLOCK_SIZE == 0
    )


def block_sparse_attention(
    q: mx.array,
    k: mx.array,
    v: mx.array,
    mask: mx.array,
    backend: Optional[str] = None,
) -> mx.array:
    """Block-sparse attention matching reference_attention semantics for 128x128 blocks.

    Args:
        q: Query tensor of shape [B, H, L_q, D].
        k: Key tensor of shape [B, H, L_kv, D].
        v: Value tensor of shape [B, H, L_kv, D].
        mask: Boolean block mask of shape [B, H, ceil(L_q/128), ceil(L_kv/128)].
        backend: Optional backend override ("metal" or "dense"); defaults to _BLOCK_SPARSE_BACKEND.
    """
    selected = _BLOCK_SPARSE_BACKEND if backend is None else backend
    if selected == "metal":
        if backend == "metal":
            return block_sparse_attention_metal(q, k, v, mask)
        if _supports_metal_block_sparse(q, k, v):
            return block_sparse_attention_metal(q, k, v, mask)
        return _block_sparse_attention_dense(q, k, v, mask)
    if selected == "dense":
        return _block_sparse_attention_dense(q, k, v, mask)
    raise ValueError(f"Unknown block_sparse_attention backend: {selected!r}")


def flash_attention(
    q: mx.array,
    k: mx.array,
    v: mx.array,
    num_heads: int,
    compatibility_mode: bool = False,
    attention_mask: Optional[mx.array] = None,
    return_KV: bool = False,
) -> mx.array:
    b, l_q, d_total = q.shape
    l_kv = k.shape[1]
    head_dim = d_total // num_heads

    q_h = q.reshape(b, l_q, num_heads, head_dim).transpose(0, 2, 1, 3)
    k_h = k.reshape(b, l_kv, num_heads, head_dim).transpose(0, 2, 1, 3)
    v_h = v.reshape(b, l_kv, num_heads, head_dim).transpose(0, 2, 1, 3)

    if attention_mask is not None:
        out = block_sparse_attention(q_h, k_h, v_h, attention_mask)
    else:
        out = mx.fast.scaled_dot_product_attention(q_h, k_h, v_h, scale=head_dim**-0.5)

    return out.transpose(0, 2, 1, 3).reshape(b, l_q, d_total)


class RMSNorm(nn.Module):
    def __init__(self, dim: int, eps: float = 1e-5):
        super().__init__()
        self.eps = eps
        self.weight = mx.ones((dim,))

    def norm(self, x: mx.array) -> mx.array:
        return x * mx.rsqrt(mx.mean(x * x, axis=-1, keepdims=True) + self.eps)

    def __call__(self, x: mx.array) -> mx.array:
        dtype = x.dtype
        return self.norm(x.astype(mx.float32)).astype(dtype) * self.weight


class AttentionModule(nn.Module):
    def __init__(self, num_heads: int):
        super().__init__()
        self.num_heads = num_heads

    def __call__(
        self,
        q: mx.array,
        k: mx.array,
        v: mx.array,
        attention_mask: Optional[mx.array] = None,
    ) -> mx.array:
        return flash_attention(
            q=q,
            k=k,
            v=v,
            num_heads=self.num_heads,
            attention_mask=attention_mask,
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
        self.local_attn_mask_h = None
        self.local_attn_mask_w = None
        self.local_range = None

    def __call__(
        self,
        x: mx.array,
        freqs: mx.array,
        f: Optional[int] = None,
        h: Optional[int] = None,
        w: Optional[int] = None,
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
        B, L, D = x.shape
        if is_stream and pre_cache_k is not None and pre_cache_v is not None:
            assert f == 2, "f must be 2"
        if is_stream and (pre_cache_k is None or pre_cache_v is None):
            assert f == 6, "start f must be 6"
        assert L == f * h * w, "Sequence length mismatch with provided (f, h, w)."

        q = self.norm_q(self.q(x))
        k = self.norm_k(self.k(x))
        v = self.v(x)
        q = rope_apply(q, freqs, self.num_heads)
        k = rope_apply(k, freqs, self.num_heads)

        win = (2, 8, 8)
        q = q.reshape(B, f, h, w, D)
        k = k.reshape(B, f, h, w, D)
        v = v.reshape(B, f, h, w, D)

        q_w = WindowPartition3D.partition(q, win)
        k_w = WindowPartition3D.partition(k, win)
        v_w = WindowPartition3D.partition(v, win)

        seqlen = f // win[0]
        one_len = k_w.shape[0] // B // seqlen
        if pre_cache_k is not None and pre_cache_v is not None:
            k_w = mx.concatenate([pre_cache_k, k_w], axis=0)
            v_w = mx.concatenate([pre_cache_v, v_w], axis=0)

        block_n = q_w.shape[0] // B
        block_s = q_w.shape[1]
        block_n_kv = k_w.shape[0] // B

        reorder_q = q_w.reshape(B, block_n * block_s, D)
        reorder_k = k_w.reshape(B, block_n_kv * block_s, D)
        reorder_v = v_w.reshape(B, block_n_kv * block_s, D)

        if (
            self.local_attn_mask is None
            or self.local_attn_mask_h != h // 8
            or self.local_attn_mask_w != w // 8
            or self.local_range != local_range
        ):
            self.local_attn_mask = build_local_block_mask_shifted_vec_normal_slide(
                h // 8, w // 8, local_range, local_range, include_self=True
            )
            self.local_attn_mask_h = h // 8
            self.local_attn_mask_w = w // 8
            self.local_range = local_range

        attention_mask = generate_draft_block_mask(
            B, self.num_heads, seqlen, q_w, k_w, topk=topk, local_attn_mask=self.local_attn_mask
        )

        out = self.attn(reorder_q, reorder_k, reorder_v, attention_mask)

        cur_block_n = k_w.shape[0]
        cache_num = cur_block_n // one_len
        if cache_num > kv_len:
            cache_k = k_w[one_len:, :, :]
            cache_v = v_w[one_len:, :, :]
        else:
            cache_k = k_w
            cache_v = v_w

        out = out.reshape(B * block_n, block_s, D)
        out = WindowPartition3D.reverse(out, win, (f, h, w))
        out = out.reshape(B, f * h * w, D)

        if is_stream:
            return self.o(out), cache_k, cache_v
        return self.o(out)


class CrossAttention(nn.Module):
    """Cross-attention for text context with persistent KV cache."""

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

        self.cache_k = None
        self.cache_v = None

    def init_cache(self, ctx: mx.array):
        self.cache_k = self.norm_k(self.k(ctx))
        self.cache_v = self.v(ctx)

    def clear_cache(self):
        self.cache_k = None
        self.cache_v = None

    def __call__(
        self, x: mx.array, y: Optional[mx.array] = None, is_stream: bool = False
    ) -> mx.array:
        q = self.norm_q(self.q(x))
        assert self.cache_k is not None and self.cache_v is not None
        k = self.cache_k
        v = self.cache_v

        out = self.attn(q, k, v)
        return self.o(out)
