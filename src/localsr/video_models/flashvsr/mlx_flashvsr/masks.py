# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Hermes Reisner. Part of the MLX port of FlashVSR (https://github.com/OpenImagingLab/FlashVSR).

import math
from typing import Optional, Tuple

import mlx.core as mx


def build_local_block_mask_shifted_vec(
    block_h: int,
    block_w: int,
    win_h: int = 6,
    win_w: int = 6,
    include_self: bool = True,
) -> mx.array:
    H, W = block_h, block_w
    r = mx.arange(H)
    c = mx.arange(W)
    YY, XX = mx.meshgrid(r, c, indexing="ij")
    r_all = YY.reshape(-1)
    c_all = XX.reshape(-1)
    r_half = win_h // 2
    c_half = win_w // 2
    start_r = mx.clip(r_all - r_half, 0, H - win_h)
    end_r = start_r + win_h - 1
    start_c = mx.clip(c_all - c_half, 0, W - win_w)
    end_c = start_c + win_w - 1
    in_row = (r_all[None, :] >= start_r[:, None]) & (r_all[None, :] <= end_r[:, None])
    in_col = (c_all[None, :] >= start_c[:, None]) & (c_all[None, :] <= end_c[:, None])
    mask = in_row & in_col
    if not include_self:
        mask = mask & ~mx.eye(H * W, dtype=mx.bool_)
    return mask


def build_local_block_mask_shifted_vec_normal_slide(
    block_h: int,
    block_w: int,
    win_h: int = 6,
    win_w: int = 6,
    include_self: bool = True,
) -> mx.array:
    H, W = block_h, block_w
    r = mx.arange(H)
    c = mx.arange(W)
    YY, XX = mx.meshgrid(r, c, indexing="ij")
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
        mask = mask & ~mx.eye(H * W, dtype=mx.bool_)
    return mask


class WindowPartition3D:
    """Partition / reverse-partition helpers for 5-D tensors (B, F, H, W, C)."""

    @staticmethod
    def partition(x: mx.array, win: Tuple[int, int, int]) -> mx.array:
        B, F, H, W, C = x.shape
        wf, wh, ww = win
        assert F % wf == 0 and H % wh == 0 and W % ww == 0, "Dims must divide by window size."
        x = x.reshape(B, F // wf, wf, H // wh, wh, W // ww, ww, C)
        x = x.transpose(0, 1, 3, 5, 2, 4, 6, 7)
        return x.reshape(-1, wf * wh * ww, C)

    @staticmethod
    def reverse(
        windows: mx.array, win: Tuple[int, int, int], orig: Tuple[int, int, int]
    ) -> mx.array:
        F, H, W = orig
        wf, wh, ww = win
        nf, nh, nw = F // wf, H // wh, W // ww
        B = windows.shape[0] // (nf * nh * nw)
        x = windows.reshape(B, nf, nh, nw, wf, wh, ww, -1)
        x = x.transpose(0, 1, 4, 2, 5, 3, 6, 7)
        return x.reshape(B, F, H, W, -1)


def generate_draft_block_mask(
    batch_size: int,
    nheads: int,
    seqlen: int,
    q_w: mx.array,
    k_w: mx.array,
    topk: int = 10,
    local_attn_mask: Optional[mx.array] = None,
    _force_grouped: bool = False,
) -> mx.array:
    assert batch_size == 1, "Only batch_size=1 supported for now"
    assert local_attn_mask is not None, "local_attn_mask must be provided"

    avgpool_q = mx.mean(q_w, axis=1)
    avgpool_k = mx.mean(k_w, axis=1)
    D = avgpool_q.shape[-1] // nheads
    q_heads = avgpool_q.reshape(avgpool_q.shape[0], nheads, D).transpose(1, 0, 2)
    k_heads = avgpool_k.reshape(avgpool_k.shape[0], nheads, D).transpose(1, 0, 2)

    if nheads > 1 and (_force_grouped or q_heads.shape[1] * k_heads.shape[1] > 1_000_000):
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
            masks.append(mx.concatenate(groups, axis=2))
        return mx.concatenate(masks, axis=1)

    scores = mx.matmul(q_heads, k_heads.transpose(0, 2, 1)) / math.sqrt(D)

    repeat_head = scores.shape[0]
    repeat_len = scores.shape[1] // local_attn_mask.shape[0]
    repeat_num = scores.shape[2] // local_attn_mask.shape[1]
    tiled_mask = mx.tile(local_attn_mask.astype(mx.bool_), (repeat_len, repeat_num))
    tiled_mask = mx.broadcast_to(
        tiled_mask[None, :, :], (repeat_head, tiled_mask.shape[0], tiled_mask.shape[1])
    )

    scores = mx.where(tiled_mask, scores.astype(mx.float32), -mx.inf)
    attn_map = mx.softmax(scores, axis=-1)

    s1 = attn_map.shape[1] // seqlen
    s2 = attn_map.shape[2]
    attn_map = attn_map.reshape(repeat_head * seqlen, s1, s2)
    loop_num = repeat_head * seqlen
    flat = attn_map.reshape(loop_num, -1)

    apply_topk = min(flat.shape[1] - 1, topk)
    # mx.topk returns values in ascending order; take min across the top (apply_topk + 1) elements.
    top_vals = mx.topk(flat, k=apply_topk + 1, axis=1)
    thresholds = mx.min(top_vals, axis=1, keepdims=True)
    mask_new = (flat > thresholds).reshape(repeat_head, seqlen * s1, s2)
    mask = mx.broadcast_to(mask_new[None, ...], (batch_size, repeat_head, seqlen * s1, s2))
    return mask


def generate_causal_block_mask(
    batch_size: int,
    nheads: int,
    seqlen: int,
    local_num: int,
    window_size: int,
    train_img: bool = False,
) -> mx.array:
    i = mx.arange(seqlen)[:, None]
    j = mx.arange(seqlen)[None, :]
    causal = (j <= i) & (j >= i - local_num + 1)
    # Set causal[0, 1] = True and causal[:2, 2] = True when dimensions permit.
    rows = mx.arange(seqlen)[:, None]
    cols = mx.arange(seqlen)[None, :]
    if seqlen > 1:
        causal = causal | ((rows == 0) & (cols == 1))
    if seqlen > 2:
        causal = causal | ((rows < 2) & (cols == 2))
    if train_img and seqlen > 1:
        causal = causal & ~((rows == seqlen - 1) & (cols < seqlen - 1))

    causal = mx.broadcast_to(
        causal[:, None, :, None], (seqlen, window_size, seqlen, window_size)
    ).reshape(seqlen * window_size, seqlen * window_size)
    return mx.broadcast_to(
        causal[None, None, :, :],
        (batch_size, nheads, seqlen * window_size, seqlen * window_size),
    )
