# FlashVSR v1.1 Tiny (MLX port) — attribution

`mlx_flashvsr/` is vendored from `flashvsr-mlx` (branch `mlx`, commit
`752cb90`, by Hermes Reisner), an MLX port of FlashVSR
(https://github.com/OpenImagingLab/FlashVSR).

## Licenses per part

- **FlashVSR code & MLX port (`mlx_flashvsr/`)**: Apache License 2.0. A copy of
  the license ships in `LICENSE`, and per-file `SPDX-License-Identifier` and
  copyright headers are retained unmodified.
- **TCDecoder (`mlx_flashvsr/tcdecoder.py`)**: adapted from `TAEHV` by Ollin
  Boer Bohan under the MIT License (`LICENSES/TAEHV-MIT.txt`).
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
- `posi_prompt.safetensors`: byte-for-byte `bfloat16` `(1, 512, 4096)` tensor
  conversion of `examples/WanVSR/prompt_tensor/posi_prompt.pth` (loaded once
  with `torch.load(..., weights_only=True)`).
  - Source `posi_prompt.pth` SHA-256:
    `4601107a11e4e11a936a6b79df579e54dbc99872132bf542151f0ffd65b4b1ef`
  - `posi_prompt.safetensors` SHA-256:
    `2996eb2cc096d783675abe496464a3fffe50ce9612154a79a5a412b10b952f8d`
