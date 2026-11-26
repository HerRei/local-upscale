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
- `stage_kind` / `role` / `fix_kind`: protocol stage kind, pipeline role, and optional Fix chip id.
- `in_image_pipeline` / `in_recipe_stages`: whether the step appears in bounded image `stages[]` requests and saved `Recipe.stages`.
- `label` / `row_label` / `short_name` / `stage_label`: UI and worker status strings.
- `required_feature`: worker `engine_info.features` entry gating the step (`image_edit`, `video_interpolation`, `video_audio_denoise`, etc.).
- `parameters`: `StepParameter` entries naming each parameter's type, default, choices, and numeric bounds.

## Adding a New Step

1. Add a `StepDefinition` (and any `StepParameter` bounds/choices and `required_feature`) to `STEPS` in `src/localsr/core/steps.py`.
2. Run `.venv/bin/python scripts/export_desktop_steps.py` to regenerate `desktop/src-tauri/resources/steps.json`.
3. Implement the step's execution in the Python worker and add any new persisted UI/recipe fields or UI controls needed to configure it.
4. If the new step adds a `FixKind`, `StageKind`, `RecipeStage.kind`, or `PlanStage.kind` string literal in TypeScript, add that literal to the union types in `desktop/src/lib/types.ts` / `desktop/src/lib/plan.ts` (enforced by `desktop/src/lib/steps.test.ts`).
