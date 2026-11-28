# Fix strength, Stabilise, Dust & scratches, Slow motion

Build spec for LocalSR 0.7.0, on `rc/0.7.0`. It fills four places that
`docs/design/enhance-panel.md` reserved (lines 43, 72-74, 1036-1037, 1094) and follows that
spec's layout, copy rules and components, and the format of
`docs/design/scan-and-background.md`.
Line numbers refer to `e50be91b`; every reference also names its function or its text. Paths
are relative to `desktop/` unless they start with `docs/`, `src/localsr/`, `tests/` or
`scripts/`.

Four tools, independent of each other. The gap study's "Ranked next ten" has them at ranks 3,
5, 4 and 7:

| # | Tool | Where | Control | Stored as | Engine |
| --- | --- | --- | --- | --- | --- |
| 1 | **Fix strength** | the value of a ticked Blur, JPEG artifacts or Noise row | `Light` / `Medium` / `Full` in the row's popover | `fix_stages[].params.strength` (a number) | a mix of the fix's output with its input; no new model |
| 2 | **Stabilise** (video) | a Fix row, first in the list | `Light` / `Medium` / `Strong` in the row's popover | `step_stages` entry `stabilise` | a new video geometry step, OpenCV, to be built |
| 3 | **Dust & scratches** (photo) | a Fix row, first in the list; painting in the viewer | a paint bar in the viewer | `step_stages` entry `repair`; the paint belongs to the photo and is kept by the host | a new photo step, OpenCV inpainting, to be built |
| 4 | **Slow motion** (video) | a third group in the Frames select | two options | `video_slow_motion` beside `video_interpolation` | the existing added frames with stretched times |

What exists and is not rebuilt: `params` on a fix and on a recipe stage in every layer
(`src/lib/types.ts:305-311`, `:326-334`; `src-tauri/src/types.rs:317-340`; the request stage,
`src-tauri/src/commands.rs:5691-5701`, `:5720`), the three parameter validators
(`src/localsr/core/steps.py:591-609`, `src-tauri/src/steps.rs:368`, `src/lib/steps.ts:180-208`),
`step_stages` with its plan row in both planners (`src/lib/plan.ts:319-340`,
`src-tauri/src/job_plan.rs:240-258`), the mask blend (`_apply_stage_mask`,
`src/localsr/worker/image_job.py:336-368`), the row popover (`src/Popover.svelte`), and the
preview patch with its cache (`src/localsr/worker/patch_preview.py`). OpenCV is already a
dependency (`pyproject.toml:76`) and is packed with the engine (`packaging/tauri_worker.spec:85`).

This spec builds on `scan-and-background.md`, which is being built now: `withStepStage` in
`src/lib/stepRows.ts` (its "Behaviour"), the patch plan that drops step stages a photo cannot
run (its task S1), and `baseFps` (its task S4). Where one of these has not landed, the task
that needs it builds it as that spec says.

Build order: fix strength first (no new step), then Dust & scratches, Stabilise, Slow motion.
The shared row change ("Shared", below) is part of FS4; if ST2 or DS4 is built before it, that
task makes it.

## Decisions

| Point | Decision | Reason |
| --- | --- | --- |
| Strength: choice or number | A number in the engine and in every file, 0 to 1, default 1. The panel offers three named stops. | foundation-c.md asks for a scalar ("blend the fix output with its input by that scalar"), and the protocol sample already sends `"strength": 0.5` on a noise fix (`tests/fixtures/job_request_samples.json:131-133`). The stops can then be tuned in the window without an engine release, and a slider needs no new field. |
| Strength: the stops | `Light` 0.4, `Medium` 0.7, `Full` 1. One table (`STRENGTH_STOPS`). | Three is what a segmented row holds in a 230 px popover. `Full` and not `Strong`: nothing is stronger than the fix's own result. The two amounts are a first setting; see the open questions. |
| Strength: default | `Full`, which sends no parameter. | Every recipe, workflow and Quality pick was made at the fix's whole effect and keeps its result. A stage with parameters is also kept out of photo batches (`src-tauri/src/worker.rs:781`, `src/localsr/worker/image_batch.py:90`), so any other default would slow every batch that ticks a fix. **(deviation: the board draws `Medium ▾`)** |
| Strength: which fixes | Blur, JPEG artifacts (video: Compression), Noise. Not Colour, not De-flicker. | Half of Colour is a pale tint, which is a saturation control with other names, and Colour runs through three paths of its own (photo, video keyframes, the patch's colour planes: `image_job.py:496-507`, `src/localsr/core/video_chain.py:262-305`, `patch_preview.py:575-608`). Its row stays as it is. |
| Value of a ticked row | The strength, where the fix has one and the engine lists `fix_strength`; the model moves into the popover alone, as enhance-panel.md:43 settles. Colour, and every fix on an engine without the feature, keep the model name. | The board; open question 5 of enhance-panel.md is answered by this. |
| Unticking | Forgets the strength; a row ticked again starts at `Full`. Quick, Best, a moved order and `Change…` keep it. | The model is remembered in `preset_pins`; a second remembered value that no unticked row shows would change a result unseen. Every place that rewrites `fix_stages` already copies the whole entry (`App.svelte:2287-2295`, `:2325-2333`, `:2441-2447`, `:2636-2638`, `:2656-2658`). |
| Strength in the plan line | `Noise (light)`; nothing is added at `Full`. | No existing plan line changes. Chips keep the bare name: the tab is narrow and the row above shows the strength. |
| Stabilise: where | A Fix row, ticked like the others, first in the video list. | enhance-panel.md:72 and :1036; the list is in run order (`fixRowOrder`, `src/lib/fixRows.ts:72-89`) and geometry runs first (`PHASES`, `steps.py:16-26`). |
| Stabilise: its value | One choice, how much the camera's path is smoothed: `Light`, `Medium`, `Strong`. Each brings its own crop. | One value per row. Stronger smoothing needs larger corrections, so the crop follows from it and is known before the job runs. |
| Stabilise: the edges **(deviation)** | Always crop to fit: the picture is enlarged by 6, 11 or 19 % in the same warp that steadies it, and the frame keeps its size. The board's `Crop to fit ▾` has no second option in this build. | A smaller frame would have to reach `output_width` (`src/localsr/core/video_pipeline.py:410-411`), the export checks (`:414-430`), the Result head and the comparison view, which lays the result over the original at the same framing. With the size kept, none of them changes, and 1080p in is still 2160p out at 2×. `Crop only` and `Keep edges` are in "Later". |
| Stabilise: Result head | Unchanged: the size is the file's times Size. | See above. |
| Stabilise: order | After Scan, before the fixes, the added frames and the upscale. | Combing breaks tracking; the fixes and the upscaler then work on the steadied frame, and De-flicker and the live pictures compare against it. |
| Stabilise: preview | Not a chip of the patch. While the row's popover is open the viewer dims what the crop removes. | One frame cannot show movement; it can show what is lost. |
| Dust & scratches: where | A Fix row on a photo, first in the list. Its value opens painting in the viewer. | enhance-panel.md:72 and :1036. It runs before the fixes so they do not sharpen a scratch. |
| Dust & scratches: the mask | The window sends strokes, never pixels or paths. The host keeps them with the photo, draws the mask and names it on that photo's job. | Masks are per photo, and one start carries one set of settings (`StartBatchInput`). The host's own rule stays whole: the worker opens no path the window chose (`check_stage_masks_granted`, `commands.rs:4743-4758`). |
| Dust & scratches: a photo without paint | The step is left out for that photo. If nothing else would run on it, the photo is not started. | Ticking is a setting for the whole queue; paint is per photo. |
| Dust & scratches: the method | OpenCV inpainting (Telea), no model, for small marks. Wider marks later got MI-GAN (`fast-edit-and-large-marks.md`, Part 2). | The gap study's rank 4; foundation-c.md:282. A model was "Later". |
| Slow motion: where | A third group in the Frames select. | It is the same added frames; one select still writes the row (enhance-panel.md:50). |
| Slow motion: sound **(open)** | None, and no subtitles. The option's group says so. | The engine already leaves both out when the speed changes (`video_pipeline.py:698-716`). Stretched speech at half speed is poor and at quarter speed unusable. |
| Slow motion: storage | A new optional field `video_slow_motion`; `video_interpolation` stays `x2` or `x4`. | An older build that reads the recipe adds the frames at normal speed instead of refusing an unknown target. |
| Recipes | Strength and slow motion are saved. Stabilise and Dust & scratches are not, like Scan and Backgr. | `RecipeStage.params` exists and 0.6.3 reads past it (its `RecipeStage` has no such field, `git show v0.6.3-beta:desktop/src-tauri/src/types.rs` 315-322, and serde ignores unknown fields). Recipes do not save `step_stages` (scan-and-background.md, "Recipes, workflows"); paint belongs to one photo. |
| Fixtures | The e2e `FEATURES` (`e2e/fixtures.ts:3`) stays. Four new scenarios list the new features. | No existing screenshot changes. |

## Shared: what a Fix row can show

`src/lib/fixRows.ts` gains a third source and two optional parts; `FixList.svelte` reads them
and holds no knowledge of a single tool.

```ts
export interface RowChoice {
  /** The parameter it writes and the radiogroup's accessible name. */
  param: 'strength' | 'smoothing';
  name: 'Strength' | 'Smoothing';
  options: readonly { value: number | string; label: string; title: string }[];
  /** The value of an entry that does not name the parameter. */
  fallback: number | string;
}
export interface FixRow {
  id: string;
  source: 'fix_stage' | 'setting' | 'step_stage';   // step_stage: an entry of settings.step_stages
  label: string;
  title: string;
  tag: '' | 'Labs';
  common: boolean;
  choice?: RowChoice;      // the row's value is this choice; its popover shows it as segments
  paints?: boolean;        // the row's value opens painting in the viewer, not a popover
}
export const STEP_ROWS = new Set<string>(['stabilise', 'repair']);
```

- A row exists for a step in `STEP_ROWS` under the rules rows have today: the step's `media`
  has this kind and the engine lists its `required_feature` (`fixRows`, `fixRows.ts:92-123`).
  Both are `common`, so `More fixes` does not appear (`splitFixRows`, `:126-139`).
- A `step_stage` row is ticked when `settings.step_stages` has its step. Ticking calls
  `updateSettings({ step_stages: withStepStage(settings.step_stages, id, entry) })`, unticking
  passes `null`.
- A row with a `choice` shows the chosen option's label as its value; the popover's first line
  after the head is `<div class="seg-inline" role="radiogroup" aria-label={choice.name}>` with
  one `role="radio"` button per option (`aria-checked`, `class:active`, `title`,
  `on:keydown={radioGroupKeydown}`), as Content has them (`EnhancePanel.svelte:531-565`).
  Choosing an option leaves the popover open.
- `FixList` takes two new props: `radioGroupKeydown` and `mediaFacts` (below, tool 3).

## 1. Fix strength

### Layout

```
┌──────────────────────────────────────────────────────┐
│ Enhance                                    Recipes ▾ │
│ Task ?     [● Upscale & restore ][      Edit       ] │
├──────────────────────────────────────────────────────┤
│ YOUR PHOTO                        1600 × 1067 · JPEG │
│ Content ?  [● Photo           ][ Illustration      ] │
├──────────────────────────────────────────────────────┤
│ Fix ?                                                │
│ [ ] Blur                                             │
│ [✓] JPEG artifacts                            Full ▾ │
│ [✓] Noise                            Light · 28 MB ▾ │
│ [✓] Colour                                 DDColor ▾ │
├──────────────────────────────────────────────────────┤
│ RESULT                                   3200 × 2134 │
│ Size ?     [ Same ][● 2× ][ 3× ][ 4× ]               │
│ Quality ?  [● Quick           ][ Best              ] │
│            LocalSR Photo S                   Models… │
╞══════════════════════════════════════════════════════╡
│ JPEG → Noise (light) → Colour → Upscale 2×         ▸ │
│ Output     PNG · Desktop                           ▸ │
│                                  28 MB to download ? │
└──────────────────────────────────────────────────────┘
```

The popover, 230 px as today, with the segments above `Model`:

```
│ [✓] Noise                                    Light ▾ │
│                     ┌──────────────────────────────┐ │
│ [✓] Colour          │ Noise                        │ │
├─────────────────────│ [● Light ][ Medium ][ Full ] │─┤
│ RESULT              │ Model  RealPLKSR Denoise     │ │
│ Size ?     [ Same ][│                      Change… │ │
│ Quality ?  [● Quick │ 2 more models                │ │
│                     └──────────────────────────────┘ │
```

The three segments share the popover's width equally. The lines under them (`Model`, the
notes, `See it on this photo`, `Change…`, `2 more models`) are today's
(`FixList.svelte:164-225`).

### When it shows

| | Strength |
| --- | --- |
| Shows when | the row is Blur, JPEG artifacts, Compression or Noise, it is ticked, and `snapshot.engine.features` has `fix_strength` |
| Also shows when | the entry stores a `strength`, whatever the engine lists |
| Never | Colour, De-flicker; Edit; SeedVR2 or FlashVSR (no Fix list, `EnhancePanel.svelte:569`) |
| Engine lacks the feature, strength stored | the value still reads `Light`; in the popover `Light` stays pressed, `Full` can be pressed, the third segment is disabled; group `title` `This engine cannot change a fix's strength. Update LocalSR.` |
| Locked (`settingsLocked`) | the value stays readable and the popover does not open (`FixList.svelte:147`) |
| Restore (Same size) | the same; the last ticked fix is the job's model and takes its strength like the others |
| Several files | one strength per fix for every file of the start |

### Copy

| Where | String |
| --- | --- |
| Row value | `Full` · `Medium` · `Light` · an amount that is no stop: `55%` |
| Row value, model missing | `Light · 28 MB` (`formatBytes`); `Needs your file` and `No model for this fix` as today |
| Value `title` | the value, then the model: `Light · RealPLKSR Denoise`, `Light · RealPLKSR Denoise · 28 MB` |
| Segments | `Light`, `Medium`, `Full` (radios in the group `Strength`) |
| Segment `title` | Light `40 % of the fix` · Medium `70 % of the fix` · Full `The whole fix` |
| Fix tip, photo, engine lists `fix_strength` | `Tick what is wrong with the picture. Each problem gets its own fix at the original size, before the picture is enlarged. Quick picks a fast model for each problem, Best the most careful one. A ticked row shows how strongly its fix is applied: click it to choose Light, Medium or Full, or another model. Light and Medium mix the fixed picture with the picture before the fix, so more of its grain and texture stays. LocalSR remembers the model you choose for a problem. The plan line at the bottom shows the order and lets you change it.` |
| Fix tip, video | the same with `video` for `picture` and `frame` for the two `picture`s of the mixing sentence, then today's ending `De-flicker runs after the frames are processed.` |
| Fix tip, engine without the feature | today's (`FixList.svelte:78-81`) |
| Plan line | `Noise (light)` · `Blur (medium)` · `JPEG (light)` · `Compression (light)` · `Noise (55%)`; at `Full` the bare name |
| Plan line, accessible name | `Plan: JPEG, Noise (light), Colour, Upscale 2×` |
| Plan sheet | the row's stage name as on the plan line; nothing else changes |
| Recipes menu and library, second line | the same names: `Noise (light) → Upscale 4×` |
| Step label while running, step chips, patch chips | the bare name, as today: `Step 1 of 2 · Noise`, `Noise` |
| Host, start refused | `This engine cannot change a fix's strength. Update LocalSR.` |

### Behaviour

In `src/lib/fixRows.ts`:

```ts
export const STRENGTH_FEATURE = 'fix_strength';
export const STRENGTH_STOPS = [
  { value: 0.4, label: 'Light', title: '40 % of the fix' },
  { value: 0.7, label: 'Medium', title: '70 % of the fix' },
  { value: 1, label: 'Full', title: 'The whole fix' },
] as const;
/** The entry's strength: its `params.strength` when that is a number from 0 to 1, else 1. */
export function strengthOf(entry: { params?: Record<string, unknown> } | undefined): number;
/** The entry with that strength. At 1 the key goes, and `params` with it when it is empty. */
export function withStrength<T extends { params?: Record<string, unknown> }>(entry: T, value: number): T;
/** `Light`, `Medium`, `Full`; `55%` for an amount more than 0.005 from every stop. */
export function strengthLabel(value: number): string;
/** The step declares `strength` (steps.json). */
export function takesStrength(stepId: string, steps?: readonly StepDefinition[]): boolean;
```

- `fixRows(media, features)` gives a row its `choice` (`param: 'strength'`, `name: 'Strength'`,
  the three stops, `fallback: 1`) when `takesStrength(step.id)`. `FixList` shows the choice
  when `features` has `fix_strength` or `strengthOf(entry) < 1`.
- `fixRowValue` (`fixRows.ts:142-177`) takes the row: with a shown choice it returns
  `strengthLabel(strengthOf(entry))` in place of the model's name and keeps ` · 28 MB`; the two
  warnings win as today. The trigger's `title` is built in `FixList` (`:146`).
- A segment calls `updateSettings({ fix_stages: settings.fix_stages.map((entry) =>
  entry.fix === row.id ? withStrength(entry, value) : entry) })`. Nothing else is written; no
  model is chosen again and `quality` stays.
- `toggleFix` is untouched: a new entry is `{ fix, model_id: '' }` (`toggleFixStage`,
  `src/lib/fixStages.ts:37-47`), so it starts at `Full`.
- Start, the plan and the patch need no wiring: `fix_stages` travels whole
  (`src/lib/startInput.ts:39`, `:100`; `patchRequestKey`, `src/lib/previewPatch.ts:249`), so a
  new strength asks for a new patch after the usual 400 ms.
- Applying a workflow gives `Full` (`workflowSettings` builds entries without `params`,
  `src/lib/workflows.ts:53-56`), and a workflow keeps its ✓ only at `Full`.

### Plan line, plan sheet, chips

`planLine` (`src/lib/planLine.ts:62-72`) picks `fix_stages` as well and takes a fourth argument
`{ amounts?: boolean }`, default true. For a stage with a `fix` (`stageStepName`, `:33-37`)
whose entry has `strengthOf(entry) < 1` it appends ` (` + the label in lower case + `)`.
`patchPlanEntries` (`previewPatch.ts:147-161`) passes `{ amounts: false }`. `stepName`
(`src/lib/jobSteps.ts:25-61`) is not changed, so the running label and the chips after a job
keep the bare name. `planMarks` pairs names by position (`jobSteps.ts:88-94`) and needs nothing.
`PlanSheet.svelte` names its rows with the plan line's names.

### Recipes and workflows

- Save: `buildRecipe` (`App.svelte:4293-4365`) writes `params: { strength }` on each fix stage
  and on Restore's last stage when the entry's strength is under 1 (the two object literals at
  `:4339-4347` and `:4348-4363`).
- Apply: `recipeFixStages` (`src/lib/recipes.ts:101-125`) copies a stage's `params.strength`
  (a number from 0 to 1, under 1) to the entry it builds; other keys are not copied.
- Match: `sameFixStages` (`recipes.ts:127-135`) also compares `strengthOf` of both entries.
  A recipe saved at `Light` loses its ✓ at `Medium` and `Update “<name>”` shows.
- Line: `recipePlanLine` (`src/lib/recipeList.ts:77`) passes the recipe's fix entries to
  `planLine`.
- Host: nothing new. `validate_recipe_stages` checks a stage's `params` against its step
  (`commands.rs:1136-1143`), so `strength` passes once the registry declares it and
  `{ "strength": 2 }` is refused. The file's format does not change.
- Workflows (`src-tauri/resources/workflows.json`) are not changed.

### Preview patch

A strength change mixes again and runs no model again.

- Engine (`patch_preview.py`): a step with a strength keeps two results. The fix's whole
  result is cached under the step's key without `strength`; the mixed picture under today's
  key, which includes the parameters (`_stage_key`, `:442-458`). A request first looks for the
  mixed picture, then for the whole result, and runs the model only when neither is there.
  `Full` is the whole result itself. The mix is announced with `cached: true` when its model
  did not run, so its near-zero time is not recorded as the model's
  (`src-tauri/src/patch_preview.rs:603-605`).
- Steps after the changed fix run again: their input changed.
- Host (`patch_preview.rs`): a step whose stage differs from the shown one only in
  `params.strength` counts 0 seconds in the estimate (`:414-423`; `step_signature`, `:282-285`).
  Under `Automatic`, when the steps after it are too slow to run by themselves, the request is
  sent cut after the last step that costs nothing (`stage_limit`, `:349`), so the changed fix's
  chip is new at once and the rest waits behind `Update preview`.
- Window: the chips and the shown chip follow today's rules. To judge a strength, press the
  fix's chip or `See it on this photo` in the popover.

### Engine contract

| | |
| --- | --- |
| Parameter | `strength` on the steps `blur`, `jpeg`, `noise` (`steps.py:109-160`): `StepParameter(name="strength", kind="number", default=1.0, minimum=0.0, maximum=1.0)` |
| Feature | `fix_strength`, added to the list in `_engine_features` (`src/localsr/worker/server.py:211-229`) |
| The host sends | `"params": {"strength": 0.4}` on the fix's stage, and on the `primary` stage of Restore (`commands.rs:5691-5701`, `:5720`). At `Full` no `params` at all. |
| Mix | `result = input + strength × (fix - input)`, on the working values, in the result's own depth. One helper beside `_apply_stage_mask` (`image_job.py:336`). |
| Photo, fix before the main model | in `_run_stage`, before the mask blend and before the step's pictures are made (`image_job.py:891-903`) |
| Photo, Restore's model | in `_primary`, beside the mask blend (`image_job.py:1163-1170`), when the primary stage names a fix |
| Video, fix | in `_build_fix_model_stage`, on each frame, both branches (`video_chain.py:255`, `:257`), HDR float frames included |
| Video, Restore's model | `VideoJobConfig` gains `primary_strength: float = 1.0`, filled from the primary stage's parameters (`server.py:2323-2333`); `_primary_upscale_stage` mixes with it (`video_chain.py:469-553`) |
| Absent or 1.0 | no mix; the result is byte for byte today's |
| 0.0 | the input |
| A skipped fix | stays skipped with its note (`image_job.py:455-472`; `video_chain.py:244-247`); nothing is mixed |
| With a mask | strength first, then the mask |
| Batches | unchanged rule: a stage with `params` runs one photo at a time |

Host: `validate_fix_selections` already checks a fix's `params` against the registry
(`commands.rs:4772-4775`). `validate_stage_params_engine` (`:4617-4627`) also refuses a
`strength` under 1 when the engine does not list `fix_strength`, with the Copy table's
message. Plan rows do not change.

### Files

| File | Change |
| --- | --- |
| `src/localsr/core/steps.py:109-160` | `strength` on three steps |
| `src-tauri/resources/steps.json` | regenerated: `scripts/export_desktop_steps.py`, then `--check` |
| `src/localsr/worker/server.py:211-229`, `:2323-2333` | the feature; `primary_strength` |
| `src/localsr/worker/image_job.py:336`, `:891-903`, `:1163-1170` | helper and the two mixes |
| `src/localsr/core/video_chain.py:231-257`, `:469-553`; `video_pipeline.py:159-202` | the two video mixes; the config field |
| `src/localsr/worker/patch_preview.py:293-358`, `:442-502` | the second key and the re-mix |
| `src-tauri/src/commands.rs:4617-4627` | the engine gate |
| `src-tauri/src/patch_preview.rs:282-285`, `:349`, `:398-423` | estimate and cut |
| `src/lib/fixRows.ts`, `planLine.ts`, `recipes.ts`, `recipeList.ts`, `previewPatch.ts` | as above |
| `src/FixList.svelte` | the segments, the value, the tip |
| `src/EnhancePanel.svelte:570-585` | passes `radioGroupKeydown` to `FixList` |
| `src/App.svelte:4339-4363` | `buildRecipe` |
| `e2e/fixtures.ts` | scenario `strength` |
| Docs | `CHANGELOG.md` under 0.7.0; `docs/design/foundation-c.md:280-281`; `docs/design/enhance-panel.md:43`, `:74`, `:1037`, `:1094` point here |

Not touched: `App.svelte`'s `toggleFix`, `lib/fixStages.ts`, `lib/workflows.ts`, both planners,
`styles.css`.

### Tasks

Each ends with `./local-ci.sh frontend` green, and `rust` or `pytest` where it touches them.

| # | Task | Touches | Done when |
| --- | --- | --- | --- |
| FS1 | Engine: the parameter, the feature, the four mixes | `steps.py`, `steps.json`, `server.py`, `image_job.py`, `video_chain.py`, `video_pipeline.py`, `tests/test_fix_strength.py` (new) | The pytest cases below pass; `tests/test_steps_registry.py` and the schema sample `image_stage_params_and_mask` still pass. |
| FS2 | Engine: the patch mixes again from its cache | `patch_preview.py`, `tests/test_patch_preview.py` | A second request that differs only in `strength` loads no model (counted in the test) and answers with `cached: true`. |
| FS3 | Host: the engine gate; the patch estimate and cut | `commands.rs`, `patch_preview.rs`, their tests | The host cases below pass. |
| FS4 | Window, pure: the shared row parts, strength helpers, plan line, recipes | `lib/fixRows.ts`, `planLine.ts`, `recipes.ts`, `recipeList.ts`, `previewPatch.ts`, their tests | vitest cases "strength" pass. |
| FS5 | Window: segments, value, tip, `buildRecipe` | `FixList.svelte`, `EnhancePanel.svelte`, `App.svelte`, `App.test.ts` | App.test block "Strength" passes. By hand in `npm run tauri dev`: Noise at `Light` on a grainy photo keeps visible grain in the patch and in the result; moving between the three stops updates the Noise chip without the model loading again. |
| FS6 | e2e, screenshot, docs | `e2e/fixtures.ts`, `enhance-panel.e2e.ts`, `screens.e2e.ts`, docs | e2e green on Chromium and WebKit at both sizes; no existing baseline differs. |

### Tests

pytest, `tests/test_fix_strength.py` (a tiny 1× model in a temporary catalog, as
`tests/test_model_steps.py` builds one):

| Test | Expected |
| --- | --- |
| `test_strength_is_declared_on_blur_jpeg_and_noise_only` | kind `number`, 0 to 1, default 1.0; `colour` declares nothing |
| `test_the_engine_lists_fix_strength` | `_engine_features([])` has `fix_strength` |
| `test_full_strength_is_byte_identical` | a job with `{"strength": 1.0}` and one without `params` save the same bytes |
| `test_a_fix_is_mixed_with_its_input` | at 0.4, every pixel is `input + 0.4 × (full - input)` within one level; at 0.0 it is the input |
| `test_strength_keeps_16_bits` | a 16-bit PNG in, 16-bit out, mixed in 16 bits |
| `test_restores_own_model_takes_the_strength` | Same size with one fix as the primary: mixed as above |
| `test_a_skipped_fix_is_not_mixed` | an unstable result is skipped with its note at any strength |
| `test_video_frames_are_mixed` | `test_video_chain.py` pattern: a fix stage with 0.4 yields mixed frames; the Restore primary too |
| `test_patch_preview.py::test_a_new_strength_mixes_again_without_the_model` | FS2's condition; a third request at `Full` also loads no model |

Host (`cargo test`):

| Test | Expected |
| --- | --- |
| `commands/tests.rs::a_strength_needs_an_engine_that_lists_it` | start refused with the Copy table's message; allowed with the feature; a start without `params` allowed either way |
| `commands/tests.rs::a_recipe_keeps_a_fix_strength` | a noise stage with `{"strength": 0.4}` is saved and read back; `{"strength": 2}` is refused |
| `commands/tests.rs::the_request_carries_the_strength` | the fix stage and Restore's primary stage have `params.strength`; at `Full` neither has `params` |
| `patch_preview` tests, `a_strength_change_costs_nothing_by_itself` | estimate 0 for the changed step; with a slow step after it the request is cut and the rest is `stale` |
| parity fixture (`UPDATE_JOB_PLAN_FIXTURE=1`) | new case: a fix with `strength` gives the same plan as without, and both start inputs carry it |

vitest:

| File | Case | Expected |
| --- | --- | --- |
| `lib/fixRows.test.ts` | `strengthOf` | 1 for no `params`, a string, `-1`, `2`; 0.4 for 0.4 |
| | `withStrength` | sets the key; at 1 removes it and an empty `params`; other keys stay |
| | `strengthLabel` | `Light`, `Medium`, `Full`, `55%` |
| | `fixRows`, real registry | `blur`, `jpeg`, `noise` have the choice; `colour`, `deflicker` do not |
| | `fixRowValue` | `Light`, `Light · 28 MB`, `Needs your file`; Colour the model name |
| `lib/planLine.test.ts` | | `Noise (light)`, `Compression (medium)` on video, `Noise (55%)`, bare at 1; `{ amounts: false }` bare |
| `lib/recipes.test.ts` | | a recipe stage with `strength` round-trips through `recipeFixStages`; `sameFixStages` is false for 0.4 against 0.7 and true for absent against 1 |
| `lib/recipeList.test.ts` | | `Noise (light) → Upscale 4×` |
| `lib/previewPatch.test.ts` | | chip names stay bare; `patchRequestKey` differs for two strengths |

`App.test.ts`, block "Strength":

| Case | Expected |
| --- | --- |
| Noise ticked, feature listed | the value button reads `Full`; its `title` names the model |
| feature not listed | the value reads `RealPLKSR Denoise`, no group `Strength` (today's tests are this case) |
| open the popover | radiogroup `Strength` with `Full` checked, before `Model` |
| press `Light` | `saveSettings` gets `fix_stages: [{ fix: 'noise', model_id: …, params: { strength: 0.4 } }]`; value `Light`; plan line `Noise (light) → Upscale 4×`; the popover is still open |
| press `Full` | the entry has no `params` |
| Quick, then Best | the entry keeps `strength` |
| untick and tick Noise | `Full` |
| Colour ticked | value `DDColor`, no group |
| Same size, Noise at `Medium` | plan line `Noise (medium)` |
| save a recipe at `Light`, change to `Medium` | the recipe's stage has `params.strength` 0.4; then no ✓ and `Update “…”` |
| strength stored, feature not listed | `Light` pressed, `Medium` disabled, `Full` enabled |
| job running | the value is readable; no popover opens |

e2e, scenario `strength` (`ready` plus `fix_strength` and `stage_params_and_masks`):
`enhance-panel.e2e.ts` gains `Strength: the three segments fit the popover and arrow keys move
between them` (`scrollWidth <= clientWidth` at both sizes) and `Strength: choosing one moves no
row`. `screens.e2e.ts` gains `panel-strength` (Noise at `Light`, popover open).

## 2. Stabilise

### Layout

```
┌──────────────────────────────────────────────────────┐
│ Enhance                                    Recipes ▾ │
│ Task ?     [● Upscale & restore ][░░░░░ Edit ░░░░░░] │
├──────────────────────────────────────────────────────┤
│ YOUR VIDEO                1920 × 1080 · 24 fps · 12s │
│ Content ?  [● Video           ][ Animation         ] │
├──────────────────────────────────────────────────────┤
│ Fix ?                                                │
│ [✓] Stabilise                       Labs    Medium ▾ │
│ [ ] Blur                                             │
│ [ ] Compression                                      │
│ [✓] Noise                                   Medium ▾ │
│ [ ] Colour                                           │
│ [ ] De-flicker                                  Labs │
├──────────────────────────────────────────────────────┤
│ RESULT                                   3840 × 2160 │
│ Size ?     [ Same ][● 2× ][ 3× ][ 4× ]               │
│ Quality ?  [● Quick           ][ Best              ] │
│            LocalSR Photo S                   Models… │
│ Frames ?   [ Original                            ▾ ] │
╞══════════════════════════════════════════════════════╡
│ Stabilise → Noise (medium) → Upscale 2×            ▸ │
│ Output     MP4 · AV1 · CRF 18 · Desktop            ▸ │
└──────────────────────────────────────────────────────┘
```

The popover holds the head and the segments, nothing else:

```
│ [✓] Stabilise                       Labs    Medium ▾ │
│                     ┌──────────────────────────────┐ │
│ [ ] Blur            │ Stabilise                    │ │
│ [ ] Compression     │ [ Light ][● Medium ][ Strong]│ │
│                     └──────────────────────────────┘ │
```

While that popover is open, the viewer dims the band the crop removes (Medium: 5 % at each
edge):

```
┌ clip.mp4   1920 × 1080 · Video                                    [ Preview ] ┐
│ ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░ │
│ ░░┌───────────────────────────────────────────────────────────────────────┐░░ │
│ ░░│                                                                       │░░ │
│ ░░│          the first frame; this part stays in the picture              │░░ │
│ ░░│                                                                       │░░ │
│ ░░└───────────────────────────────────────────────────────────────────────┘░░ │
│ ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░ │
└───────────────────────────────────────────────────────────────────────────────┘
```

### When it shows

| | `Stabilise` |
| --- | --- |
| Shows when | the task is `video` with a frame-by-frame model and `features` has `video_stabilise` |
| Also shows when | the step is stored in `step_stages`, whatever the engine lists |
| Never | a photo; Edit; SeedVR2 or FlashVSR (they take no steps, `stepStageMedia`, `plan.ts:179-183`) |
| HDR kept (`video_hdr_mode: 'preserve'`) | disabled and shown unticked, `title` `Not available while HDR is preserved.`, as De-flicker (`FixList.svelte:43-61`). Choosing to keep HDR removes the entry, where it turns De-flicker off (`updateSettings`, `App.svelte:2861-2867`). |
| Engine lacks the feature, step stored | the row stays ticked and can be unticked; its value does not open; row `title` `This engine cannot stabilise. Update LocalSR.` |
| Locked | disabled with the body's `fieldset`; the value stays readable |
| Several files | one choice for every video of the start; a photo in the same queue ignores it (`active_step_stages`, `job_plan.rs:240-258`) |

### Copy

| Where | String |
| --- | --- |
| Row | `Stabilise`, tag `Labs` |
| Row `title` | `Steadies shaky handheld video. The picture is enlarged a little so its moving edges stay out of view.` |
| Value and segments | `Light`, `Medium`, `Strong` (radios in the group `Smoothing`) |
| Segment `title` | Light `Evens out the camera's path over half a second · cuts 3 % off each edge` · Medium `… over one second · cuts 5 % off each edge` · Strong `… over two seconds · cuts 8 % off each edge` |
| Fix tip, video, added before the De-flicker sentence | `Stabilise runs first: it follows how the camera moves from frame to frame, evens that path out, and enlarges the picture a little so the edges that move in and out stay hidden. The video keeps its size. Wobble from a rolling shutter and blur that the shake left in single frames stay.` |
| Plan line | `Stabilise` |
| Plan sheet row | stage `Stabilise`, note line `Medium · cuts 5 % off each edge`. No arrows, no `Change…`. |
| Step label while running | `Step 1 of 3 · Stabilise` |
| Patch, the `This frame` label's `title` | `Stabilise, De-flicker, added frames and sound cannot be shown on one frame.` (`PreviewPatch.svelte:266`) |
| Result head | unchanged |

### Behaviour

- Row: `fixRows` gives the step `source: 'step_stage'`, `tag: 'Labs'` (`LABS_ROWS`,
  `fixRows.ts:20`), `common: true`, and the choice `param: 'smoothing'`, `name: 'Smoothing'`,
  three options, `fallback: 'medium'`. With phase `geometry` it sorts first
  (`fixRowOrder`).
- Ticking writes `{ step: 'stabilise', params: { smoothing: 'medium' } }` with `withStepStage`;
  a segment replaces the entry with the new value; unticking removes it. The parameter is
  always written.
- Nothing else is wired by hand: the plan row (`role: 'step'`, first after `Deinterlace`),
  a job at Same size with nothing else ticked (`hasStepWork`, `App.svelte:987`) and the start
  request come from `step_stages`. `PlanStage['kind']` (`plan.ts:141-151`) gains `'stabilise'`.
- Names: `stageStepName` (`planLine.ts:25-59`) and `stepName` (`jobSteps.ts`) return
  `Stabilise` for the kind; the Plan sheet's note line is the chosen segment's label and the
  second half of its `title`.
- Size and Frames: no number changes. The Result head, the Frames options and the cost line
  read the same with and without the tick.
- Scan: `Deinterlace` stays the plan's first row and `Stabilise` follows it; at double rate
  the smoothing still covers the same seconds.
- Preview patch: `'stabilise'` joins `VIDEO_ONLY_KINDS` (`previewPatch.ts:135-141`), and the
  patch plan drops the stage as it drops every step a photo cannot run (scan-and-background.md,
  task S1). No chip.
- The dimmed band: `FixList` reports the open popover's crop through a new action
  `showCropGuide(fraction | null)` (0.03, 0.05, 0.08; `null` when it closes). `PreviewPane`
  takes `cropGuide: number | null` and draws, inside the stage that holds the picture
  (`PreviewPane.svelte:1532-1589`), an `aria-hidden` box inset by that fraction with a
  shadow of `rgba(0, 0, 0, 0.5)` spread outside it and a 1 px `--surface-border` line. It
  shows only on a video's still picture before a job, never on a playing comparison.
- After the job the comparison works as for every video. In `Split` the two halves do not meet
  at the divider wherever the result was moved; `Original` and `Enhanced` are the views to use.

### Engine contract

```python
StepDefinition(
    id="stabilise", media=("video",), phase="geometry", order=20, needs_model=False,
    in_image_pipeline=True, label="Stabilise", stage_label="Stabilising",
    required_feature="video_stabilise", dev_only=True,   # off in task ST4
    parameters=(StepParameter(name="smoothing", kind="choice", default="medium",
                              choices=("light", "medium", "strong")),),
)
```

| `smoothing` | The path is evened out over | Cut off each edge | Enlarged by |
| --- | --- | --- | --- |
| `light` | 0.25 s before and after each frame | 3 % | 1.064 |
| `medium` | 0.5 s | 5 % | 1.111 |
| `strong` | 1.0 s | 8 % | 1.190 |

The host sends the stage as it sends `deinterlace`: `{"kind": "stabilise", "step":
"stabilise", "params": {"smoothing": "medium"}}` among the stages before the fixes
(`image_pipeline_stages`, `commands.rs:5673-5690`). What the window relies on:

1. Width, height, frame count and every frame's time are the input's.
2. No pixel from outside the source frame is ever shown: the correction of each frame is
   limited to what the cut-off band covers.
3. The enlargement and the correction are one warp per frame (`cv2.warpAffine`, cubic), so the
   picture is resampled once.
4. Movement is measured between neighbouring frames as a shift and a turn, from tracked points
   on a grey copy no longer than 640 px (`goodFeaturesToTrack`, `calcOpticalFlowPyrLK`,
   `estimateAffinePartial2D`). A pair with too few points counts as no movement. A cut starts a
   new path, so two shots are never evened out together.
5. The stage holds back only the frames it needs to look ahead, never more than 400 MB of
   them; on frames too large for the window of the chosen level, the window is shortened.
6. With HDR kept the job is refused: `Stabilise is not yet supported with HDR preservation.`,
   beside De-flicker's refusal (`video_pipeline.py:334-335`).
7. It runs where `deinterlace` runs, after it: in the loop before the frames are added before
   upscaling and before the frame's side data is taken (`build_video_stages`,
   `video_chain.py:711-717`; the two later loops skip it, `:725`, `:746`). The live source
   pictures, De-flicker and the added frames therefore see the steadied frame.
8. Handler: `VIDEO_STEP_HANDLERS["stabilise"]` (`video_chain.py:394-401`), in a new
   `src/localsr/core/stabilise.py`.

The host and both planners need no rule of their own: `validate_step_stages`
(`commands.rs:4650-4703`), the feature gate (`:4705-4741`) and the plan row are generic.

### Files

| File | Change |
| --- | --- |
| `src/localsr/core/steps.py`, `steps.json` | the step |
| `src/localsr/core/stabilise.py` (new), `video_chain.py:394-401`, `:711-746`, `video_pipeline.py:334-335` | handler, order, HDR refusal |
| `src/lib/fixRows.ts`, `planLine.ts`, `jobSteps.ts`, `previewPatch.ts`, `plan.ts:141-151` | row, names, patch, the kind |
| `src/FixList.svelte` | `step_stage` rows; the HDR rule for this row; `showCropGuide` |
| `src/PlanSheet.svelte:86-121` | note line of the `stabilise` row |
| `src/PreviewPane.svelte`, `src/PreviewPatch.svelte:266`, `src/lib/enhancePanel.ts`, `src/App.svelte:2861-2867` | the band; the title; the action; the HDR rule |
| `e2e/fixtures.ts` | scenario `shaky` |
| Docs | `CHANGELOG.md`; `KNOWN_LIMITATIONS.md` (rolling shutter, HDR, the crop); `docs/video-support.md` under "Optional processing"; `docs/architecture/steps.md`; `docs/design/foundation-c.md:276-277`; `docs/design/enhance-panel.md:72-74` point here |

### Tasks

| # | Task | Touches | Done when |
| --- | --- | --- | --- |
| ST1 | Engine: the step, developer-only, with its handler. Write the first three tests below first. The developer-only lists and the count of added steps gain the id (`tests/test_grain_step.py:153-157`, `src-tauri/src/steps.rs:683-685`, `src/lib/steps.test.ts:119-123`). | `steps.py`, `steps.json`, `stabilise.py`, `video_chain.py`, `video_pipeline.py`, `tests/test_video_stabilise.py` (new), the three gate tests | The pytest cases pass. The worker writes into the task's notes how many milliseconds a 1080p frame costs on the CPU of the machine used. |
| ST2 | Window, pure: the row, the names, the patch | `lib/fixRows.ts`, `planLine.ts`, `jobSteps.ts`, `previewPatch.ts`, `plan.ts`, tests | vitest cases "stabilise" pass. |
| ST3 | Window: the row in `FixList`, the Plan sheet note, the HDR rule, the dimmed band | `FixList.svelte`, `PlanSheet.svelte`, `PreviewPane.svelte`, `PreviewPatch.svelte`, `lib/enhancePanel.ts`, `App.svelte`, `App.test.ts` | App.test block "Stabilise" passes. |
| ST4 | Ship: `dev_only` off, export, gate tests, e2e, screenshots, docs | `steps.py`, `steps.json`, `tests/test_steps_registry.py`, the three gate tests of ST1, `e2e/*`, docs | The engine lists `video_stabilise` without `LOCALSR_DEV_STEPS`. By hand: a handheld walking clip at `Medium` is visibly steadier, shows no black edge in any frame, and has the source's length and sound. |

### Tests

pytest, `tests/test_video_stabilise.py`. The clip is made in the test: a textured still picture
moved by a known random shift of up to 12 px per frame, 60 frames at 30 fps.

| Test | Expected |
| --- | --- |
| `test_stabilise_is_a_video_geometry_step_after_deinterlace` | phase `geometry`, order above `deinterlace`'s, model-less, choices as above |
| `test_the_result_moves_less` | the standard deviation of the frame-to-frame shift of the output (measured by phase correlation) is at most half the input's at `medium`, and `strong` is not above `medium` |
| `test_no_edge_of_the_source_shows` | the source has a 1 px magenta border; no output frame contains a magenta or black pixel |
| `test_size_count_and_times_are_kept` | same width, height, frame count and timestamps as the input |
| `test_a_still_clip_only_gets_larger` | every output frame equals the input enlarged by 1.111 about its centre, within two levels |
| `test_a_cut_starts_a_new_path` | two shots in one clip: the frames next to the cut are not shifted towards the other shot |
| `test_hdr_preserve_is_refused` | the message of rule 6 |
| `test_it_runs_before_the_fixes_and_after_deinterlace` | with a fix stage and `deinterlace` in the job, the fix receives the steadied frame |
| `test_feature_is_listed_without_the_dev_switch` (ST4) | `video_stabilise` with the variable unset |

Host: `job_plan.rs::stabilise_follows_deinterlace_in_a_frame_by_frame_video_plan` (present with
fixes and alone at Same size; absent for SeedVR2 and for a photo task); new parity cases
(video with `stabilise`; with `deinterlace` and `stabilise`; stored on a photo task);
`commands/tests.rs::the_patch_plan_leaves_video_only_steps_out` gains `stabilise`.

vitest: `lib/fixRows.test.ts` (video: first row `stabilise`, `Labs`, `common`, the choice; no
row on a photo or without the feature), `lib/planLine.test.ts` and `lib/jobSteps.test.ts`
(`Stabilise`), `lib/previewPatch.test.ts` (no entry).

`App.test.ts`, block "Stabilise":

| Case | Expected |
| --- | --- |
| video, feature listed | checkbox `Stabilise` first in the group `Fix`, unticked, with `Labs` |
| photo; feature not listed; SeedVR2 chosen | no such checkbox |
| tick | `step_stages: [{ step: 'stabilise', params: { smoothing: 'medium' } }]`; value `Medium`; plan line starts `Stabilise`; the Result head is unchanged |
| press `Strong` | `params: { smoothing: 'strong' }`; the popover stays open |
| untick | `step_stages: []` |
| a `deinterlace` entry stored beside it | ticking and unticking leave it; plan line `Deinterlace → Stabilise → …` |
| `video_hdr_mode: 'preserve'` | disabled, unticked, the `title`; choosing Preserve with the step stored saves `step_stages` without it |
| Same size, nothing else | no `Nothing to run`; Start enabled |
| popover open | `PreviewPane` gets `cropGuide` 0.05; closed, `null` |
| Plan sheet | row `Stabilise` with the note `Medium · cuts 5 % off each edge` |

e2e, scenario `shaky` (`video` plus `video_stabilise` and `stage_params_and_masks`):
`Stabilise: ticking moves no other row and changes no number in the head`, `Stabilise: the
band shows while the popover is open and goes when it closes`. `screens.e2e.ts`: `panel-stabilise`
(ticked, popover open, band in the viewer).

## 3. Dust & scratches

0.7.0 adds a model for wider marks (MI-GAN, 28 MB, downloaded with the first start that has a
painted photo): Part 2 of `fast-edit-and-large-marks.md` has its decisions, copy and states. The
row, strings and engine contract below are the ones without that model, which an older engine or
catalog keeps.

### Layout

The row and its three values:

```
│ [ ] Dust & scratches                                 │
│ [✓] Dust & scratches                          Paint… │
│ [✓] Dust & scratches                          1 mark │
│ [✓] Dust & scratches                         3 marks │
```

In the panel, with other work:

```
├──────────────────────────────────────────────────────┤
│ Fix ?                                                │
│ [✓] Dust & scratches                         3 marks │
│ [ ] Blur                                             │
│ [ ] JPEG artifacts                                   │
│ [✓] Noise                                     Full ▾ │
│ [ ] Colour                                           │
├──────────────────────────────────────────────────────┤
│ RESULT                                   3200 × 2134 │
│ Size ?     [ Same ][● 2× ][ 3× ][ 4× ]               │
│ Quality ?  [● Quick           ][ Best              ] │
│            LocalSR Photo S                   Models… │
╞══════════════════════════════════════════════════════╡
│ Dust & scratches → Noise → Upscale 2×              ▸ │
│ Output     PNG · Desktop                           ▸ │
```

At Same size with nothing else the repair is the whole job:

```
│ RESULT                                   1600 × 1067 │
│ Size ?     [● Same ][ 2× ][ 3× ][ 4× ]               │
│ Quality ?  [● Quick           ][ Best              ] │
╞══════════════════════════════════════════════════════╡
│ Dust & scratches                                   ▸ │
│ Output     PNG · Desktop                           ▸ │
```

The viewer while painting. The bar sits at the top of the canvas, centred, 8 px under the
head; it wraps to two lines when the viewer is narrower than the bar.

```
┌ IMG_2041.jpg   1600 × 1067                                                    ┐
│     ┌───────────────────────────────────────────────────────────────────┐     │
│     │ [● Paint ][ Erase ]  ──●────────  12 px   Undo  Clear  ? [ Done ] │     │
│     └───────────────────────────────────────────────────────────────────┘     │
│                                                                               │
│        the photo at the zoom you chose, sharp at 100 % and above              │
│        painted places are tinted                 ░░                           │
│        the pointer is a ring as wide as the brush        o                    │
│                                                                               │
│                                            [ -  200%  +  Fit  1:1  ◐ ]        │
└───────────────────────────────────────────────────────────────────────────────┘
```

The view switch, the step chips and the `Preview` button are not in the head while painting.
The zoom strip at the bottom is today's (`PreviewPane.svelte:1638-1667`).

### When it shows

| | `Dust & scratches` |
| --- | --- |
| Row shows when | the task is Upscale & restore on a photo (`upscale` or `denoise`) and `features` has `image_repair` |
| Also shows when | the step is stored in `step_stages`, whatever the engine lists |
| Never | a video; Edit |
| Value, ticked | `Paint…` while the open photo has no paint; `1 mark`, `3 marks` once it has. Either opens painting. No photo open: `Paint…`, disabled, `title` `Open a photo first.` |
| Unticked | no value. The paint stays with its photos and counts again when the row is ticked. |
| Locked, or a job is waiting | the value is disabled, `title` `Available when the job finishes.`; painting closes |
| Engine lacks the feature, step stored | the row stays ticked and can be unticked; the value is disabled; row `title` `This engine cannot repair marks. Update LocalSR.` |
| Several photos | the tick is one setting for all; each photo has its own paint. A photo without paint skips the step. |

### Copy

| Where | String |
| --- | --- |
| Row | `Dust & scratches` |
| Row `title` | `Repairs small marks that you paint over: dust, scratches, spots.` |
| Value | `Paint…` · `1 mark` · `3 marks` |
| Value `title` | no paint: `Paint over the marks on this photo` · with paint: `3 marks on this photo. Click to change them.` · with several photos in the queue, added: ` 5 of 30 photos have marks.` |
| Bar, accessible name | `Dust & scratches` (a `role="toolbar"`) |
| Bar, mode | `Paint`, `Erase` (radios in the group `Brush`); `title` `Paint over a mark · B`, `Take paint away · E` |
| Bar, size | a range input, accessible name `Brush size`, `title` `Brush size · [ and ]`; beside it `12 px` |
| Bar, buttons | `Undo` (`title` `Undo · ⌘Z`, elsewhere `Undo · Ctrl+Z`) · `Clear` (`title` `Take every mark off this photo`) · `Done` (`title` `Finish painting · Esc`) |
| Bar tip (`About Dust & scratches`) | `Paint over each mark: a speck of dust, a scratch, a spot. When you start, LocalSR fills the painted places from the picture around them, before the other fixes and the upscale. This works for small marks on even areas such as sky, walls and skin. A large area, or one that crosses fine detail, comes out smeared. Zoom in with the wheel to see small marks; hold Space and drag to move the picture. B paints, E erases, [ and ] change the brush, and holding M hides the paint. Each photo keeps its own marks, and a photo without marks is left as it is.` |
| Fix tip, photo, added at the end | ` Dust & scratches repairs only the places you paint over.` |
| Foot note, the tick is the only work and the open photo has no paint | `Nothing to run. Paint over the marks first, or pick another fix.` |
| Plan line | `Dust & scratches` |
| Plan sheet row | stage `Dust & scratches`, name `3 marks`. No arrows, no `Change…`. |
| Step label while running | `Step 1 of 3 · Dust & scratches` |
| Step chip after a job | `Dust & scratches`, `title` `The picture after the marks were repaired` |
| Preview patch chip | `Dust & scratches`; until task DS7 it is disabled and struck through with the engine's `A masked step is not shown in the preview patch.` (`patch_preview.py:48`) |
| File name, the repair alone | `_repaired` in place of `_denoised` |
| Host, paint changed while a job waits | `Marks can be changed when the queue is done.` |
| Host, start with the tick as the only work and no painted photo | `Paint over the marks on a photo first.` |
| `workflows.json:9`, Old scan `note` | `Enlarges and sharpens, and smooths film grain. Scratches and dust stay unless you tick Dust & scratches and paint over them. Colours are not changed.` |

### Painting

**Opening.** Ticking the row opens painting when a photo is open and has no paint. The row's
value opens it at any time. Painting is a state of the window (`paintingRepair` in
`App.svelte`), not a setting.

**What changes in the viewer.** The comparison view becomes `Original` and its switch, the step
chips and the `Preview` button leave the head; the preview patch hides and comes back with the
last view when painting ends. The sharp region the viewer loads at 100 % and above stays, so
small marks can be seen (`currentOriginalRegion`, `PreviewPane.svelte:1562-1575`). Zoom is not
changed on opening.

**The paint.** An `<svg>` in the stage, over the photo, with `viewBox="0 0 <width> <height>"`
in source pixels, so it follows zoom and pan with the picture and stays sharp at any zoom. The
strokes are paths inside one `<mask>` in the order they were made, round caps and joins,
`stroke-width` the brush size: a paint stroke white, an erase stroke black. One rectangle in
`--mask-tint` shows through that mask. `--mask-tint` is `rgba(255, 43, 214, 0.55)` in every
palette: a colour old photos do not have, told from them by its shape as well.

**Pointer.**

| Input | Does |
| --- | --- |
| Press and drag, main button | a stroke in the current mode. A press without a move is a dot. |
| Alt held | erases while it is held |
| Space held and drag, or the middle button | moves the picture, with today's drag (`pointerDown`, `pointerMove`, `PreviewPane.svelte:1111-1171`), which also takes the middle button while painting |
| Wheel, the zoom strip | zoom, as today (`onWheel`, `:1105-1109`) |
| Pointer over the canvas | the system pointer is hidden and a ring shows: the brush's width at the current zoom, a white line inside a black one; under 6 px wide it is a crosshair. With Space held it is the hand. |

A stroke's points are taken in source pixels (the inverse of the `<svg>`'s screen matrix),
rounded to one decimal, and a new point is kept when it is at least an eighth of the brush
from the last one. Points outside the photo are held to its edge.

**Brush size.** In source pixels, so it means the same at every zoom. Smallest 2; largest the
photo's longer side / 20, held between 40 and 400; first size the longer side / 250, held
between 4 and 64. `[` and `]` divide and multiply it by 1.25. The size is kept for the session.

**Keys**, while painting and focus is not in a text field or a dialog (the guard of
`windowKeyDown`, `:1222-1228`):

| Key | Does |
| --- | --- |
| `B` · `E` | Paint · Erase |
| `[` · `]` | smaller · larger brush |
| `⌘Z` / `Ctrl+Z` | undo |
| `⇧⌘Z` / `Ctrl+Y` / `Ctrl+Shift+Z` | redo |
| `M`, held | hides the paint (the viewer's hold-to-see-the-original key, `holdsOriginalKey`, `:1218-1220`) |
| `Esc` | Done |

The native menus have no Undo item (`src-tauri/src/native_menu.rs:276-310`: LocalSR, File,
Presets, View, Window, Help), so the keys reach the window. The bar's controls are ordinary
buttons in tab order; opening moves focus to `Paint`, and `Done` returns it to the row's value.

**Undo.** One list of strokes and one redo list per photo, for the session. `Clear` is one
step that undo brings back. `Undo` and `Clear` are disabled when there is nothing to take.

**Saving.** 300 ms after a stroke ends, and on `Done`, the window sends the photo's whole list
to the host (`set_repair_strokes`). Nothing is lost by closing the app; the undo history is.

**Another photo.** Selecting another photo while painting keeps painting on: the bar stays, the
paint and the undo history are that photo's. Selecting a video, removing the photo, starting a
job or unticking the row ends painting.

**Without paint.** A ticked row adds nothing to a photo without paint: the plan line does not
name the step for it, and Start runs the rest. When the tick is the only work, a photo without
paint is not started: `Only this one` is off for it with the Copy table's note, and `Restore
all N` counts and starts the painted photos only.

### Behaviour in code

New `src/lib/maskPaint.ts`, pure:

```ts
export interface RepairStroke {
  mode: 'paint' | 'erase';
  /** Brush width in source pixels, 1 to 1024. */
  size: number;
  /** x0, y0, x1, y1, … in source pixels with one decimal; two numbers are a dot. */
  points: number[];
}
export const MAX_STROKES = 2000;
export const MAX_NUMBERS = 100_000;   // over all strokes of one photo
export function brushLimits(image: Size): { min: number; max: number; first: number };
export function stepBrush(size: number, key: '[' | ']', image: Size): number;
export function addPoint(stroke: RepairStroke, x: number, y: number, image: Size): RepairStroke;
export function strokePath(stroke: RepairStroke): string;            // the path's `d`
export function paintMarks(strokes: readonly RepairStroke[]): number; // the paint strokes
export interface PaintHistory { strokes: RepairStroke[]; undone: RepairStroke[][] ; past: RepairStroke[][] }
export function commit(history: PaintHistory, strokes: RepairStroke[]): PaintHistory;
export function undo(history: PaintHistory): PaintHistory;
export function redo(history: PaintHistory): PaintHistory;
```

New `src/MaskPaint.svelte` (the `<svg>`, the pointer handling, the ring) and
`src/PaintBar.svelte` (the bar). `PreviewPane` takes `painting: boolean`, `paintStrokes`,
`onPaint(strokes)` and `onPaintDone()`, renders `MaskPaint` beside the patch
(`PreviewPane.svelte:1591`) with the stage's style, and the bar inside the canvas. `App.svelte`
holds `paintingRepair`, the history per media id, the brush size, and the two host calls.

The row: `fixRows` gives `repair` the source `step_stage`, `common: true`, `paints: true`; with
phase `cleanup` it sorts first. `FixList` takes `mediaFacts: { marks: number; marked: number;
photos: number; open: boolean }` and the action `paintRepair()`; the value is a
`button.fix-value` without a chevron. Ticking writes `{ step: 'repair' }`.

The plan knows whether the open photo is painted, as it knows whether the open video has sound
(`has_audio`, `App.svelte:1527`): `PlanJobOptions` gains `masked_steps?: string[]`
(`src/lib/api.ts:208-215`; `commands.rs:1248`; `job_plan.rs:81`), which the window fills with
`['repair']` when the open photo's `repair_marks` is above 0. Both planners leave a step with
`requires_mask` out unless it is listed (`active_step_stages`, `job_plan.rs:240-258`;
`activeStepStages`, `plan.ts:189-200`). `hasStepWork` and `nothingToRunReason`
(`App.svelte:987-992`) follow from that, and `runnableMedia` (`:1043-1045`) leaves out a photo
without paint when the tick is the only work.

Names: `stageStepName` and `stepName` return `Dust & scratches` for the kind `repair`;
`resultStepChips` (`jobSteps.ts:135-168`) uses the Copy table's `title`; the Plan sheet's name
for the row is the value text. `PlanStage['kind']` gains `'repair'`.

### Engine and host contract

```python
StepDefinition(
    id="repair", media=("image",), phase="cleanup", order=3, needs_model=False,
    supports_mask=True, requires_mask=True, in_image_pipeline=True,
    label="Dust & scratches", stage_label="Repairing marks",
    required_feature="image_repair", dev_only=True,   # off in task DS6
)
```

| Side | Addition |
| --- | --- |
| Registry | `StepDefinition.requires_mask: bool = False` (`steps.py:63`), exported (`scripts/export_desktop_steps.py:55-66`), read in Rust (`steps.rs:129`) and TypeScript (`steps.ts:31`) |
| Worker, parsing | a stage of a `requires_mask` step without `mask_path` is refused: `The repair stage needs a mask.` (`src/localsr/core/pipeline.py:239-248`) |
| Worker, the step | `IMAGE_STEP_HANDLERS["repair"]` (`image_job.py:592-599`): loads the mask at the picture's size (`load_mask`, `src/localsr/core/face_compositing.py:21-49`), takes every value above 0.5 as painted, and fills those pixels with `cv2.inpaint(…, 3, cv2.INPAINT_TELEA)`, one colour channel at a time as 32-bit float so 16-bit pictures keep their depth. Transparency is not touched. An empty mask returns the picture. The mask blend that follows in `_run_stage` (`:895-900`) keeps every pixel outside the paint as it was. |
| Stage sent | `{"kind": "repair", "step": "repair", "mask_path": "<the host's file>"}` before the fixes, and before `remove_background` (order 3 against 5) |
| Engine features | `image_repair`; the stage also needs `stage_params_and_masks`, which is checked today (`commands.rs:4735-4740`) |
| Host, the paint | columns on `media`: `repair_strokes TEXT NOT NULL DEFAULT ''` (the JSON list), `repair_marks INTEGER NOT NULL DEFAULT 0`, `repair_rev INTEGER NOT NULL DEFAULT 0`, added like the other late columns (`src-tauri/src/database.rs:145`); the last two join `MEDIA_COLUMNS` (`:16`) and `MediaItem` (`types.rs`, `src/lib/types.ts:170-189`), both optional for the window |
| Host, commands | `get_repair_strokes(media_id) -> RepairStroke[]` and `set_repair_strokes(media_id, strokes)`. The second checks: the media is a probed photo; at most 2000 strokes and 100 000 numbers; every size from 1 to 1024; every number finite; no job running or waiting. It stores the list, counts the paint strokes, raises `repair_rev`. |
| Host, the mask | `repair_mask_path(state, media) -> Option<PathBuf>`: `None` without paint; else `<app data>/masks/<media id>-<rev>.png`, drawn on first use: 8-bit grey, the photo's width and height, a pixel white when its centre is within half the brush of a paint stroke's line and no later erase stroke covers it. Older files of the same photo are deleted then; removing the media or clearing the queue deletes them too (`remove_media_checked`, `commands.rs:478`; `clear_media_checked`, `:570`). |
| Host, a job | for each photo, a `requires_mask` step gets `mask_path` from `repair_mask_path`, or is left out (`build_job_request`, `commands.rs:5401`; `image_pipeline_stages`, `:5673-5690`). A photo left with nothing to run is not queued; a start that queues nothing is refused with the Copy table's message (`start_jobs_checked`, `:1348`). |
| Host, validation | the settings and the start input carry `{ "step": "repair" }` without a mask; only the worker asks for one, on the stage the host built. A `mask_path` that the window sends on a `requires_mask` step is refused (`validate_step_stages`, `:4650-4703`): `The mask of Dust & scratches is painted in LocalSR.` |
| Host, name | `output_ending` (`:5868-5890`): a job that is the repair alone ends in `_repaired` |

The mask lives in the host's own folder and not in a job's scratch folder, as foundation-c.md
(line 160-162) first proposed, so that the patch and every job of the photo read one file.

### Preview patch (task DS7, after the rest)

Until DS7 the chip is struck through and the other steps still show. With DS7:

- Worker: `_skip_reason` (`patch_preview.py:504-513`) lets a `requires_mask` step through.
  `_run_one` cuts the mask to the padded region (`geometry.box`), writes it to the patch's work
  folder and runs the stage with that file. A region without paint gives the picture unchanged.
- Host: `patch_plan` (`commands.rs:5750`) fills the mask as a job does.
- Window: `patchRequestKey` (`previewPatch.ts:231-262`) gains the photo's `repair_rev`, so
  `Done` asks for a new patch.

### Files

| File | Change |
| --- | --- |
| `src/localsr/core/steps.py`, `scripts/export_desktop_steps.py`, `steps.json` | `requires_mask`; the step |
| `src/localsr/core/pipeline.py:239-248`, `src/localsr/worker/image_job.py:592-599` | the refusal; the handler |
| `src-tauri/src/steps.rs`, `types.rs`, `database.rs`, `commands.rs`, `job_plan.rs`, `lib.rs`; new `src-tauri/src/repair_mask.rs` | flag, fields, columns, commands, the drawing, the job, the plan option |
| `src/lib/maskPaint.ts`, `src/MaskPaint.svelte`, `src/PaintBar.svelte` (new) | painting |
| `src/PreviewPane.svelte`, `src/App.svelte`, `src/FixList.svelte`, `src/EnhancePanel.svelte`, `src/PlanSheet.svelte` | wiring, the row, the sheet's name |
| `src/lib/fixRows.ts`, `planLine.ts`, `jobSteps.ts`, `plan.ts`, `api.ts`, `types.ts`, `steps.ts`, `jobPlanClient.ts`, `enhancePanel.ts` | as above |
| `src/styles.css` | `--mask-tint`; `.paint-bar`, `.mask-paint`, `.brush-ring` |
| `src-tauri/resources/workflows.json:9` | the Old scan note |
| `e2e/fake-host.ts`, `e2e/fixtures.ts` | the two commands, kept per media in the page; scenario `scratched` |
| Docs | `CHANGELOG.md`; `README.md` feature list; `KNOWN_LIMITATIONS.md` (small marks only); `docs/architecture/steps.md` (`requires_mask`); `docs/design/foundation-c.md:282-283`; `docs/design/enhance-panel.md:72` points here |

### Tasks

| # | Task | Touches | Done when |
| --- | --- | --- | --- |
| DS1 | Engine: `requires_mask`, the step (developer-only), the handler; the gate tests as in ST1 | `steps.py`, export script, `steps.json`, `pipeline.py`, `image_job.py`, `tests/test_repair_step.py` (new), the three gate tests | The pytest cases pass. |
| DS2 | Host: the paint store, the two commands, the drawing | `repair_mask.rs`, `database.rs`, `types.rs`, `commands.rs`, `lib.rs`, tests | Host cases "paint" and "mask" pass. |
| DS3 | Host and planners: the mask on a job, `masked_steps`, a photo without paint, `_repaired` | `commands.rs`, `job_plan.rs`, `steps.rs`, `lib/plan.ts`, `api.ts`, `jobPlanClient.ts`, the parity fixture | Host cases "job" pass; parity passes on the rebuilt fixture. |
| DS4 | Window, pure: `maskPaint.ts`, the row, the names | `lib/maskPaint.ts`, `fixRows.ts`, `planLine.ts`, `jobSteps.ts`, tests | vitest cases "paint" pass. |
| DS5 | Window: `MaskPaint`, `PaintBar`, the viewer and app wiring, the row's value | the Svelte files above, `styles.css`, `App.test.ts`, `PreviewPane.test.ts` | App.test block "Dust & scratches" passes. |
| DS6 | Ship: `dev_only` off, export, gate tests, fake host, e2e, screenshots, the workflow note, docs | as ST4, plus `fake-host.ts`, `workflows.json`, `lib/workflows.test.ts` | The engine lists `image_repair` without the switch. By hand: on a scanned print, five specks and one hair-thin scratch on sky are gone in the result at 2×, the rest of the picture is unchanged at `Same`, and an EXIF-rotated photo is repaired where it was painted. |
| DS7 | The patch shows the step | `patch_preview.py`, `commands.rs`, `lib/previewPatch.ts`, tests | The chip shows the region with the painted mark gone; a second request after more paint runs only this step and the ones after it. |

### Tests

pytest, `tests/test_repair_step.py`:

| Test | Expected |
| --- | --- |
| `test_repair_is_a_photo_cleanup_step_that_needs_a_mask` | image only, phase `cleanup`, before `remove_background`, `supports_mask` and `requires_mask`, model-less |
| `test_a_stage_without_a_mask_is_refused` | the message above |
| `test_a_painted_speck_is_filled_from_its_surroundings` | a smooth gradient with a black 5 × 5 speck, mask over it: every repaired pixel is within 4 levels of the gradient |
| `test_pixels_outside_the_mask_do_not_change` | byte-identical outside |
| `test_an_empty_mask_changes_nothing` | byte-identical |
| `test_16_bits_and_alpha_are_kept` | a 16-bit RGBA PNG: 16-bit out, alpha unchanged |
| `test_the_mask_is_fitted_to_the_picture` | a mask at half the picture's size repairs the same place |
| `test_repair_runs_before_the_fixes_and_alone_at_same_size` | stage order; a job of the step alone saves a file |
| `test_a_video_job_refuses_repair` | the media refusal (`pipeline.py:218-225`) |
| `test_patch_preview.py::test_repair_is_cut_to_the_region` (DS7) | the mark inside the region is gone; with the mark outside, the picture is the input |

Host (`cargo test`):

| Test | Expected |
| --- | --- |
| `repair_mask.rs::a_dot_is_a_filled_circle` | size 10 at (20, 20): 255 where the centre distance is at most 5, else 0 |
| `::a_line_has_round_ends_and_joins` | three points; pixels checked at the ends, the bend and beside the line |
| `::erase_takes_paint_away_and_later_paint_returns` | paint, erase across it, paint again |
| `::the_mask_has_the_photos_size_and_one_channel` | the PNG's header |
| `commands/tests.rs::paint_is_kept_per_photo` | two photos, two lists; `repair_marks` 2 and 0; `repair_rev` rises with each save |
| `::paint_is_refused_while_a_job_waits`, `::too_much_paint_is_refused`, `::paint_on_a_video_is_refused` | the messages; nothing stored |
| `::removing_a_photo_removes_its_mask_file` | the folder is empty afterwards |
| `database/tests.rs::old_databases_gain_the_paint_columns` | a database without them loads with `''`, 0, 0 |
| `commands/tests.rs::a_painted_photo_gets_the_stage_and_an_unpainted_one_does_not` | two photos in one start, `repair` and a fix ticked: stage with `mask_path` on the first request only |
| `::a_start_of_repair_alone_queues_only_painted_photos` | one job; with no painted photo the Copy table's refusal |
| `::a_mask_path_from_the_window_is_refused` | the message |
| `::the_repair_alone_is_named_repaired` | `_repaired.png`; with a fix `_denoised`; with an upscale `_upscaled_2x` |
| `job_plan.rs::repair_is_planned_only_for_a_painted_photo` | first row with `masked_steps: ["repair"]`; absent without; absent on a video task |
| parity fixture | new cases: `repair` listed and not listed; with `remove_background` (repair first) |

vitest, `lib/maskPaint.test.ts`: `brushLimits` (6000 × 4000: 2, 300, 24; 800 × 600: 2, 40, 4);
`stepBrush` stays inside the limits; `addPoint` drops a point closer than an eighth of the
brush, rounds to one decimal and holds a point to the picture; `strokePath` of one point is a
zero-length line; `paintMarks` counts paint strokes only; `commit`, `undo`, `redo` (a new
stroke empties redo; `Clear` is one step). `lib/fixRows.test.ts`: photo, first row `repair`,
`paints`, no row on a video or without the feature. `lib/planLine.test.ts`,
`lib/jobSteps.test.ts`: the name and the chip's `title`.

`App.test.ts`, block "Dust & scratches":

| Case | Expected |
| --- | --- |
| photo, feature listed | checkbox `Dust & scratches` first in `Fix`, unticked, no value |
| video; Edit; feature not listed | no such checkbox |
| tick, photo without paint | `step_stages: [{ step: 'repair' }]`; the toolbar `Dust & scratches` is in the viewer with focus on `Paint`; the view switch and `Preview` are gone |
| tick, photo with 3 marks | value `3 marks`; no toolbar until the value is pressed |
| a stroke (pointer down, two moves, up on the canvas) | after 300 ms `set_repair_strokes` gets one `paint` stroke whose points are source pixels; value `1 mark` once the snapshot says so |
| `E`, a stroke; `B` | an `erase` stroke; `Paint` checked again |
| `]`, `[` | the size text changes by 1.25 and stays in the limits |
| `Undo`, `⌘Z`, `⇧⌘Z` | the list shrinks and grows; `Undo` disabled when empty |
| `Clear`, then `Undo` | empty list saved; the strokes are back |
| `Esc` | no toolbar; focus on the row's value; the view switch is back |
| select another photo while painting | the toolbar stays; `get_repair_strokes` is asked for the new id |
| select a video while painting | no toolbar |
| Same size, nothing else, no paint | the Copy table's note; plan line `Nothing to run`; Start disabled |
| Same size, nothing else, 3 marks | plan line `Dust & scratches`; Start enabled |
| 2× with Noise, no paint | plan line `Noise → Upscale 2×` |
| three photos, one painted, the tick alone | the all-button counts 1 |
| job running | the value is disabled with its `title` |

`PreviewPane.test.ts`: with `painting`, a drag on the canvas does not pan and a drag with Space
held does; the wheel still zooms.

e2e, scenario `scratched` (`ready` plus `image_repair` and `stage_params_and_masks`, two
photos, the first with three marks): `Dust & scratches: the bar fits the viewer at 1024 × 700
and wraps instead of being cut`, `…: a stroke is tinted where the pointer went, at 100 % and at
400 %` (the tint's box against the pointer's path, ± 2 px), `…: every control of the bar is
reached by Tab and shows a focus ring`, `…: the ring is as wide as the brush at the current
zoom`. `screens.e2e.ts`: `panel-dust` (ticked, `3 marks`) and `viewer-paint` (painting, three
marks shown).

## 4. Slow motion

### Layout

```
│ RESULT                             3840 × 2160 · 24s │
│ Size ?     [ Same ][● 2× ][ 3× ][ 4× ]               │
│ Quality ?  [● Quick           ][ Best              ] │
│            LocalSR Photo S                   Models… │
│ Frames ?   [ 2× slower · 24s                     ▾ ] │
╞══════════════════════════════════════════════════════╡
│ Noise → Upscale 2× → Slow motion 2×                ▸ │
│ Output     MP4 · AV1 · CRF 18 · no sound · Desktop ▸ │
```

The select's list, on a 12 s clip at 24 fps:

```
│ Frames ?   [ 2× slower · 24s                     ▾ ] │
│          ┌───────────────────────────────────────┐   │
│          │   Original                            │   │
│          │ Add frames after upscaling            │   │
│          │   2× · 48 fps                         │   │
│          │   4× · 96 fps                         │   │
│          │   60 fps                              │   │
│          │   120 fps                             │   │
│          │ Add frames before upscaling (slower)  │   │
│          │   2× · 48 fps · before upscaling      │   │
│          │   …                                   │   │
│          │ Slow motion (no sound)                │   │
│          │ ✓ 2× slower · 24s                     │   │
│          │   4× slower · 48s                     │   │
│          └───────────────────────────────────────┘   │
```

### When it shows

The group is in the Frames select whenever the select is shown (frame-by-frame video,
`EnhancePanel.svelte:693`) and `features` has `video_slow_motion`. A stored choice on an
engine without the feature is listed as the one disabled option of the group while it is the
value. The whole select is disabled as today while HDR is kept or the engine cannot add frames
(`:702-707`).

### Copy

| Where | String |
| --- | --- |
| Group | `Slow motion (no sound)` |
| Options | `2× slower · 24s`, `4× slower · 48s`: the clip's length times the factor (`formatDuration`, `src/lib/format.ts:8`). Without a known length: `2× slower`, `4× slower`. |
| Frames tip, added before `RIFE · Labs makes the frames` | ` Slow motion adds the same frames after upscaling and plays them at the clip's own rate, so the video becomes two or four times as long. It is saved without sound and subtitles.` |
| Result head | ` · 24s` after the size, in place of a rate: `3840 × 2160 · 24s` |
| Plan line | `Slow motion 2×`, `Slow motion 4×` |
| Plan sheet, the frames row's note | `2× slower · after upscaling · Labs` |
| Step label while running | `Step 3 of 3 · Slow motion 2×` |
| Output line | ` · no sound` before the folder, when the file has sound: `MP4 · AV1 · CRF 18 · no sound · Desktop` |
| Output sheet, `Clean up audio` | disabled and unticked, `title` `Slow motion has no sound.` |
| Recipes, second line | `Noise → Upscale 2× → Slow motion 2×` |
| File name | `_slow` after the usual ending: `clip_video_2x_slow.mp4` |
| Engine note on the result, source had sound | `Slow motion · saved without sound` |
| Host, start refused | `This engine cannot make slow motion. Update LocalSR.` |

### Behaviour

- Setting: `video_slow_motion?: boolean` in `UiSettings`, `Recipe` and `StartBatchInput`
  (`src/lib/types.ts`; `src-tauri/src/types.rs`, `#[serde(default)]`). It counts only while
  `video_interpolation` is `x2` or `x4`.
- The select (`EnhancePanel.svelte:700-733`): two more values, `slow:x2` and `slow:x4`. They
  write `{ video_interpolation: 'x2' | 'x4', video_interpolation_order: 'after_upscale',
  video_slow_motion: true }`; every other option also writes `video_slow_motion: false`. The
  value shown is `slow:<target>` when the flag is set and the target is `x2` or `x4`.
- Head: `outputFps` (`:333-339`) is `null` with slow motion; the head then shows the new
  length when the file's is known (`:590-597`). With Scan at double rate the head shows both:
  ` · 50 fps · 24s`.
- Names: `stageStepName` (`planLine.ts:49-55`) and `stepName` (`jobSteps.ts:47-53`) return
  `Slow motion 2×` for the `interpolate` kind when the flag is set; `planLine` picks
  `video_slow_motion`.
- Output: `outputSummary` (`src/lib/enhancePanel.ts:92-122`) puts `no sound` where
  `sound cleaned` goes (`:109`), unless `audioDisabled` says the file has none; the two never
  show together. `OutputSheet`
  disables `Clean up audio` (`OutputSheet.svelte:267-268`). The setting `video_clean_audio` is
  not changed, so leaving slow motion brings it back.
- Plan: both planners leave the `Clean audio` stage out while slow motion applies
  (`job_plan.rs:662`; `plan.ts`, the `audio_denoise` stage at `:457`), and the start input sends
  `video_clean_audio: false` with it.
- Recipes: `buildRecipe` saves the flag (`App.svelte:4330-4333`); `recipeSettingsPatch` applies
  `recipe.video_slow_motion ?? false` (`recipes.ts:76-77`); `recipeMatches` compares it
  (`:186-189`); `recipePlanLine` names it. A recipe without the field is normal speed.
- Preview patch: nothing; added frames are already left out (`previewPatch.ts:135-141`).
- Comparison after the job: the result is two or four times as long as the original, and the
  two players are run on one clock (`src/lib/compare-video-sync.ts`). For a result at least
  1.5 times as long as its original and not marked unfinished, the viewer shows `Enhanced`
  only; `Original` and `Split` are disabled with the `title` `A slow-motion result plays on its
  own.`

### Engine contract

| | |
| --- | --- |
| Registry | `interpolate` gains `StepParameter(name="slow_motion", kind="bool", default=False)` (`steps.py:328-341`); like its two neighbours it declares the value, and the request carries it as a field of its own |
| Feature | `video_slow_motion`, added in `_engine_features` |
| The host sends | `"slow_motion": true` on the video job request, with `"interpolation": "x2"` or `"x4"`; the field is left out when false (`src-tauri/src/protocol.rs:371-376`; `protocol/worker-protocol.schema.json`; `server.py:2366-2375`; `VideoJobConfig`, `video_pipeline.py:189-192`) |
| Frames | exactly the frames `x2` or `x4` gives, in either order |
| Times | each frame's time, counted from the first frame, and its length are multiplied by the factor; a variable-rate clip keeps its rhythm |
| Rate in the file | the rate before the frames were added (the file's, or twice it after Scan at double rate), not `output_fps` (`video_pipeline.py:690-691`) |
| Sound, subtitles | left out, as with a rate override (`:713-716`); `Clean up audio` does not run (`:283`, `:436`) |
| Refused | with a target other than `x2` or `x4`: `Slow motion needs 2× or 4× added frames.`; with a rate override; with HDR kept (as added frames are, `:777-781`) |

Host: `validate_start_input` (`commands.rs:4803`) refuses the flag without `x2`/`x4`, on a
temporal model, and on an engine without the feature; `output_ending` (`:5868`) appends
`_slow`; `build_start_input` (`job_plan.rs`) and `buildStartInput` (`startInput.ts:129-133`)
carry the flag.

### Files

| File | Change |
| --- | --- |
| `src/localsr/core/steps.py:328-341`, `steps.json`, `server.py`, `video_pipeline.py`, `video_chain.py:761-762` | the parameter, the feature, the field, the times |
| `protocol/worker-protocol.schema.json`, `tests/fixtures/job_request_samples.json` | the optional field; one sample |
| `src-tauri/src/types.rs`, `protocol.rs`, `commands.rs`, `job_plan.rs` | field, request, checks, name, plan |
| `src/lib/types.ts`, `startInput.ts`, `plan.ts`, `planLine.ts`, `jobSteps.ts`, `recipes.ts`, `recipeList.ts`, `enhancePanel.ts` | as above |
| `src/EnhancePanel.svelte:332-345`, `:590-597`, `:693-734`; `src/OutputSheet.svelte:262-282`; `src/PlanSheet.svelte:106-119`; `src/App.svelte:4330-4333`; `src/PreviewPane.svelte:1431-1445` | select, head, sheet, recipe, the view rule |
| `e2e/fixtures.ts` | scenario `slow` |
| Docs | `CHANGELOG.md`; `docs/video-support.md:128-143`; `KNOWN_LIMITATIONS.md:74-75` |

### Tasks

| # | Task | Touches | Done when |
| --- | --- | --- | --- |
| SM1 | Engine: the flag, the times, no sound | `steps.py`, `steps.json`, `server.py`, `video_pipeline.py`, `video_chain.py`, schema, `tests/test_video_interpolation.py` | The pytest cases pass. |
| SM2 | Host and planners: field, request, checks, `_slow`, no `Clean audio` stage | `types.rs`, `protocol.rs`, `commands.rs`, `job_plan.rs`, `lib/plan.ts`, `startInput.ts`, the parity fixture | Host cases pass; parity passes on the rebuilt fixture. |
| SM3 | Window: the group, the head, the names, the Output line and sheet, recipes | `EnhancePanel.svelte`, `OutputSheet.svelte`, `PlanSheet.svelte`, `App.svelte`, the `lib` files, tests | App.test block "Slow motion" passes. |
| SM4 | The view rule, e2e, screenshot, docs | `PreviewPane.svelte`, `e2e/*`, docs | By hand: a 10 s clip at `2× slower` is 20 s long, plays at the source's rate in QuickTime and VLC, has no sound track, and opens in `Enhanced`. |

### Tests

pytest, `tests/test_video_interpolation.py`:

| Test | Expected |
| --- | --- |
| `test_slow_motion_doubles_the_length_and_keeps_the_rate` | a 2 s clip at 25 fps with `x2`: 100 frames, about 4 s, the stream's rate 25 |
| `test_slow_motion_at_four_times` | 200 frames, about 8 s |
| `test_slow_motion_keeps_a_variable_rhythm` | a clip with uneven frame times: every output gap is the matching source gap times 2, divided over the added frames |
| `test_slow_motion_has_no_sound_and_says_so` | no audio stream; the note |
| `test_slow_motion_needs_x2_or_x4` | `fps60` with the flag is refused |
| `test_the_engine_lists_video_slow_motion` | in `_engine_features([])` |

Host: `commands/tests.rs::slow_motion_needs_two_or_four_times_and_an_engine_that_lists_it`,
`::the_video_request_carries_slow_motion` (present when true, absent when false),
`::a_slow_motion_result_is_named_slow`; `job_plan.rs` parity cases (slow motion with `Clean up
audio` on: no `audio_denoise` stage; the `interpolate` stage as for `x2`); `settings.rs`: `{}`
loads `false`; a recipe without the field loads `false`.

vitest: `lib/planLine.test.ts`, `lib/jobSteps.test.ts` (`Slow motion 2×`, `Slow motion 4×`;
`2× frames` without the flag); `lib/recipes.test.ts` (apply, match); `lib/recipeList.test.ts`
(the line); `lib/enhancePanel.test.ts` (`no sound` with sound, nothing without, never beside
`sound cleaned`).

`App.test.ts`, block "Slow motion":

| Case | Expected |
| --- | --- |
| video, feature listed | the select has the group `Slow motion (no sound)` with `2× slower · 24s`, `4× slower · 48s` for a 12 s clip |
| feature not listed | no such group |
| choose `2× slower · 24s` | `video_interpolation: 'x2'`, `video_interpolation_order: 'after_upscale'`, `video_slow_motion: true`; head ends ` · 24s` and has no rate; plan line ends `Slow motion 2×`; Output line has `no sound` |
| choose `2× · 48 fps` afterwards | `video_slow_motion: false`; head ends ` · 48 fps` |
| `Clean up audio` on, then slow motion | the Output line shows `no sound`, not `sound cleaned`; the sheet's tick is disabled; `video_clean_audio` is still true in the settings |
| a video without sound | no `no sound` |
| save a recipe, apply it | the flag is saved and comes back; the menu's line ends `Slow motion 2×` |
| HDR kept | the select is disabled as today |

e2e, scenario `slow` (`video` plus `video_slow_motion`): `Slow motion: the option's text is not
cut in the select and the head fits` (`scrollWidth <= clientWidth` at both sizes);
`video-compare.e2e.ts` gains `a result twice as long as its original opens in Enhanced and the
other two views are off`. `screens.e2e.ts`: `panel-slow-motion`.

## Screenshots and fixtures

Scenarios in `e2e/fixtures.ts`: `strength`, `shaky`, `scratched`, `slow`, each an existing
scenario plus the features named above. New shots: `panel-strength`, `panel-stabilise`,
`panel-dust`, `viewer-paint`, `panel-slow-motion`: 20 files over the four projects
(`chromium-1024`, `chromium-1440`, `webkit-1024`, `webkit-1440`). No existing baseline
changes: the shared `FEATURES` lists none of the four features, so no row gets a strength, no
list gets a new first row and no select a new group. A differing old baseline is a bug in a
"shows when" rule.

## Later

| Part | Appears when | Where and how |
| --- | --- | --- |
| A strength slider | someone asks for amounts between the stops | under the segments in the same popover; the field is already a number |
| Batches at `Light` and `Medium` | the batch runner mixes too | `image_batch.py:90` and `worker.rs:781` let a stage through whose only parameter is `strength` |
| Colour's strength | wanted | its own stops (`Soft`, `Natural`, `Full`) on the three colour paths |
| A workflow with a strength | one of the four wants it | an optional `"strength": {"noise": 0.7}` in `workflows.json`, read by `workflowSettings` |
| Stabilise: `Crop only`, `Keep edges` | the frame size of a video job can differ from the file's | a second row of segments in the popover; the Result head then shows the smaller size and the comparison view a note |
| Stabilise and Backgr. in recipes | recipes save `step_stages` | as scan-and-background.md, "Later" |
| Stabilise with HDR kept | the warp is checked on float frames | the row is enabled |
| Dust & scratches: found for you | a detector exists | marks proposed in another tint, accepted or removed in the same bar; the board's hint `2 found` on the row |
| Dust & scratches: a model, larger objects | a licence-clean inpainting model is in the catalog | the step gets a `model_task`; the row's value gains a popover with `Model`, and `Paint…` moves into it |
| Dust & scratches on video | masks follow motion | not this tool |
| Marks in the queue list | wanted for large batches | a dot on the thumbnail of a painted photo |
| Slow motion with sound | the bundled libraries have a tempo filter and it is checked at half and quarter speed | a second group, `Slow motion (slowed sound)` |
| Slow motion against its original | the compare clock takes a factor per side | the original plays at half or quarter speed beside the result |

## What may not work as drawn

- **The fold.** The photo list and the video list each grow by one 30 px row when their new
  feature is listed. At 1024 × 700 Size and Quality are further down the scrolling body.
- **The amounts.** 0.4 and 0.7 were chosen by reasoning, not on pictures: at 0.4, 60 % of the
  noise's strength remains. FS5's hand check is where they are judged; they are one table.
- **A mix is not a weaker model.** At `Light`, a deblurred edge is the average of a soft and a
  sharp edge. For Noise and JPEG artifacts this is what is wanted; for Blur it may look less
  natural than it sounds.
- **Batches.** A fix that is not at `Full` makes a queue of photos run one at a time, which is
  slower wherever photos are batched today.
- **Stabilise is an estimate.** Tracking fails on water, crowds and very dark clips; then
  frames are left as they are. A slow pan is kept, a fast one is cut into by the limit of rule
  2 and can stutter at `Strong`. On 4K frames the look-ahead of `Strong` is shortened by the
  memory limit.
- **The dimmed band** shows the first frame only and says nothing about how well the clip
  will be steadied.
- **The paint and the mask are drawn twice**, by the browser and by the host. They can differ
  by a pixel at a stroke's edge. The engine fills what the host drew.
- **Marks are strokes.** A scratch painted in four strokes reads `4 marks`.
- **A trackpad** zooms with two fingers in the viewer today, so moving the picture while
  painting needs Space or the zoom strip.
- **Slow motion in players.** The file's times are stretched and its rate is the source's.
  A player that ignores frame times and trusts the header still plays it right; an editor that
  assumes a constant rate on a variable-rate source may not.
- **The view rule for slow motion** reads the two lengths. A result whose original was
  replaced on disk by a shorter file would be taken for slow motion.

## Open questions

Each has a default the spec builds.

1. **The strength amounts.** `Light` 0.4 and `Medium` 0.7 (built), or other amounts after
   looking at pictures. One table.
2. **Strength when a fix is ticked.** `Full`, so nothing existing changes (built); or `Medium`
   as the board draws, which changes every new job's result and takes those photos out of
   batches until the batch runner can mix.
3. **Stabilise's edges.** Always crop to fit, the frame keeps its size (built); or the board's
   `Crop to fit ▾` as a real choice with a smaller frame as its second option, which needs the
   frame size to change through the video job and the comparison view.
4. **Slow motion's sound.** None (built); or the sound slowed with it, which needs a tempo
   filter that has not been checked in the bundled libraries.
