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
- `dev_only`: the worker lists the step's `required_feature` and runs the step only when it was started with `LOCALSR_DEV_STEPS=1` (the desktop host passes its environment on). Only `grain` uses this gate; `remove_background` shipped with the Backgr. row and `deinterlace` with the Scan row.
- `model_task`: for a step stage that needs a model, the catalog `purposes` entry its models carry. A step with `needs_model=True` is a step stage only when it is not a fix and names one.
- `output_alpha`: the step's handler returns a matte that becomes the image's alpha; a photo job with such a step saves as PNG unless its format keeps alpha (PNG, TIFF, WebP).
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
6. A job adds such a step by id through `step_stages` (`StepStageSelection {step, params?, mask_path?, model_id?}` in `UiSettings` and `StartBatchInput`, omitted when empty). The host plans it (`job_plan.rs` `active_step_stages`, `plan.ts` `activeStepStages`, same rules in the parity fixture), validates it on save and start (`commands.rs` `validate_step_stages`, `validate_step_stages_engine`, and every mask path must be a granted media file), and sends it as an ordinary `stages[]` entry: before the fixes when its phase comes before `primary`, after the primary and Faces when it comes after `face` (`image_pipeline_stages`). Each step runs at most once; `limits.max_step_stages` bounds the list. Settings load drops entries this version cannot run. Give a step that should stay hidden `dev_only=True` and a `required_feature`. Design: `docs/design/foundation-c.md`.

## Adding a Step with a Model or an Alpha Output

1. Give the `StepDefinition` `needs_model=True` and a `model_task` (a step stage cannot be a fix), and add its catalog models with that task in their `purposes`. Set `output_alpha=True` for a step whose result is a matte (photos only). Re-export `steps.json`.
2. A job names the model in `StepStageSelection.model_id`. The host requires it at start, resolves it as it resolves a fix's model (`installed_stage_model` in `commands.rs`: in the catalog, of the step's task, installed) and sends it as the stage's `model_id` and `model_path`; the worker checks the file against the catalog SHA-256 before reading it. The plan shows the model's row (download, needs a file, or missing) like a fix's, and settings load drops an entry whose model this catalog does not offer for the step.
3. Register the handler in `IMAGE_MODEL_STEP_HANDLERS` (`src/localsr/worker/image_job.py`). It receives `(runner, image, stage, index, data, progress, notes, model)`, where `model` is a `StepModel` (path, inspected info, the module the engine loaded, device, precision); the runner loads the model as a fix's is and releases it after the stage.
4. An `output_alpha` handler (with or without a model) returns a matte, height x width with values 0..1. The stage multiplies it into the image's alpha (a mask keeps the alpha outside it) and leaves the colour unchanged; the alpha follows later stages and is scaled with the image when it is saved. The host saves such a job as PNG when the chosen format is JPEG or HEIC, and the output line says so (`output_format_keeping_alpha` in `job_plan.rs`, `outputFormatKeepingAlpha` in `plan.ts`).
5. A job may consist of step stages alone: Restore with nothing picked, or Upscale at 1x, runs them without a main model.

## Adding a Video Step

1. Add or update the `StepDefinition` in `src/localsr/core/steps.py` with `"video"` in `media` (and `phase`, `order`, `needs_model`, `required_feature`, and any `StepParameter` entries), then run `.venv/bin/python scripts/export_desktop_steps.py` to keep `desktop/src-tauri/resources/steps.json` in sync.
2. Write a `VideoStreamStage` (`Iterator[StreamFrame] -> Iterator[StreamFrame]`) in `src/localsr/core/video_chain.py` plus a handler `(stage: VideoFixStage, step_ctx: VideoStepContext) -> VideoStreamStage` that reads `stage.params` and preserves per-frame side data on `StreamFrame`.
3. Register the handler in `VIDEO_STEP_HANDLERS` (`src/localsr/core/video_chain.py`) keyed by `step.id`. `WorkerServer._run_video_job` (`src/localsr/worker/server.py`) forwards `step_id` and `params` onto `VideoFixStage` and rejects any video stage carrying `mask_path`, any step whose `media` lacks `"video"`, any step stage that brings its own model, and any model-less stage after the main model (the frame chain runs them only before it).
