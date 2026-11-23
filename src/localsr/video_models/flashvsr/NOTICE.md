# FlashVSR v1.1 Tiny (MLX and PyTorch/ROCm ports) — attribution

`mlx_flashvsr/` is vendored from `flashvsr-mlx` (branch `mlx`, commit
`752cb90`, by Hermes Reisner), an MLX port of FlashVSR
(https://github.com/OpenImagingLab/FlashVSR).

`torch_flashvsr/` is vendored from `flashvsr-mlx` (branch `main`, commit
`12b3b1a`, by Hermes Reisner), which combines upstream OpenImagingLab/FlashVSR
with upstream PR #114 (`8a95fd3`, "ROCm LCSA inference with PyTorch
FlexAttention" by flowerinheart) and 16 GB ROCm memory fixes.

## Licenses per part

- **FlashVSR code & MLX/PyTorch ports (`mlx_flashvsr/`, `torch_flashvsr/`)**:
  Apache License 2.0. A copy of the license ships in `LICENSE`, and per-file
  `SPDX-License-Identifier` and copyright headers are retained unmodified.
- **TCDecoder (`mlx_flashvsr/tcdecoder.py`, `torch_flashvsr/tcdecoder.py`)**:
  adapted from `TAEHV` by Ollin Boer Bohan under the MIT License
  (`LICENSES/TAEHV-MIT.txt`).
- **Block-sparse Metal attention kernel (`mlx_flashvsr/block_sparse_attn.py`)**:
  MMA fragment/tile helpers and online-softmax structure adapted from MLX's
  steel attention kernels (Copyright (c) 2024-2026 Apple Inc., MIT License,
  `LICENSES/MLX-MIT.txt`).
- **Model weights (not bundled)**: downloaded from
  `https://huggingface.co/JunhaoZhuang/FlashVSR-v1.1` at revision
  `2aa7e1f3ab50e743537d1eca243fb0bff442b336` under the Apache License 2.0
  (`diffusion_pytorch_model_streaming_dmd.safetensors`, `LQ_proj_in.ckpt`,
  `TCDecoder.ckpt`).

## Modifications for LocalSR

- `mlx_flashvsr/pipeline.py`: `_DEFAULT_PROMPT_PATH` points to
  `posi_prompt.safetensors` alongside `mlx_flashvsr/`, and `init_cross_kv` loads
  the Safetensors file via `mx.load` so no pickle prompt file is loaded at
  runtime.
- `mlx_flashvsr/`: formatted with `ruff` to match the repository's lint and
  formatting checks (marked where changed; logic unchanged).
- `torch_flashvsr/`: vendored minimal Tiny-only PyTorch pipeline from
  `flashvsr-mlx` commit `12b3b1a`:
  - `rocm_attention.py`: from `diffsynth/models/rocm_attention.py` (PR #114
    `8a95fd3`), unchanged logic, formatted for `ruff`.
  - `wan_video_dit.py`: from `diffsynth/models/wan_video_dit.py`; pruned CUDA
    `block_sparse_attn`, `flash_attn`, `sageattention`, training `forward`,
    unused I2V `MLP`, and `WanModelStateDictConverter`; routes CPU sparse
    attention to `reference_attention` and ROCm (`torch.version.hip`) to
    `sparse_attention`.
  - `flow_match.py`: from `diffsynth/schedulers/flow_match.py`; pruned unused
    training/step methods.
  - `lq_proj.py`: from `examples/WanVSR/utils/utils.py`; kept `Causal_LQ4x_Proj`
    and its layers, pruned `Buffer_LQ4x_Proj`.
  - `tcdecoder.py`: from `examples/WanVSR/utils/TCDecoder.py`; kept `TAEHV`
    (with `set_rocm_cache_release`) and `build_tcdecoder`, pruned unused
    Diffusers wrapper and stdout print.
  - `pipeline.py`: from `diffsynth/pipelines/flashvsr_tiny.py` and
    `examples/WanVSR/infer_flashvsr_v1.1_tiny.py`; self-contained without
    `diffsynth`, `ModelManager`, `WanVideoVAE`, `TeaCache`, or USP; loads DiT
    weights via `safetensors`, `LQ_proj_in.ckpt` / `TCDecoder.ckpt` via
    `torch.load(..., weights_only=True)`, and `posi_prompt.safetensors` via
    `safetensors.torch.load_file`; retains pre-decode KV-cache/LQ release and
    enables `TCDecoder.set_rocm_cache_release(True)` on ROCm; reports progress
    via `progress_callback` instead of `tqdm` or banner prints.
- `posi_prompt.safetensors`: byte-for-byte `bfloat16` `(1, 512, 4096)` tensor
  conversion of `examples/WanVSR/prompt_tensor/posi_prompt.pth` (loaded once
  with `torch.load(..., weights_only=True)`).
  - Source `posi_prompt.pth` SHA-256:
    `4601107a11e4e11a936a6b79df579e54dbc99872132bf542151f0ffd65b4b1ef`
  - `posi_prompt.safetensors` SHA-256:
    `2996eb2cc096d783675abe496464a3fffe50ce9612154a79a5a412b10b952f8d`
