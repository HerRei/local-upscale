# Architecture Decision Record: 0004 - Video Face-Model Switching

## Status

Accepted and Implemented. Integrated with `hat_s_x4_face` (blended $\alpha=0.10$ checkpoint `base_95k_interp_a0p1.pth`).

## Context

The frame-by-frame video pipeline (`src/localsr/core/video_pipeline.py`)
restores every frame with a single model. A HAT-S checkpoint fine-tuned on
faces is available in the model catalog (`hat_s_x4_face`). The video pipeline
uses it for frames containing faces and falls back to the general
HAT-S model for frames without faces.

This is not the same as a temporal SR model (RealBasicVSR / RVRT). Both
the face and general models are single-frame HAT-S variants; the
selection is per-frame, not per-pixel.

## Decision

Extend the video pipeline with an optional per-frame model selection
step. The design is intentionally minimal so the existing decode →
infer → encode loop stays unchanged.

### Extension point

`VideoJobConfig` gains two optional fields:

- `face_model_path: str | None` — path to the face fine-tune checkpoint.
  When `None`, the pipeline behaves exactly as today.
- `face_threshold: float` — minimum face-area ratio (face bounding box
  area / frame area) required to switch to the face model. Default 0.20.
  Prevents using a face-trained model on wide shots where a face is
  small and the rest of the frame is landscape, clothing, or sky.

### Detection

A lightweight face detector runs on each decoded frame **before**
inference, not after. MediaPipe Face Detection is the intended choice
(~5 ms/frame on CPU, no GPU contention with the inference device). The
detector runs in the worker process alongside PyAV decode.

### Model management

`InferenceEngine` already caches a loaded model via `load_model()` /
`release_model()`. The video pipeline will load **both** models at job
start (two HAT-S checkpoints fit comfortably in 16 GB VRAM) and switch
the active model per frame by calling `load_model` with the appropriate
path. The cache check in `load_model` avoids reloading when switching
back and forth.

### What is explicitly deferred

- **Region-based compositing** (face model on the face crop, general
  model on the rest, alpha-blended at the boundary) is not part of this
  extension. It would give better quality on mixed-content frames but
  requires two inference passes per frame, seamless boundary blending,
  and the face model being trained on detector-output crops at the same
  scale. That is a separate ADR if it becomes necessary.
- **Temporal coherence between the two models**: switching models
  frame-to-frame can introduce a subtle discontinuity at the switch
  boundary. A 3-frame temporal median post-process pass (see ADR 0005,
  if adopted) would smooth this. Without it, the discontinuity is
  usually imperceptible because face and general HAT-S share the same
  architecture and training lineage.

## Consequences

- The worker process holds two loaded models during video jobs that
  opt into face switching. Memory usage roughly doubles for model
  weights (from ~80 MB to ~160 MB for two HAT-S checkpoints). This is
  negligible compared to activation memory.
- The face detector adds a CPU-side dependency (MediaPipe or
  equivalent). It must be an optional dependency so the base video
  pipeline continues to work without it.
- The `VideoJobRequest` protocol message gains `face_model_path` and
  `face_threshold` fields. The GUI gains a "Use face model when
  available" toggle in the video task panel.
- The existing `run_video_job` function gains one branch: if
  `face_model_path` is set and a face is detected above threshold,
  call `engine.load_model(face_model_path, ...)` before
  `process_frame`; otherwise call `load_model(primary_model_path, ...)`.
  No other changes to the loop.

## Open questions

- Whether the face fine-tune generalizes acceptably to upper-body and
  portrait shots where hair, shoulders, and background are visible
  alongside the face. This will be evaluated empirically once training
  completes.
- Whether MediaPipe is the right detector or whether a simpler
  haar-cascade is sufficient. MediaPipe is preferred for robustness;
  the decision can be revisited based on real-world test footage.