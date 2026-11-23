# SPDX-License-Identifier: Apache-2.0
# Copyright (c) OpenImagingLab FlashVSR authors and flowerinheart (upstream PR #114).
# Modified for LocalSR: added pure-PyTorch vectorised gather attention (no Triton/compiler),
# backend dispatch via FLASHVSR_ATTENTION, and formatted for repository lint rules.
"""Block-sparse execution of the official 128-token LCSA block mask."""

import os
from functools import lru_cache

import torch
import torch.nn.functional as F

BLOCK_SIZE = 128
# Cap peak temporary gather/mask buffer per SDPA chunk so 768x1408 runs within
# a few hundred MB extra on a 16 GB GPU without Triton or FlexAttention.
GATHER_CHUNK_BYTES = 256 * 1024 * 1024


def reference_attention(q, k, v, mask):
    """Gather all allowed keys per query block before a single softmax."""
    out = torch.zeros_like(q)
    for b in range(q.shape[0]):
        for h in range(q.shape[1]):
            for row in range(mask.shape[2]):
                blocks = mask[b, h, row].nonzero().flatten()
                if blocks.numel() == 0:
                    continue
                indices = (
                    blocks[:, None] * BLOCK_SIZE + torch.arange(BLOCK_SIZE, device=q.device)
                ).flatten()
                indices = indices[indices < k.shape[2]]
                start, end = row * BLOCK_SIZE, min((row + 1) * BLOCK_SIZE, q.shape[2])
                out[b, h, start:end] = F.scaled_dot_product_attention(
                    q[b, h, start:end].float(),
                    k[b, h, indices].float(),
                    v[b, h, indices].float(),
                    dropout_p=0.0,
                ).to(q.dtype)
    return out


def gather_attention(
    q,
    k,
    v,
    mask,
    *,
    precision: str = "native",
    chunk_bytes: int = GATHER_CHUNK_BYTES,
):
    """Vectorised block-sparse attention in pure PyTorch without Triton."""
    if chunk_bytes <= 0:
        raise ValueError(f"chunk_bytes must be positive, got {chunk_bytes}")
    B, H, Sq, D = q.shape
    Sk = k.shape[2]
    if Sq == 0 or Sk == 0 or B == 0 or H == 0:
        return torch.zeros_like(q)

    n_q = (Sq + BLOCK_SIZE - 1) // BLOCK_SIZE
    n_k = (Sk + BLOCK_SIZE - 1) // BLOCK_SIZE
    pad_q = n_q * BLOCK_SIZE - Sq
    pad_k = n_k * BLOCK_SIZE - Sk
    q_pad = F.pad(q, (0, 0, 0, pad_q)) if pad_q else q
    k_pad = F.pad(k, (0, 0, 0, pad_k)) if pad_k else k
    v_pad = F.pad(v, (0, 0, 0, pad_k)) if pad_k else v

    q_blocks = q_pad.reshape(B, H, n_q, BLOCK_SIZE, D)
    k_blocks = k_pad.reshape(B, H, n_k, BLOCK_SIZE, D)
    v_blocks = v_pad.reshape(B, H, n_k, BLOCK_SIZE, D)

    out_dtype = q.dtype
    compute_dtype = torch.float32 if precision == "fp32" else out_dtype
    elem_bytes = 4 if compute_dtype == torch.float32 else q.element_size()

    R_total = B * H * n_q
    out_blocks = torch.zeros((R_total, BLOCK_SIZE, D), dtype=out_dtype, device=q.device)
    flat_mask = mask.reshape(R_total, n_k)
    counts = flat_mask.sum(dim=-1)

    r_idx = torch.arange(R_total, device=q.device)
    b_all = r_idx // (H * n_q)
    h_all = (r_idx // n_q) % H
    q_all = r_idx % n_q

    tail_len = Sk % BLOCK_SIZE
    tok_arange = torch.arange(BLOCK_SIZE, device=q.device) if tail_len else None

    start = 0
    while start < R_total:
        rem_max_k = int(counts[start:].max().item())
        if rem_max_k == 0:
            break
        per_row_bytes = (
            2 * BLOCK_SIZE * D * elem_bytes + rem_max_k * BLOCK_SIZE * (2 * D + 1) * elem_bytes
        )
        rows = max(1, min(R_total - start, chunk_bytes // max(1, per_row_bytes)))
        end = start + rows

        c_slice = counts[start:end]
        max_k = int(c_slice.max().item())
        if max_k == 0:
            start = end
            continue

        m_slice = flat_mask[start:end]
        row_nz, blk_nz = m_slice.nonzero(as_tuple=True)
        offsets = c_slice.cumsum(0) - c_slice
        slot_nz = torch.arange(row_nz.numel(), device=q.device) - offsets[row_nz]
        block_idx = torch.zeros((rows, max_k), dtype=torch.long, device=q.device)
        block_idx[row_nz, slot_nz] = blk_nz

        bh_b = b_all[start:end, None]
        bh_h = h_all[start:end, None]
        k_g = k_blocks[bh_b, bh_h, block_idx].reshape(rows, 1, max_k * BLOCK_SIZE, D)
        v_g = v_blocks[bh_b, bh_h, block_idx].reshape(rows, 1, max_k * BLOCK_SIZE, D)
        q_c = q_blocks[b_all[start:end], h_all[start:end], q_all[start:end]].unsqueeze(1)
        if compute_dtype != out_dtype:
            q_c = q_c.to(compute_dtype)
            k_g = k_g.to(compute_dtype)
            v_g = v_g.to(compute_dtype)

        all_full = tail_len == 0 and bool((c_slice == n_k).all())
        has_keys = c_slice > 0
        all_have_keys = True if all_full else bool(has_keys.all())

        if all_full:
            attn_bias = None
        else:
            slot_valid = torch.arange(max_k, device=q.device)[None, :] < c_slice[:, None]
            if tail_len:
                assert tok_arange is not None
                tok_valid = slot_valid[:, :, None] & (
                    (block_idx[:, :, None] != (n_k - 1)) | (tok_arange[None, None, :] < tail_len)
                )
            else:
                tok_valid = slot_valid[:, :, None].expand(rows, max_k, BLOCK_SIZE)
            tok_valid_flat = tok_valid.reshape(rows, max_k * BLOCK_SIZE)
            if not all_have_keys:
                # Keep one dummy key unmasked for empty rows so SDPA softmax never sees all -inf.
                tok_valid_flat = tok_valid_flat.clone()
                tok_valid_flat[~has_keys, 0] = True
            attn_bias = torch.zeros(
                (rows, 1, 1, max_k * BLOCK_SIZE), dtype=compute_dtype, device=q.device
            )
            attn_bias.masked_fill_(~tok_valid_flat[:, None, None, :], float("-inf"))

        res = F.scaled_dot_product_attention(q_c, k_g, v_g, attn_mask=attn_bias, dropout_p=0.0)
        res = res.squeeze(1)
        if not all_have_keys:
            res = torch.where(has_keys[:, None, None], res, 0.0)
        out_blocks[start:end] = res.to(out_dtype)
        start = end

    return out_blocks.reshape(B, H, n_q * BLOCK_SIZE, D)[:, :, :Sq, :].contiguous()


@lru_cache(maxsize=1)
def _compiled_attention():
    from torch.nn.attention.flex_attention import flex_attention

    return torch.compile(flex_attention, dynamic=False, fullgraph=True)


def sparse_attention(q, k, v, mask):
    expected = (
        q.shape[0],
        q.shape[1],
        (q.shape[2] + BLOCK_SIZE - 1) // BLOCK_SIZE,
        (k.shape[2] + BLOCK_SIZE - 1) // BLOCK_SIZE,
    )
    if mask.shape != expected or mask.dtype != torch.bool or mask.device != q.device:
        raise ValueError(f"Expected boolean block mask {expected} on {q.device}")

    backend = os.environ.get("FLASHVSR_ATTENTION", "gather")
    if backend not in ("gather", "flex", "reference"):
        raise ValueError("FLASHVSR_ATTENTION must be gather, flex, or reference")
    precision = os.environ.get("FLASHVSR_ROCM_PRECISION", "native")
    if precision not in ("native", "fp32"):
        raise ValueError("FLASHVSR_ROCM_PRECISION must be native or fp32")

    if backend == "reference":
        return reference_attention(q, k, v, mask)
    if backend == "gather":
        return gather_attention(q, k, v, mask, precision=precision)

    from torch.nn.attention.flex_attention import BlockMask

    counts = mask.sum(-1).to(torch.int32)
    indices = torch.argsort(mask.to(torch.int32), dim=-1, descending=True, stable=True).to(
        torch.int32
    )
    # Logical blocks are completely allowed; tail tokens remain length-bounded.
    block_mask = BlockMask.from_kv_blocks(
        torch.zeros_like(counts),
        indices,
        counts,
        indices,
        BLOCK_SIZE=BLOCK_SIZE,
        seq_lengths=(q.shape[2], k.shape[2]),
    )
    if precision == "fp32":
        out = _compiled_attention()(q.float(), k.float(), v.float(), block_mask=block_mask).to(
            q.dtype
        )
    else:
        out = _compiled_attention()(q, k, v, block_mask=block_mask)
    # Empty rows have no allowed keys and return zero, rather than NaN.
    valid = (counts > 0).repeat_interleave(BLOCK_SIZE, dim=-1)[..., : q.shape[2]]
    return torch.where(valid[..., None], out, 0)
