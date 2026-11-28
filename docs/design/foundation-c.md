# Foundation C: model-less steps, parameters and masks

Design note for LocalSR 0.7.0, after the architecture review of 2026-11-26. Foundations A
(one step registry), B (video as a chain of frame stages), D (16-bit), E (job plan in Rust)
and F (typed job request) are in. C is what the next tools need before they can be added:
steps without a model, parameters a user sets, and masks.

Line numbers in "Today" refer to `fdca2d1c` on `w/070-foundation-c` (before this change).
Paths are from the repository root.

## Summary

Most of C already exists below the host: the registry declares parameters, masks and
model-less steps, the worker validates and runs them, and the protocol carries `step`,
`params` and `mask_path` on every stage. What is missing is the way in: the host can only
send a stage that is a fix with a catalog model, so a model-less step can never reach the
worker, and nothing on the host checks a mask path against what the user granted.

The smallest additive design closes that with one new list beside `fix_stages`:
`step_stages`, each entry `{step, params?, mask_path?}` naming a registry step by id. It is
optional in the settings, the start input and the parity fixture, travels as ordinary
`stages[]` entries in protocol v1 (no schema change), is placed by its registry phase, and
is gated by the step's own engine feature. A registry flag `dev_only` keeps a step's feature
out of `engine_info` unless `LOCALSR_DEV_STEPS=1` is set, so the step stays out of every
job until a developer turns it on; no UI control adds a step stage yet.

`grain` (film grain, `strength` 0-100, `seed`, mask support) is the first step built on it.

## Today

### (a) Model-less steps

Works:

- The registry has `needs_model` per step (`src/localsr/core/steps.py:54`); de-flicker is
  model-less (`steps.py:290-308`).
- The worker accepts model-less image stages: `_parse_stage_item` only demands a model when
  `step.needs_model` (`src/localsr/core/pipeline.py:199-200`); before the primary they must
  sit in a phase before `primary`, after it in a phase after `face`
  (`pipeline.py:255-265`, `281-283`). A job of only model-less stages and fixes at 1× takes
  the fix-only path (`pipeline.py:111-113`, `148-167`).
- Execution is by registry id: `IMAGE_STEP_HANDLERS` (`src/localsr/worker/image_job.py:356`)
  for photos, `VIDEO_STEP_HANDLERS` (`src/localsr/core/video_chain.py:308`) for video frames;
  stages after the primary run in `_run_postprocess` (`image_job.py:639`).
- A test-only model-less step proves the worker path (`tests/test_pipeline.py:1006-1131`).

Missing:

- **No way in from the host.** `StartBatchInput` has only `fix_stages`
  (`desktop/src-tauri/src/types.rs:1267`); every entry must be a fix kind
  (`desktop/src-tauri/src/commands.rs:4411-4423`) with a catalog model
  (`commands.rs:4774-4805`). `image_pipeline_stages` builds fixes, the primary and the face
  stage, nothing else (`commands.rs:5208-5257`). The plan knows de-flicker by name only
  (`desktop/src-tauri/src/job_plan.rs:474-493`, `desktop/src/lib/plan.ts:321`).
- **Media is not checked.** `_parse_stage_item` never compares `step.media` with the job, so
  an image-only step in a video job fails late with "No video handler registered".
- **Video drops stages after the primary.** `_run_video_job` builds `VideoFixStage`s from
  `pipeline.preprocess_stages` only (`src/localsr/worker/server.py:2200`); a model-less
  stage after the primary would be dropped without a word.
- **1× Upscale drops them too.** At output scale 1 with an upscale primary,
  `resolve_image_pipeline` keeps only the stages before the primary (`pipeline.py:137-140`).
- **Bounds.** `max_image_pipeline_stages` (6 = four fixes, primary, face) is also the length
  limit for every stage (`pipeline.py:240-243`), so a full recipe plus one step is refused.

### (b) Step parameters

Path: declared in `steps.json` -> UI value -> typed request -> Rust plan and validation ->
Python handler.

Works:

- Declarations: `StepParameter` with kind, default, bounds, choices, `required`, `nullable`
  (`steps.py:28-43`), exported to `desktop/src-tauri/resources/steps.json`
  (`scripts/export_desktop_steps.py:22-36`).
- One validator per language, all generic: Python `validate_step_params` (`steps.py:487-505`),
  Rust `StepRegistry::validate_step_params` (`desktop/src-tauri/src/steps.rs:240-269`),
  TypeScript `validateStepParams` (`desktop/src/lib/steps.ts:148-176`).
- Carriage: `FixStageSelection.params` and `RecipeStage.params` (`types.rs:317-339`,
  `desktop/src/lib/types.ts:301-321`), `PipelineStageRequest.params` (`protocol.rs:218-239`),
  `pipelineStage.params` in the schema (`protocol/worker-protocol.schema.json:238-257`).
- Gate: any stage with params or a mask needs the engine feature `stage_params_and_masks`
  (`commands.rs:4389-4409`, `src/localsr/worker/server.py:205`).

Missing:

- **No UI value.** Nothing in `desktop/src` writes `params` (only the types declare it). The
  parameters that exist today (de-flicker window, face fidelity, edit steps/seed,
  interpolation) travel as their own top-level fields; their registry entries are bounds,
  not carriage.
- **No defaults at run time.** `validate_step_params` returns only the keys that were sent;
  a handler must know each default itself.
- **Parameters of a fix are not declared.** The schema sample `image_stage_params_and_mask`
  sends `strength` on the noise fix (`tests/fixtures/job_request_samples.json:101-136`),
  which the worker would refuse as an unknown parameter: noise declares none
  (`steps.py:135-151`). Fine as a protocol sample; not a working path.

### (c) Masks

Works:

- Registry: `supports_mask` per step (`steps.py:62`), false for every shipped step.
- Validation: host (`commands.rs:4429-4443`, `1048-1050`), request (`request_validation.py:365`)
  and pipeline (`pipeline.py:209-217`) refuse a mask on a step without `supports_mask`.
- Compositing in one place: `_apply_stage_mask` loads an 8- or 16-bit single-channel mask,
  resizes it bilinearly to the stage output and blends `mask * out + (1 - mask) * in`
  (`image_job.py:225-257`, `src/localsr/core/face_compositing.py:21-49`); used for every
  non-primary stage (`image_job.py:595-600`) and the primary (`image_job.py:782-789`).
- Batching skips masked or parameterised stages (`desktop/src-tauri/src/worker.rs:775-776`,
  `src/localsr/worker/image_batch.py:91`).

Missing:

- **No mask source.** Nothing in the UI paints, picks or writes a mask.
- **The host never checks a mask path against the grants.** `validate_start`
  (`commands.rs:4194-4214`) checks the output folder and a custom model, not `mask_path`;
  the webview could make the worker open any image path.
- **Video refuses masks** (`server.py:2189-2191`), by design for now.

## Design

Additive only: protocol v1 gains no field, settings gain one optional list, and
`apply_job_envelope`, `updates.rs` and `engine_manager.rs` are untouched.

1. **Registry.** `StepDefinition.dev_only` (default false) and a derived limit
   `max_step_stages` (the number of steps a job may add by id). Rust reads `media`, `phases`,
   `in_image_pipeline` and `dev_only`, which it ignored before. A *step stage* is a registry
   step that is model-less, in the stage list, not a fix, and placed before `primary` or after
   `face`.
2. **One list, `step_stages`.** `StepStageSelection {step, params?, mask_path?}` in
   `UiSettings` and `StartBatchInput` (Rust and TypeScript), omitted when empty, so every
   saved file, recipe and fixture stays as it is. Settings load drops entries this version
   cannot run (an unknown step, a value outside today's bounds, a repeat), so a settings file
   from a newer LocalSR never blocks saving.
3. **Plan (Rust and TypeScript, same rules, parity fixture).** The plan lists the step stages
   that run for the task's media (photos for Upscale and Restore, frames for frame-by-frame
   video, none for temporal models and Edit): phases before `primary` ahead of the fixes,
   later phases after the primary and face, each as a `role: "step"` row like De-flicker.
   `build_start_input` carries the same entries.
4. **Validation (host).** On save, plan and start: known step stage, parameters by the registry,
   mask only where `supports_mask`, each step once. On start also: the step suits the task's
   media, the engine lists the step's `required_feature` (and `stage_params_and_masks` when
   params or a mask are sent), and every mask path is a granted media file, the same rule as
   the input image (fix masks included).
5. **Request.** `image_pipeline_stages` adds step stages as ordinary `stages[]` entries
   (`kind`, `step`, `params`, `mask_path`) before the fixes or after the primary/face, also
   at 1×.
6. **Worker.** `resolve_image_pipeline(media=...)` refuses a step for the wrong media; 1×
   Upscale keeps the model-less stages after the primary; the video job refuses stages after
   the primary instead of dropping them; the stage limit counts model stages and step stages
   separately; `resolved_step_params` fills declared defaults for handlers.
7. **Dev gate.** Features of `dev_only` steps are listed in `engine_info.features` only when
   `LOCALSR_DEV_STEPS=1` (the host passes its environment to the worker), and the worker
   refuses a `dev_only` step otherwise. The host needs no flag of its own: the step's
   `required_feature` gate does it. No UI shows step stages yet.
8. **Proof: `grain`.** Image-only, phase `grade`, model-less, `supports_mask`, `dev_only`,
   `required_feature: image_grain`, parameters `strength` (int 0-100, default 25) and `seed`
   (int 0-2147483647, default 0). Monochrome Gaussian grain from a seeded NumPy generator, so
   the same image, strength and seed give the same bytes; strength 0 is the identity.

Masks need no new field for "outside": the mask file is the single source, and whoever paints
it writes it inverted. A mask painted in the app would be written by a host command into the
job's scratch folder and granted there, which keeps rule 4.

## Built

Line numbers refer to the working tree of this change.

- Registry: `dev_only` and `max_step_stages` (`src/localsr/core/steps.py:71`, `:86`, `:437`),
  the `grain` step (`steps.py:338-354`), dev features kept apart from the shipped ones
  (`steps.py:403-425`), `dev_steps_enabled` (`steps.py:441`), `resolved_step_params`
  (`steps.py:455`); exported by `scripts/export_desktop_steps.py:62`, `:78` into
  `desktop/src-tauri/resources/steps.json`.
- Rust registry: `phases`, `media`, `in_image_pipeline`, `label`, `dev_only`,
  `max_step_stages` read (`desktop/src-tauri/src/steps.rs:18`, `:33`, `:75-97`),
  `StepPlacement`, `step_stage`, `validate_step_stage` (`steps.rs:104-162`).
- Types: `StepStageSelection` and `step_stages` on `UiSettings` and `StartBatchInput`
  (`desktop/src-tauri/src/types.rs:344`, `:416`, `:1282`; `desktop/src/lib/types.ts:310`,
  `:343`, `:741`).
- Plan, both languages: `step_stage_media`, `active_step_stages`, the `step` row
  (`desktop/src-tauri/src/job_plan.rs:227-277`, `:379`, `:595`, `:680`;
  `desktop/src/lib/plan.ts:179-210`, `:276`, `:398`; `desktop/src/lib/startInput.ts:69`, `:94`);
  `stepStage` (`desktop/src/lib/steps.ts:109`).
- Host checks: `validate_step_stages`, `validate_step_stages_engine`,
  `check_stage_masks_granted` (`desktop/src-tauri/src/commands.rs:4426-4515`), called from
  plan (`:1177`), save (`:4243`), start (`:1273`, `:4209`, `:4603-4611`); 1× work
  (`:4683`); request stages (`:5337-5395`); settings load (`desktop/src-tauri/src/settings.rs:43`,
  `:77`).
- Worker: media check and dev gate (`src/localsr/core/pipeline.py:218-225`), stage bounds and
  once-only (`pipeline.py:188-206`), 1× keeps later stages (`pipeline.py:144`), video refuses
  later stages (`src/localsr/worker/server.py:2193-2202`), dev features
  (`server.py:223`), grain (`src/localsr/core/grain.py:15`, `src/localsr/worker/image_job.py:343`,
  `:381`).
- Tests: `tests/test_grain_step.py`, `tests/test_video_chain.py`
  (`test_video_job_refuses_a_step_after_the_main_model_instead_of_dropping_it`), Rust tests in
  `steps.rs`, `job_plan.rs`, `settings.rs` and `commands/tests.rs`, `desktop/src/lib/steps.test.ts`,
  `desktop/src/lib/stepStages.test.ts`, seven parity cases (`jobPlanParity.test.ts:591`) in
  `desktop/src-tauri/tests/fixtures/job_plan_cases.json`, and the host-built request sample
  `image_modelless_grain_step` in `tests/fixtures/job_request_samples.json`, which the worker
  resolves in `test_the_hosts_grain_sample_resolves_in_the_worker`.

To try it: start LocalSR with `LOCALSR_DEV_STEPS=1` and add
`"step_stages": [{"step": "grain", "params": {"strength": 40, "seed": 7}}]` to the `ui` object
of the settings file (a `mask_path` must be a photo added to LocalSR).

### Steps with a model, alpha output and jobs of steps alone

The plumbing background removal uses, behind the same dev gate. The tests also register
steps (and a catalog model) of their own, as `test_pipeline.py` does for model-less steps.

- Registry: `model_task` (the catalog `purposes` entry a step's models carry) and
  `output_alpha` (`src/localsr/core/steps.py:74`, `:76`), exported by
  `scripts/export_desktop_steps.py:63`; read in Rust (`desktop/src-tauri/src/steps.rs:149`,
  `:152`) and TypeScript (`desktop/src/lib/steps.ts:47`, `:49`). A step stage is now a stage
  list entry that is not a fix and is model-less or names a `model_task`
  (`is_step_stage`, `is_model_step_stage`, `steps.py:427`, `:437`; `steps.rs:192`;
  `steps.ts:125`).
- Selection: `StepStageSelection.model_id`, omitted when absent
  (`desktop/src-tauri/src/types.rs:352`, `desktop/src/lib/types.ts:319`).
- Host checks: a model id only on a step that needs one, and at start one is required
  (`desktop/src-tauri/src/commands.rs:4505`, messages at `:4536`, `:4551`); the model is
  resolved with the fix's code, now `installed_stage_model` (`commands.rs:4873`): known,
  of the step's task (`job_plan::serves_step`, `desktop/src-tauri/src/job_plan.rs:259`) and
  installed (`commands.rs:5052`). Like a fix model it travels as `model_id` and
  `model_path` on its stage (`commands.rs:5497`); the worker checks the file against the
  catalog SHA-256 before it reads it (`ModelAdapter._checkpoint_trust`). Settings load
  drops a step entry whose model this catalog does not offer for the step
  (`desktop/src-tauri/src/settings.rs:86`).
- Plan, both languages: a step with a model gets the fix's row, with the model, its download
  size and `can_download`/`needs_file`, or a `missing` row while none resolves
  (`job_plan.rs:297`, `desktop/src/lib/plan.ts:319`), so `pendingDownloads` fetches it and
  Start waits (`stepStagesReady`, `plan.ts:218`; `desktop/src/App.svelte:951`).
- Alpha output: a photo job with an `output_alpha` step keeps PNG, TIFF and WebP and saves
  JPEG and HEIC as PNG (`output_format_keeping_alpha`, `job_plan.rs:280`, in the start input
  at `:743`; `outputFormatKeepingAlpha`, `plan.ts:229`, `desktop/src/lib/startInput.ts:105`;
  enforced again at start, `keep_alpha_output_format`, `commands.rs:4247`, `:1314`,
  `:1878`). The output line then reads "PNG · folder · Saved as PNG to keep the transparent
  background." (`desktop/src/lib/enhancePanel.ts:111`).
- Jobs of steps alone: Restore with nothing picked and an added step plans the step alone
  (no "Nothing to run", `App.svelte:930`), and the host runs it at 1x without a main model
  (`no_restore_model`, `commands.rs:4755`, which now covers photos as well as videos).
- Worker: `_is_fix_only_stages` and the stage parser take model-bearing step stages, before
  the fixes or after Faces, counted with the added steps and once only
  (`src/localsr/core/pipeline.py:171`, `:203`, `:276`, `:321`). Such a stage loads its model
  as a fix does (inspect with the catalog check, `engine.load_model`, release afterwards)
  and its handler in `IMAGE_MODEL_STEP_HANDLERS` gets it as a `StepModel`
  (`src/localsr/worker/image_job.py:403`, `:453`, `:746`). A video job refuses such a stage
  (`src/localsr/worker/server.py:2202`).
- Alpha in the worker: an `output_alpha` handler returns a matte (height x width, 0..1);
  `_with_matte` multiplies it into the image's alpha (resized to the image as the save does,
  a mask keeping the alpha outside it) and leaves the colour as it was (`image_job.py:275`,
  `:696`); the runner keeps the manager's alpha in step (`:556`, `:811`), and the save
  scales any alpha to the saved picture, so a matte set before the upscaler grows with it and
  one set after it is already at the output size (`src/localsr/core/image_io.py:844`,
  `:947`).
- Tests: `tests/test_model_steps.py` (a tiny one-channel ESRGAN in a temporary catalog,
  loaded through the engine; a tampered copy refused; alpha before and after a 2x upscale, at
  8 and 16 bits, with a mask; a step-only job at 1x; parser bounds; the registry rules),
  `tests/test_video_chain.py`
  (`test_video_job_refuses_a_step_that_brings_its_own_model`), Rust tests in `steps.rs`
  (test steps via `steps::testing::with_steps`, `steps.rs:21`), `job_plan.rs`, `settings.rs`
  and `commands/tests.rs`, `desktop/src/lib/stepStages.test.ts`, `steps.test.ts`,
  `App.test.ts` ("runs an added step alone at Same size"), fourteen parity cases with
  steps and catalog models of their own (`extra_steps`, `extra_models`;
  `desktop/src/lib/jobPlanParity.test.ts:663`), and the host-built request sample
  `image_step_with_model_alone` in `tests/fixtures/job_request_samples.json`, which the
  worker resolves in `test_the_hosts_model_step_sample_resolves_in_the_worker`.

## What each tool still needs

- **Background removal** (model step, alpha out): the `remove_background` step loads the
  MIT BiRefNet general checkpoint and sets a matte. It runs in `cleanup`, before fixes and
  the primary, so the matte is computed at source size and scales with the image. The
  Backgr. row (`Keep` / `Remove`, spec in `docs/design/scan-and-background.md`) ships it;
  a fill colour and a second model are still to come.
- **Deinterlacing**: the video geometry step supports frame and field modes, field order
  selection, and progressive-source passthrough. The Scan row (`Automatic`, `Double rate`
  and four hand-set orders; spec in `docs/design/scan-and-background.md`) ships it.
- **Video stabilisation**: needs a video geometry handler and crop output dimensions passed
  to `VideoChainContext`.
- **Grain/film look for real**: the look (midtone weighting, grain size, colour grain), a UI
  row, and running after the primary in video (today refused).
- **Denoise/deblur strength**: built in 0.7.0 as `Light` / `Medium` / `Full` on a ticked Blur,
  JPEG artifacts or Noise row; see `docs/design/tools-wave.md`, section 1.
- **Object/scratch removal** (inpainting with a painted mask): a model-bearing step stage, a
  `requires_mask` registry flag, and the host command that writes a painted mask.
- **Masks per fix**: `supports_mask` on the fix steps and the painting UI; the plumbing is
  the same as above.
- **Progress text**: a model-less stage reports `stage_kind` = its id and no model id, so the
  status line reads "Processing · stage n/m" and "Preparing ." until labels are looked up by
  step id as well.
- **Memory after the primary**: a stage after the upscaler gets the whole output as a 32-bit
  float image (`_run_postprocess`, `src/localsr/worker/image_job.py`), about 12 bytes a pixel;
  a large 4× output needs a banded or tiled path before such a step ships.
- **Recipes**: `RecipeStage` already has `params` and `mask_path`, but recipes do not save
  step stages yet (`in_recipe_stages` is false for `grain`).
- **Status line and name of a step-only job**: while it runs, the activity line names the
  step's label, not its model (`activityModel` in `App.svelte`), and the result is named
  like any Restore result (`_denoised`, `output_ending` in `commands.rs`).
