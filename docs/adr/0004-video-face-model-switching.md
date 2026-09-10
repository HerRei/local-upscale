# Architecture Decision Record: 0004 - Video Face-Model Switching

## Status

Accepted and Implemented. Integrated with `hat_s_x4_face` (α=0.10, `base_95k_interp_a0p1.pth`) and
`hat_l_x4_face` (α=0.25, `hat_l_x4_face_task4.pth`) on the later-release
`codex/hat-face-models` branch. Each pairs reciprocally with its own stock variant.

Amended for v0.0.11-alpha: the processing design and fidelity-controlled spatial blend remain
implemented, but independent rights for the exact checkpoint/training data are not verified. The
checkpoint is no longer a trusted automatic download; the app accepts only a hash-matching
user-supplied copy and makes no commercial-use claim.

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

YuNet 2023mar runs on each sampled decoded frame **before** inference. The
detector stays on CPU and therefore does not contend with the inference device. Its exact
232,589-byte ONNX asset is independently MIT-licensed by the OpenCV Zoo directory and is downloaded
on first face-aware use with a pinned SHA-256. `opencv-python-headless==4.10.0.84` is isolated in the
`face` dependency extra and packaged in release workers; that version retains macOS 12 and NumPy 1.x
compatibility for the v0.0.11 cross-build. Video reuses a mask for a bounded number of adjacent
frames instead of detecting every frame.

### Model management

`InferenceEngine` caches both compatible models for the job. Face-aware tiles run through the
face checkpoint, non-face tiles through the primary checkpoint, and boundary tiles through both.
A feathered spatial mask blends the outputs. Fidelity is not presented as a native HAT parameter.
LocalSR bicubic-resamples the original face region to the target size and defines the control as:

`face_mix = original_upscaled × (1 − fidelity) + restored × fidelity`

`output = primary × (1 − face_mask) + face_mix × face_mask`

Thus zero retains the original identity-bearing pixels inside the feathered face region, one uses
the strongest face-restoration output, and non-face regions remain exactly the primary upscaler's
output.

### What is explicitly deferred

- Native checkpoint-specific fidelity controls; the current control is a documented spatial blend.
- Optical-flow tracking of face masks. The bounded detection interval is intentionally simpler and
  remains Labs until motion-heavy footage is physically tested.
- Automatic distribution of HAT-S Face. Only an exact hash-matching user-supplied checkpoint is
  accepted while its independent weight/training-data rights remain unresolved.

## Consequences

- The worker process holds two loaded models during video jobs that
  opt into face switching. Memory usage roughly doubles for model
  weights (from ~80 MB to ~160 MB for two HAT-S checkpoints). This is
  negligible compared to activation memory.
- The face detector adds the optional headless OpenCV CPU dependency. The base image/video pipeline
  continues to work when the `face` extra is absent and feature negotiation disables the control.
- The `VideoJobRequest` protocol message gains `face_model_path` and
  `face_threshold` fields. The GUI gains a "Use face model when
  available" toggle in the video task panel.
- `run_video_job` caches detection masks, executes the same bounded tiled face-aware function as
  images, and keeps progress/cancellation inside the active recipe stage.

## Open questions

- Whether the face fine-tune generalizes acceptably to upper-body and
  portrait shots where hair, shoulders, and background are visible
  alongside the face. This will be evaluated empirically once training
  completes.
- Whether a future detector can improve difficult-angle coverage without a larger or less clearly
  licensed dependency. YuNet is the deliberately small, verified alpha baseline.
