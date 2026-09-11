# SeedVR2 vendored code — attribution

The `vendor/` directory contains the SeedVR2 inference implementation from
https://github.com/numz/ComfyUI-SeedVR2_VideoUpscaler (commit
`4490bd1f482e026674543386bb2a4d176da245b9`, version 2.5.24), which builds on
SeedVR2 by ByteDance (https://github.com/ByteDance-Seed/SeedVR,
https://huggingface.co/ByteDance-Seed/SeedVR2-3B). Portions of the video VAE
derive from the HuggingFace diffusers library.

- License: Apache License 2.0 for the code and the model weights. A copy of
  the license ships in `vendor/LICENSE`. Per-file copyright headers
  (ByteDance Ltd., HuggingFace Team) are retained unmodified.
- Modifications by LocalSR are marked in-file with a
  "Modified for LocalSR" comment (currently: `core/alpha_upscaling.py`,
  where OpenCV is imported lazily so RGB-only installs do not require it, and
  `core/generation_utils.py`, where the fixed text embeddings load from Safetensors,
  `utils/downloads.py`, where model downloads are restricted to HTTPS, and
  `utils/debug.py`, where diagnostics go to stderr to preserve the JSON-lines protocol,
  and `models/video_vae_v3/modules/attn_video_vae.py`, where optional preview callbacks
  report the actual encoding/decoding regions without modifying model tensors).
- The ComfyUI interface layer (`src/interfaces/`) of the upstream project is
  not vendored. `pos_emb.safetensors` / `neg_emb.safetensors` are byte-for-byte
  tensor conversions of the precomputed text conditioning embeddings from the
  ByteDance release; their source `.pt` SHA-256 values are retained in Safetensors metadata.

Credits: SeedVR2 (ByteDance Seed team); ComfyUI-SeedVR2_VideoUpscaler
implementation and MPS support (numz and contributors; AInVFX).
