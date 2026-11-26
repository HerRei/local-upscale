# Pipeline Step Registry

`src/localsr/core/steps.py` is the single source of truth for LocalSR's pipeline steps, fix kinds, ordering phases, limits, feature gates, and step parameters.

## Export and Sync

- Source of truth: `src/localsr/core/steps.py` (`PHASES`, `STEPS`, `LIMITS`).
- Export script: `scripts/export_desktop_steps.py` writes `desktop/src-tauri/resources/steps.json` (run with `--check` to verify sync).
- Python reads `localsr.core.steps` directly (`pipeline.py`, `request_validation.py`, `server.py`, `image_job.py`).
- Rust embeds `desktop/src-tauri/resources/steps.json` via `include_str!` in `desktop/src-tauri/src/steps.rs`.
- TypeScript imports `desktop/src-tauri/resources/steps.json` in `desktop/src/lib/steps.ts`.
- `tests/test_steps_registry.py` fails if the exported JSON drifts out of sync with `src/localsr/core/steps.py`.

## Step Fields

Each `StepDefinition` in `STEPS` declares:

- `id`: unique step identifier (e.g. `blur`, `jpeg`, `upscale`, `face_restore`, `deflicker`, `interpolate`).
- `media`: tuple of supported media kinds (`"image"`, `"video"`).
- `phase`: execution phase from `PHASES` (`audio`, `geometry`, `cleanup`, `restore`, `primary`, `face`, `temporal`, `grade`, `encode`).
- `order`: sort rank within the phase (and `fix_chip_order` for Fix chip display order).
- `needs_model`: whether the step requires a model/bundle checkpoint.
- `supports_mask`: whether stages for this step may carry a `mask_path` for linear compositing (`mask * stage_output + (1 - mask) * stage_input`).
- `stage_kind` / `role` / `fix_kind`: protocol stage kind, pipeline role, and optional Fix chip id.
- `in_image_pipeline` / `in_recipe_stages`: whether the step appears in bounded image `stages[]` requests and saved `Recipe.stages`.
- `label` / `row_label` / `short_name` / `stage_label`: UI and worker status strings.
- `required_feature`: worker `engine_info.features` entry gating the step (`image_edit`, `video_interpolation`, `video_audio_denoise`, etc.).
- `parameters`: `StepParameter` entries naming each parameter's type, default, choices, numeric bounds, `nullable`, and `required`.

## Adding a New Step

1. Add a `StepDefinition` (and any `StepParameter` bounds/choices and `required_feature`) to `STEPS` in `src/localsr/core/steps.py`.
2. Run `.venv/bin/python scripts/export_desktop_steps.py` to regenerate `desktop/src-tauri/resources/steps.json`.
3. Implement the step's execution in the Python worker and add any new persisted UI/recipe fields or UI controls needed to configure it.
4. If the new step adds a `FixKind`, `StageKind`, `RecipeStage.kind`, or `PlanStage.kind` string literal in TypeScript, add that literal to the union types in `desktop/src/lib/types.ts` / `desktop/src/lib/plan.ts` (enforced by `desktop/src/lib/steps.test.ts`).

## Adding a Model-less or Masked Step

1. In `src/localsr/core/steps.py`, add the `StepDefinition` with `needs_model=False`, `in_image_pipeline=True`, `phase` placed where it runs (`geometry`, `cleanup`, `restore` before `primary`, or `grade` after `face`), and `supports_mask=True` if the step accepts a mask. Declare every parameter in `parameters` using `StepParameter(..., required=...)`; Python (`validate_step_params`), Rust (`StepRegistry::validate_stage_params`), and TypeScript (`validateStepParams`) all validate `params` from `steps.json` without per-step validators.
2. Re-export `desktop/src-tauri/resources/steps.json` with `.venv/bin/python scripts/export_desktop_steps.py`.
3. Register one implementation function in `IMAGE_STEP_HANDLERS` (`src/localsr/worker/image_job.py`) keyed by `step.id`. It receives `(runner, image, stage, index, data, progress, notes)` and returns an updated image dict (`stage.params` holds validated parameters).
4. Do not implement masking inside the step itself: `ImageJobRunner` reads any `stage.mask_path` as a single-channel 8- or 16-bit image, resizes it with bilinear resampling to the stage's output resolution, and composites `mask * stage_output + (1 - mask) * stage_input` once in `_apply_stage_mask`. Callers that want feathered edges supply an already-feathered mask.
5. In the Rust host, any stage carrying `params` or `mask_path` is gated behind `limits().stage_params_feature` (`stage_params_and_masks`) so older engines never receive them.

## Adding a Video Step

1. Add or update the `StepDefinition` in `src/localsr/core/steps.py` with `"video"` in `media` (and `phase`, `order`, `needs_model`, `required_feature`, and any `StepParameter` entries), then run `.venv/bin/python scripts/export_desktop_steps.py` to keep `desktop/src-tauri/resources/steps.json` in sync.
2. Write a `VideoStreamStage` (`Iterator[StreamFrame] -> Iterator[StreamFrame]`) in `src/localsr/core/video_chain.py` plus a handler `(stage: VideoFixStage, step_ctx: VideoStepContext) -> VideoStreamStage` that reads `stage.params` and preserves per-frame side data on `StreamFrame`.
3. Register the handler in `VIDEO_STEP_HANDLERS` (`src/localsr/core/video_chain.py`) keyed by `step.id`. `WorkerServer._run_video_job` (`src/localsr/worker/server.py`) forwards `step_id` and `params` onto `VideoFixStage` and rejects any video stage carrying `mask_path`.
