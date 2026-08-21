# LocalSR Video Upscaling — Decision-Grade Implementation Plan

Grounded in the four research reports plus direct reads of `src/localsr/core/video_pipeline.py` and `src/localsr/core/model_catalog.py` (2026-08-21).

---

## 1. Verdict on VOSR: DO NOT integrate for video. Defer indefinitely for image.

**VOSR is not a video model.** "VO" = Vision-Only; it is "[CVPR2026] VOSR: A Vision-Only Generative Model for **Image** Super-Resolution" (https://github.com/cswry/VOSR, https://arxiv.org/abs/2604.03225). The authors explicitly stripped the 3D/temporal design out of the Qwen video-VAE they reuse. Its inference script handles only still-image extensions — used on video it would be per-frame generative SR with *worse* flicker than LocalSR's existing pipeline, because generative models hallucinate differently each frame.

Even as an image backend, integrate-later-at-best, for four independent reasons:
- **Weights license unresolved**: HF repo (https://huggingface.co/CSWRY/VOSR) has no model card, no license tag; bundles SD2.1 VAE / Qwen VAE / DINOv2 with unverified downstream terms. Code is Apache-2.0, weights are not clearly redistributable.
- **No Apple Silicon path**: `cuda-else-cpu` only, fp32-only inference, `torch==2.5.1+cu121` + triton + bitsandbytes pins that do not install on macOS.
- **Weight**: ~2.5–3.5 GB minimum for the 0.5B one-step pipeline, plus a runtime `torch.hub` DINOv2 fetch.
- **Not Spandrel-loadable** — it would need the same custom-adapter machinery as a real video model, spent on a per-frame model instead.

Tell the user plainly: the name was misread, and the models that match the actual goal are SeedVR2 and FlashVSR. Revisit VOSR only if its weight license is clarified AND we want a generative *image* tier.

## 2. Chosen family for the first temporal release: SeedVR2-3B

(https://github.com/IceClear/SeedVR2, weights https://huggingface.co/ByteDance-Seed/SeedVR2-3B, community quants https://huggingface.co/numz/SeedVR2_comfyUI)

- **License**: Apache-2.0 for code AND weights end-to-end, including the numz quant re-packs and the numz MPS implementation (https://github.com/numz/ComfyUI-SeedVR2_VideoUpscaler). This is the only serious generative VSR family that is fully commercially clean. Compatible with LocalSR's MIT license; requires only a NOTICE file (see §5).
- **Apple Silicon**: the numz node advertises full MPS support with explicit MPS dtype handling — a working implementation to adapt, not a port from scratch. No other diffusion VSR has any Mac story at all; FlashVSR is CUDA-only in every variant found (verified absence, Report 2). Real M-series throughput is unbenchmarked — ship as "Quality mode, patience required" (see §7).
- **Practical size**: 3B fp16 = 6.78 GB, fp8 = 3.39 GB, GGUF Q4 = 1.91 GB, VAE = 501 MB — all fit the existing pinned on-demand download mechanism without touching the 273 MB DMG.
- **One-step DiT** (ICLR 2026, adversarial post-training): no 25-step sampling loop; no mandatory flash-attn (graceful SDPA fallback verified); BlockSwap + VAE tiling for 8–16 GB NVIDIA cards.
- **Longevity signal**: native ComfyUI adoption (https://docs.comfy.org/tutorials/utility/seedvr2) plus a live quant/finetune ecosystem — which is also what makes BYO Tier B (§3) meaningful.

**FlashVSR v1.1** (https://github.com/OpenImagingLab/FlashVSR, ~6.95 GB Apache-2.0 weights) is the designated *second* engine — NVIDIA-only "fast mode" (~17 FPS at 768×1408 on A100; 1080p on 8 GB via the Sparse-SageAttention forks). It can never be the only engine because it has zero MPS path; vendor from the Apache-2.0 base + FlashVSR_plus technique (https://github.com/lihaoyun6/FlashVSR_plus), NOT the GPL-3.0 ComfyUI node. Phase 3.

**RealBasicVSR rejected** as the temporal engine despite tiny weights: dead upstream (mmcv/mmagic, sdist-only on PyPI — compile-at-install poison for a PyInstaller bundle), deformable-conv ops have no MPS kernel, and quality is a tier below. **The universal fallback is the pipeline you already have**: frame-by-frame HAT + temporal-median deflicker runs on every machine, and `video_pipeline.py`'s docstring already declares the seam for the temporal call. The UI frames this as two modes of one Video task: "Compatible (frame-by-frame)" everywhere, "Temporal (SeedVR2)" gated on hardware.

## 3. Bring-your-own-checkpoint design: two explicit tiers

**Tier A — per-frame image checkpoints (exists today, keep as-is).** Single `.pth/.pt/.safetensors` → Spandrel auto-detect → existing frame pipeline + deflicker. This is what "custom checkpoint" means to the OpenModelDB community (https://openmodeldb.info/ — effectively 100% single-image). Keep drag-and-drop single-file semantics and the existing pickle warning (`model_adapter.py:81–84`).

**Tier B — temporal family bundles.** There is no Spandrel for video and none is coming (Spandrel PR #227 dormant since April 2024, zero video archs in 0.4.2 — https://github.com/chaiNNer-org/spandrel/pull/227). So:
1. **Named family allowlist**, not arbitrary architectures: `seedvr2_3b`, `seedvr2_7b` first; `flashvsr_v1_1` later. Each family gets vendored loader code and a **manifest schema**: required roles (`dit`, `vae`), expected tensor-key fingerprint and shapes per role, accepted dtypes (fp16/fp8/GGUF-Q4/Q8).
2. **BYO = alternative weights within a family**: user points a family slot at a local file (official fp16, community fp8/GGUF, "sharp" finetunes). Validation before load: extension check (prefer `.safetensors`; `.pth/.ckpt` get the existing pickle warning and load only inside the isolated worker subprocess), header/key-set fingerprint match against the family schema, dtype/size sanity. On mismatch: refuse with "not a SeedVR2-3B-shaped checkpoint", never a stack trace.
3. **Hashes**: catalog bundles keep mandatory pinned SHA-256 per file (existing `download_model` behavior); user-supplied files get an *optional* hash field recorded in settings so re-validation can detect swaps.
4. **UI**: a multi-file bundle is one logical model (Refocused's "weight family" concept, https://www.refocused.ai/guide/models is the UX precedent — Refocused itself has NO BYO, it's a benchmark not a source).
5. Legacy `.pth` recurrent models (RealBasicVSR) explicitly excluded — OpenMMLab code dependency, minimal community momentum.

## 4. Integration architecture, file by file

**`core/model_catalog.py`**
- Add `ModelPurpose.VIDEO`.
- New frozen dataclasses: `ModelFile(role, filename, size_bytes, sha256, download_url)` and `CatalogVideoModel(model_id, name, family, files: tuple[ModelFile, ...], engine_kind, license_name, min_unified_memory_gb, min_vram_gb, …)`. Keep `CatalogModel` untouched for image models.
- `download_bundle()` wrapper looping the existing `download_model()` machinery per file (preflight sums all files × 1.15; per-file `.part` + SHA-256 + atomic replace already correct). `ModelStore` gets `bundle_dir_for(model)` (`models/seedvr2_3b/`) and `is_installed` checks every file.
- Catalog entries pinned to immutable HF `resolve/{commit}` URLs (same pattern as `CATALOG_REVISION`): `seedvr2_3b_fp8` (3.39 GB, CUDA), `seedvr2_3b_fp16` (6.78 GB, MPS/CUDA), shared `ema_vae_fp16.safetensors` (501 MB) from https://huggingface.co/numz/SeedVR2_comfyUI.

**`protocol/messages.py`**
- `VideoJobRequest` += `model_kind: str = "spandrel_image"`, `temporal_window: int`, `temporal_overlap: int`, `bundle_dir: str | None`, `target_height: int | None` (SeedVR2 is arbitrary-resolution — "scale" is the wrong abstraction; keep `scale` for the frame-by-frame path).
- A `VideoModelInfo` variant (or optional fields on `ModelInfo`): `kind`, `temporal_window`, `supports_arbitrary_resolution`. `VideoFrameStarted/Completed/VideoJobCompleted` stay unchanged — cadence becomes bursty per clip, schema doesn't move.

**`worker/server.py`**
- Branch on `model_kind` BEFORE the unconditional `self.model_adapter.inspect(data["model_path"])` at line 557 — today a video-model job dies at inspect. Route `"seedvr2"` to a new `VideoModelAdapter`; keep cancel/thumbnail/ETA wiring intact.

**`core/` new code**
- `src/localsr/video_models/seedvr2/` — vendored arch + loader adapted from the Apache-2.0 numz implementation, original headers retained (first vendored third-party code in the repo — establishes the `NOTICE` pattern, §5). Device map: MPS/CUDA/CPU; fp16 on MPS, fp8 on CUDA; SDPA attention only (no flash-attn, no triton).
- `process_clip()` in a clip-aware twin of `InferenceEngine` (input `[1,T,C,H,W]`, spatial tiling reuses `generate_tiles()` halo logic with T stacked; OOM backoff halves *temporal window first*, then tile size; `safe_memory` empty_cache between clips, not tiles).
- `video_pipeline.py`: a `_clip_batches()` generator stage wrapping `decode_frames` — exactly the `_deflicker_frames` generator-wrapping-generator shape (N frames in → burst of N−overlap out, linear-blend overlap frames). `run_video_job` dispatches on `model_kind`; decode/encode/cancel/progress/atomic-output plumbing unchanged, exactly as the module docstring intended. `_SafeModelInfo`'s `__getattr__` duck-typing means `VideoJobConfig.model_info: object` needs no change. Deflicker is bypassed in temporal mode (redundant and would soften SeedVR2 output).
- `video_io.py`: audio passthrough in `encode_video` (add an audio stream, copy packets via PyAV remux); `container.seek()` in `decode_frames` for `start_frame` (currently decodes-and-discards from 0).

**UI (`slint_app.py`, `main.slint`, `core/presets.py`, `core/estimator.py`)**
- Flip `VIDEO_ENABLED` (line 58); add video extensions (`.mp4 .mov .m4v .mkv .webm .avi`) with a probe branch (`probe_image_size` crashes on mp4); build `VideoJobRequest` in `_start_next_job` (currently zero uses of it in the UI); fix `_output_path_for` to emit `.mp4`; wire the orphaned `deflicker` Slint property (written once at line 171, never read); implement the two stubbed `_on_video_frame_*` handlers (thumbnail + ETA fields already arrive on the wire).
- Model picker: `_model_is_compatible` for the Video task offers (a) `ModelPurpose.VIDEO` bundles labeled "Temporal", (b) image upscale models labeled "Frame-by-frame" — fixing the current bug where task 2 silently reuses the image predicate. Precision picker unlocks fp16 for video (worker plumbing at `inference.py:75–79` already exists; UI is fp32-only today at line 149).
- Recipes: "Video — Fast & Compatible" (HAT-S ×4 + deflicker w3) and "Video — Best Quality (Temporal)" (SeedVR2-3B, hardware-gated) in `rank_models_for_preset`. `estimator.py` gets a first video cost model: frames × per-frame-tile estimate for mode A; frames × measured-first-clip seconds for mode B.

## 5. Packaging: base app stays flat

- **DMG unchanged (~273 MB).** Torch (408 MB unpacked) and PyAV/FFmpeg (43 MB) — the only heavy runtimes needed — are already paid for. Vendoring SeedVR2 arch code adds pure Python + `einops` (0.1 MB wheel). Avoid the diffusers/transformers stack entirely by vendoring (the spandrel approach); even if later needed, the whole HF stack is ~19 MB of pure-Python wheels (Report 4 PyPI measurements). Explicitly avoid: flash-attn, triton, bitsandbytes, mmcv, basicsr (sdist-only, compile-at-install).
- **Weights are on-demand, never bundled**: 1.9–8.2 GB per variant makes bundling impossible; the existing pinned-SHA-256 streaming downloader handles it after the §4 multi-file extension. Precedent on all sides: LocalSR's own catalog, ComfyUI's model-manager convention, and Refocused's per-family weight downloads + per-hardware engines (https://www.refocused.ai/guide/models).
- **Source install**: add `[project.optional-dependencies] video = ["einops>=0.8"]` so minimal installs stay minimal; the frozen DMG just includes it.
- **Housekeeping owed anyway**: create `THIRD_PARTY_NOTICES` when vendoring — and note the DMG *already* redistributes GPL `libx264`/`libx265` via PyAV with no notice file (Report 4 §8). Fix both at once. Also surface the CC BY-NC-SA (non-commercial) face-model license more prominently than the current one-line status text.

## 6. Phased delivery

**Phase 1 — ship the pipeline that already exists (one working session).** No new model, no new deps; the worker side is complete and dormant. Tasks: (1) `image_formats.py` — add `SUPPORTED_VIDEO_EXTENSIONS`; (2) `slint_app.py` — probe branch for video in ingest, `VIDEO_ENABLED = True`, construct/send `VideoJobRequest` in `_start_next_job`, video extension in `_output_path_for`, implement `_on_video_frame_started/_completed`, wire `deflicker_enabled` into the request; (3) `main.slint` — surface CRF/container defaults (mp4/CRF 18 hardcoded is acceptable for beta); (4) `estimator.py` — naive frames × frame-cost estimate; (5) smoke test: 1080p clip on MPS end-to-end, cancel mid-job, deflicker on/off. Ship as **"Video (beta): frame-by-frame + deflicker."**

**Phase 2 — SeedVR2-3B temporal engine (2–4 sessions).** Vendor `video_models/seedvr2/` + NOTICE; catalog `CatalogVideoModel`/`ModelFile` + `download_bundle`; protocol fields; worker `model_kind` branch before inspect; `_clip_batches` + `process_clip` with overlap blending and temporal-first OOM backoff; fp16 UI unlock; hardware gating (hide/disable temporal mode below floors, §7); audio passthrough in `encode_video` (visibility jumps once real video work lands).

**Phase 3 — breadth.** BYO Tier B (family schema validation, alternative-weight slot, GGUF Q4 support); FlashVSR v1.1 CUDA fast mode via Sparse-SageAttention (vendored Apache base); `decode_frames` seek; SeedVR2-7B for 24 GB+ machines; per-family estimator calibration.

## 7. Honest constraints to tell the user

- **VOSR was a misread** — it's an image model; the plan pivots to SeedVR2 (with FlashVSR as a future NVIDIA fast mode).
- **Temporal mode will be slow on your Mac.** SeedVR2 on MPS is *functional* (per the numz implementation) but no published M-series benchmarks exist anywhere; a 3B DiT per clip window plausibly means seconds to tens of seconds per output frame at 1080p. A 2-minute clip is an overnight job, not an interactive one. Frame-by-frame + deflicker remains the fast Mac path.
- **Hardware floor for temporal mode**: ~16 GB unified memory minimum (fp16 weights alone are 6.78 GB + activations; BlockSwap auto-disables on macOS), 24–32 GB comfortable. 8 GB Macs get frame-by-frame only. On NVIDIA: 12–16 GB for fp8, 8 GB only via GGUF+tiling (phase 3).
- **FlashVSR will never run on this Mac** — CUDA-only in every known variant.
- **Quality caveats (official SeedVR2 statements)**: over-generates detail on already-clean footage; not robust to heavy degradation or large motion. It is a restoration model, not magic.
- **Current gaps until phase 2/3**: output has **no audio** (dropped by `encode_video`); trimming a late segment decodes everything before it (no seek); face-aware model switching does not combine with the temporal engine; Mac results are generative — two runs won't be pixel-identical.
- **Licensing**: everything in the temporal plan is Apache-2.0/MIT-clean; the one non-commercial item in the app remains the existing face model (CC BY-NC-SA), and the GPL x264/x265 notice debt predates this work.

Key files: `/Users/hermesheiniger/LocalSR/src/localsr/core/video_pipeline.py`, `/Users/hermesheiniger/LocalSR/src/localsr/core/model_catalog.py`, `/Users/hermesheiniger/LocalSR/src/localsr/core/video_io.py`, `/Users/hermesheiniger/LocalSR/src/localsr/worker/server.py` (branch before line 557), `/Users/hermesheiniger/LocalSR/src/localsr/protocol/messages.py`, `/Users/hermesheiniger/LocalSR/src/localsr/ui/slint_app.py` (lines 58, 149, 460–469, 1330–1390, 1810–1816), `/Users/hermesheiniger/LocalSR/src/localsr/core/image_formats.py`, `/Users/hermesheiniger/LocalSR/src/localsr/core/presets.py`, `/Users/hermesheiniger/LocalSR/src/localsr/core/estimator.py`.