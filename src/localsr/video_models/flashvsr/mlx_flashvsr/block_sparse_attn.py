# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Hermes Reisner. Part of the MLX port of FlashVSR (https://github.com/OpenImagingLab/FlashVSR).
#
# The Metal MMA fragment/tile helpers and online-softmax structure in _METAL_HEADER /
# _METAL_SOURCE are adapted from MLX's steel attention kernels (MIT License,
# Copyright (c) 2024-2026 Apple Inc., mlx/backend/metal/kernels/steel/attn/; text in LICENSES/MLX-MIT.txt).

from typing import Tuple

import mlx.core as mx

_BLOCK_SIZE = 128
_HEAD_DIM = 128
_BQ = 32
_BK = 16
_WM = 4
_THREADGROUP_SIZE = _WM * 32  # 128 threads (4 simdgroups)

_METAL_HEADER = r"""
// Adapted from MLX steel attention (Copyright (c) 2024-2026 Apple Inc., MIT License).
#include <metal_stdlib>
#include <metal_simdgroup>
#include <metal_simdgroup_matrix>

using namespace metal;

#define BS_CONST static constant constexpr const
#define BS_UNROLL _Pragma("clang loop unroll(full)")

template <typename T>
struct BaseMMAFrag8x8 {
  typedef metal::simdgroup_matrix<T, 8, 8> mat_type;
  typedef metal::vec<T, 2> frag_type;

  template <typename U>
  using dtype_mat_t = typename metal::simdgroup_matrix<U, 8, 8>;
  template <typename U>
  using dtype_frag_t = typename metal::vec<U, 2>;

  static inline short2 get_coord(ushort simd_lane_id) {
    const short qid = simd_lane_id / 4;
    const short fm = (qid & 4) + ((simd_lane_id / 2) % 4);
    const short fn = (qid & 2) * 2 + (simd_lane_id % 2) * 2;
    return short2{fn, fm};
  }

  template <typename SrcPtrType>
  static inline void load(thread frag_type& dst, SrcPtrType src, int str_y) {
    dst[0] = static_cast<T>(src[0]);
    dst[1] = static_cast<T>(src[str_y]);
  }

  template <typename Atype, typename Btype, typename Ctype>
  static inline void mma(
      thread frag_type& D,
      thread dtype_frag_t<Atype>& A,
      thread dtype_frag_t<Btype>& B,
      thread dtype_frag_t<Ctype>& C) {
    mat_type D_mat;
    dtype_mat_t<Atype> A_mat;
    dtype_mat_t<Btype> B_mat;
    dtype_mat_t<Ctype> C_mat;

    reinterpret_cast<thread dtype_frag_t<Atype>&>(A_mat.thread_elements()) = A;
    reinterpret_cast<thread dtype_frag_t<Btype>&>(B_mat.thread_elements()) = B;
    reinterpret_cast<thread dtype_frag_t<Ctype>&>(C_mat.thread_elements()) = C;

    metal::simdgroup_multiply_accumulate(D_mat, A_mat, B_mat, C_mat);

    D = reinterpret_cast<thread frag_type&>(D_mat.thread_elements());
  }

  static inline void row_max(thread const frag_type& inp, thread T* out) {
    T t = metal::max(inp.x, inp.y);
    T q = metal::simd_shuffle_xor(t, ushort(1));
    q = metal::max(t, q);
    T s = metal::simd_shuffle_xor(q, ushort(8));
    s = metal::max(q, s);
    out[0] = metal::max(out[0], s);
  }

  static inline void row_sum(thread const frag_type& inp, thread T* out) {
    T t = inp.x + inp.y;
    T q = metal::simd_shuffle_xor(t, ushort(1));
    q = t + q;
    T s = metal::simd_shuffle_xor(q, ushort(8));
    s = q + s;
    out[0] = out[0] + s;
  }
};

template <typename T, int kTileRows_, int kTileCols_>
struct MMATile8x8 {
  using Frag = BaseMMAFrag8x8<T>;
  typedef typename Frag::frag_type frag_type;
  BS_CONST int kTileRows = kTileRows_;
  BS_CONST int kTileCols = kTileCols_;
  BS_CONST int kNumFrags = kTileRows * kTileCols;
  BS_CONST int kElemsPerTile = kNumFrags * 2;

  frag_type val_frags[kNumFrags];

  inline void clear() thread {
    BS_UNROLL
    for (short i = 0; i < kNumFrags; ++i) {
      val_frags[i] = frag_type(0);
    }
  }

  inline thread frag_type& frag_at(short i, short j) thread {
    return val_frags[i * kTileCols + j];
  }

  inline thread T* elems() thread {
    return reinterpret_cast<thread T*>(val_frags);
  }

  template <typename U, int str_x, int str_y>
  inline void load_tgp(const threadgroup U* src) thread {
    BS_UNROLL
    for (short i = 0; i < kTileRows; ++i) {
      BS_UNROLL
      for (short j = 0; j < kTileCols; ++j) {
        Frag::load(
            frag_at(i, j),
            &src[(i * 8) * str_x + (j * 8) * str_y],
            str_y);
      }
    }
  }
};
"""

_METAL_SOURCE = f"""
// Adapted from MLX steel attention (Copyright (c) 2024-2026 Apple Inc., MIT License).
constexpr short BQ = {_BQ};
constexpr short BK = {_BK};
constexpr short BD = {_HEAD_DIM};
constexpr short WM = {_WM};
constexpr short TGP_SIZE = {_THREADGROUP_SIZE};
constexpr short TQ = BQ / (WM * 8);
constexpr short TK = BK / 8;
constexpr short TD = BD / 8;
constexpr short padQ = 8;
constexpr short padKV = 8;
constexpr short LDQ_tgp = BD + padQ;
constexpr short LDKV_tgp = BD + padKV;

// 16-byte aligned threadgroup buffers: (32 * 136 + 16 * 136) * 2 B = 13,056 B (< 16 KB, <= 2 TGs/core on Apple7).
threadgroup uint4 Q_smem_u4[(BQ * LDQ_tgp) / 8];
threadgroup uint4 KV_smem_u4[(BK * LDKV_tgp) / 8];
threadgroup T* Q_smem = reinterpret_cast<threadgroup T*>(Q_smem_u4);
threadgroup T* KV_smem = reinterpret_cast<threadgroup T*>(KV_smem_u4);

const uint simd_lane_id = thread_index_in_simdgroup;
const uint simd_group_id = simdgroup_index_in_threadgroup;
const uint lid_flat = simd_group_id * 32 + simd_lane_id;

const int q_tile_idx = int(threadgroup_position_in_grid.x);
const int bh_idx = int(threadgroup_position_in_grid.y);

const int n_qb = q_shape[2] / {_BLOCK_SIZE};
const int n_kb = k_shape[2] / {_BLOCK_SIZE};
const int l_q = q_shape[2];
const int l_kv = k_shape[2];

const int q_row_start = q_tile_idx * BQ;
const int qb = q_row_start / {_BLOCK_SIZE};

const int sel_row_idx = bh_idx * n_qb + qb;
const int count = counts[sel_row_idx];

const size_t qo_head_off = size_t(bh_idx) * size_t(l_q) * size_t(BD);
device T* O_tile = out + qo_head_off + size_t(q_row_start) * size_t(BD);

if (count <= 0) {{
  for (int idx = int(lid_flat); idx < BQ * BD; idx += TGP_SIZE) {{
    O_tile[idx] = T(0);
  }}
  return;
}}

const device T* Q_tile = q + qo_head_off + size_t(q_row_start) * size_t(BD);
const device T* K_head = k + size_t(bh_idx) * size_t(l_kv) * size_t(BD);
const device T* V_head = v + size_t(bh_idx) * size_t(l_kv) * size_t(BD);
const int sel_base = sel_row_idx * n_kb;

constexpr int vecs_per_row = BD / 8;
constexpr int q_total_vecs = BQ * vecs_per_row;
for (int v_idx = int(lid_flat); v_idx < q_total_vecs; v_idx += TGP_SIZE) {{
  int r = v_idx / vecs_per_row;
  int c8 = (v_idx % vecs_per_row) * 8;
  Q_smem_u4[(r * LDQ_tgp + c8) / 8] = *((const device uint4*)(&Q_tile[r * BD + c8]));
}}

using FragAcc = BaseMMAFrag8x8<float>;
MMATile8x8<T, TQ, 1> Qtile;
MMATile8x8<T, 1, TK> Ktile;
MMATile8x8<float, TQ, TK> Stile;
MMATile8x8<T, 1, 1> Vtile;
MMATile8x8<float, TQ, TD> Otile;
Otile.clear();

const short2 sc = FragAcc::get_coord(ushort(simd_lane_id));
const short sm = sc.y;
const short sn = sc.x;
const short tm = short(simd_group_id) * (8 * TQ);

const short Qs_offset = (tm + sm) * LDQ_tgp + sn;
const short Ks_offset = sn * LDKV_tgp + sm;
const short Vs_offset = sm * LDKV_tgp + sn;

float max_score[TQ];
float sum_score[TQ];
BS_UNROLL
for (short i = 0; i < TQ; ++i) {{
  max_score[i] = -FLT_MAX;
  sum_score[i] = 0.0f;
}}

// 1 / sqrt(128) * log2(e)
const float scale_log2e = 0.08838834764831845f * M_LOG2E_F;
constexpr int n_sub = {_BLOCK_SIZE} / BK;
constexpr int kv_total_vecs = BK * vecs_per_row;

for (int i_blk = 0; i_blk < count; ++i_blk) {{
  const int kb = selected_kb[sel_base + i_blk];
  const device T* K_blk = K_head + size_t(kb) * {_BLOCK_SIZE} * size_t(BD);
  const device T* V_blk = V_head + size_t(kb) * {_BLOCK_SIZE} * size_t(BD);

  for (int sub = 0; sub < n_sub; ++sub) {{
    const bool is_first = (i_blk == 0) && (sub == 0);
    const device T* K_sub = K_blk + size_t(sub * BK) * size_t(BD);
    const device T* V_sub = V_blk + size_t(sub * BK) * size_t(BD);

    threadgroup_barrier(mem_flags::mem_threadgroup);
    for (int v_idx = int(lid_flat); v_idx < kv_total_vecs; v_idx += TGP_SIZE) {{
      int kr = v_idx / vecs_per_row;
      int kc = (v_idx % vecs_per_row) * 8;
      KV_smem_u4[(kr * LDKV_tgp + kc) / 8] = *((const device uint4*)(&K_sub[kr * BD + kc]));
    }}

    Stile.clear();
    threadgroup_barrier(mem_flags::mem_threadgroup);

    BS_UNROLL
    for (short dd = 0; dd < TD; ++dd) {{
      simdgroup_barrier(mem_flags::mem_none);
      Qtile.template load_tgp<T, LDQ_tgp, 1>(&Q_smem[Qs_offset + dd * 8]);
      BS_UNROLL
      for (short ik = 0; ik < TK; ++ik) {{
        BaseMMAFrag8x8<T>::load(
            Ktile.frag_at(0, ik),
            &KV_smem[Ks_offset + ik * 8 * LDKV_tgp + dd * 8],
            LDKV_tgp);
      }}
      simdgroup_barrier(mem_flags::mem_none);

      BS_UNROLL
      for (short iq = 0; iq < TQ; ++iq) {{
        BS_UNROLL
        for (short ik = 0; ik < TK; ++ik) {{
          FragAcc::mma(
              Stile.frag_at(iq, ik),
              Qtile.frag_at(iq, 0),
              Ktile.frag_at(0, ik),
              Stile.frag_at(iq, ik));
        }}
      }}
    }}

    BS_UNROLL
    for (short ii = 0; ii < MMATile8x8<float, TQ, TK>::kElemsPerTile; ++ii) {{
      Stile.elems()[ii] *= scale_log2e;
    }}

    threadgroup_barrier(mem_flags::mem_threadgroup);
    for (int v_idx = int(lid_flat); v_idx < kv_total_vecs; v_idx += TGP_SIZE) {{
      int vr = v_idx / vecs_per_row;
      int vc = (v_idx % vecs_per_row) * 8;
      KV_smem_u4[(vr * LDKV_tgp + vc) / 8] = *((const device uint4*)(&V_sub[vr * BD + vc]));
    }}

    float new_max[TQ];
    float factor[TQ];
    BS_UNROLL
    for (short iq = 0; iq < TQ; ++iq) {{
      new_max[iq] = is_first ? -FLT_MAX : max_score[iq];
      BS_UNROLL
      for (short ik = 0; ik < TK; ++ik) {{
        FragAcc::row_max(Stile.frag_at(iq, ik), &new_max[iq]);
      }}
      factor[iq] = is_first ? 0.0f : fast::exp2(max_score[iq] - new_max[iq]);
      max_score[iq] = new_max[iq];

      float sum_tmp = 0.0f;
      BS_UNROLL
      for (short ik = 0; ik < TK; ++ik) {{
        Stile.frag_at(iq, ik)[0] = fast::exp2(Stile.frag_at(iq, ik)[0] - new_max[iq]);
        Stile.frag_at(iq, ik)[1] = fast::exp2(Stile.frag_at(iq, ik)[1] - new_max[iq]);
        FragAcc::row_sum(Stile.frag_at(iq, ik), &sum_tmp);
      }}
      sum_score[iq] = sum_score[iq] * factor[iq] + sum_tmp;

      BS_UNROLL
      for (short id = 0; id < TD; ++id) {{
        Otile.frag_at(iq, id)[0] *= factor[iq];
        Otile.frag_at(iq, id)[1] *= factor[iq];
      }}
    }}

    threadgroup_barrier(mem_flags::mem_threadgroup);

    BS_UNROLL
    for (short iq = 0; iq < TQ; ++iq) {{
      BS_UNROLL
      for (short id = 0; id < TD; ++id) {{
        BS_UNROLL
        for (short ik = 0; ik < TK; ++ik) {{
          simdgroup_barrier(mem_flags::mem_none);
          Vtile.template load_tgp<T, LDKV_tgp, 1>(&KV_smem[Vs_offset + ik * 8 * LDKV_tgp + id * 8]);
          simdgroup_barrier(mem_flags::mem_none);
          FragAcc::mma(
              Otile.frag_at(iq, id),
              Stile.frag_at(iq, ik),
              Vtile.frag_at(0, 0),
              Otile.frag_at(iq, id));
        }}
      }}
    }}
  }}
}}

BS_UNROLL
for (short iq = 0; iq < TQ; ++iq) {{
  float inv_sum = (sum_score[iq] > 0.0f) ? (1.0f / sum_score[iq]) : 0.0f;
  int row = tm + sm + iq * 8;
  BS_UNROLL
  for (short id = 0; id < TD; ++id) {{
    int col = sn + id * 8;
    float2 val = Otile.frag_at(iq, id) * inv_sum;
    O_tile[row * BD + col] = static_cast<T>(val.x);
    O_tile[row * BD + col + 1] = static_cast<T>(val.y);
  }}
}}
"""

_KERNEL = None


def _get_kernel():
    global _KERNEL
    if _KERNEL is None:
        _KERNEL = mx.fast.metal_kernel(
            name="flashvsr_block_sparse_attn",
            input_names=["q", "k", "v", "selected_kb", "counts"],
            output_names=["out"],
            header=_METAL_HEADER,
            source=_METAL_SOURCE,
            ensure_row_contiguous=True,
        )
    return _KERNEL


def compact_block_mask(mask: mx.array) -> Tuple[mx.array, mx.array]:
    """Pack selected kv-block indices per (b, h, q_block) row using MLX ops.

    Args:
        mask: Boolean block mask of shape [B, H, n_qb, n_kb].

    Returns:
        (selected_kb, counts) where selected_kb has shape [B, H, n_qb, n_kb] (int32)
        with active kv-block indices packed at [0 : count], and counts has shape
        [B, H, n_qb] (int32).
    """
    b, h, n_qb, n_kb = mask.shape
    n_rows = b * h * n_qb
    if n_rows == 0 or n_kb == 0:
        return (
            mx.zeros((b, h, n_qb, n_kb), dtype=mx.int32),
            mx.zeros((b, h, n_qb), dtype=mx.int32),
        )
    m2 = mask.reshape(n_rows, n_kb)
    cs = mx.cumsum(m2.astype(mx.int32), axis=-1)
    counts = cs[:, -1].reshape(b, h, n_qb)
    pos = mx.where(m2, cs - 1, n_kb)
    buf = mx.zeros((n_rows, n_kb + 1), dtype=mx.int32)
    kb_idx = mx.broadcast_to(mx.arange(n_kb, dtype=mx.int32)[None, :], (n_rows, n_kb))
    selected = mx.put_along_axis(buf, pos, kb_idx, axis=-1)[:, :n_kb]
    return selected.reshape(b, h, n_qb, n_kb), counts


def block_sparse_attention_metal(
    q: mx.array,
    k: mx.array,
    v: mx.array,
    mask: mx.array,
) -> mx.array:
    """Metal block-sparse flash attention for 128x128 blocks and head_dim=128.

    Args:
        q: Query tensor [B, H, L_q, 128] in bfloat16 or float16 (L_q % 128 == 0).
        k: Key tensor [B, H, L_kv, 128] matching q.dtype (L_kv % 128 == 0).
        v: Value tensor [B, H, L_kv, 128] matching q.dtype.
        mask: Boolean block mask [B, H, L_q // 128, L_kv // 128].
    """
    assert q.ndim == 4 and k.ndim == 4 and v.ndim == 4, "q, k, v must be 4D [B, H, L, D]"
    b, h, l_q, d = q.shape
    l_kv = k.shape[2]
    assert k.shape[:2] == (b, h) and v.shape[:2] == (b, h), "Batch/head mismatch between q, k, v"
    assert v.shape[2] == l_kv, "k and v sequence lengths must match"
    assert d == _HEAD_DIM and k.shape[3] == _HEAD_DIM and v.shape[3] == _HEAD_DIM, (
        f"Metal block-sparse kernel requires head_dim={_HEAD_DIM}, got {(d, k.shape[3], v.shape[3])}"
    )
    assert l_q % _BLOCK_SIZE == 0 and l_kv % _BLOCK_SIZE == 0, (
        f"L_q ({l_q}) and L_kv ({l_kv}) must be multiples of {_BLOCK_SIZE}"
    )
    assert q.dtype in (mx.bfloat16, mx.float16), (
        f"Metal block-sparse kernel requires bfloat16 or float16, got {q.dtype}"
    )
    assert k.dtype == q.dtype and v.dtype == q.dtype, "q, k, v dtypes must match"

    n_qb = l_q // _BLOCK_SIZE
    n_kb = l_kv // _BLOCK_SIZE
    expected = (b, h, n_qb, n_kb)
    if mask.shape != expected or mask.dtype != mx.bool_:
        raise ValueError(f"Expected boolean block mask {expected}, got {mask.shape} {mask.dtype}")

    if b == 0 or h == 0 or l_q == 0 or l_kv == 0:
        return mx.zeros_like(q)

    selected_kb, counts = compact_block_mask(mask)
    n_q_tiles = l_q // _BQ
    kernel = _get_kernel()
    return kernel(
        inputs=[q, k, v, selected_kb, counts],
        template=[("T", q.dtype)],
        grid=(n_q_tiles * _THREADGROUP_SIZE, b * h, 1),
        threadgroup=(_THREADGROUP_SIZE, 1, 1),
        output_shapes=[q.shape],
        output_dtypes=[q.dtype],
    )[0]
