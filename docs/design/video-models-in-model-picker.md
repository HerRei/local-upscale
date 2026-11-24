# Video models in the model picker

Design for LocalSR 0.6.0, branch `w/060-flashvsr-ui`. It is a build spec: everything the
implementer needs is here. Line numbers refer to the WIP commit `89f1cc6f`. Only
`desktop/src/` and `desktop/e2e/` change; the host, the worker and the catalog stay as they
are.

Two things change:

1. **No "Video engine" select.** SeedVR2 and FlashVSR are chosen like every other upscale
   model: the plan row's **Change…** opens the model library, and the library lists them
   next to the models that work frame by frame.
2. **Availability is per model.** A video model can run when the worker lists its engine
   and the chosen device suits that engine. One that cannot is marked in the library with a
   short tag and a reason.

## Decisions

- **The model is the choice, the method is its property.** The words are *frame by frame*
  (image models, the default, never labelled on the plan row) and *several frames at once*
  (SeedVR2, FlashVSR). "Whole clips" was rejected: in everyday use a clip is the whole video.
- **Same list, own sub-list.** In "For this job" the video models follow the frame-by-frame
  models under their own list head. They keep their own sub-list, and show the same quality and
  speed dots on the image models' scales (speed 0 = slower than any frame-by-frame model).
- **Quick and Best stay frame-by-frame recipes.** A video model is always "chosen by you".
- **Nothing about unavailable models in the main panel**, unless the unavailable model is
  the chosen one (a saved setting, a recipe, a changed device). Then one warning line under
  the plan says why and Start is off. Reasons otherwise live in the library. The plan row names
  the video models that can run here in a link beside "N more models".
- **An unavailable video model cannot be taken** ("Use for this job" is off), as an Edit
  model that is too heavy cannot. It can still be downloaded, as Edit models can.
- **The settings fields keep their meaning**: an image model is
  `selected_video_model_id = 'frame_by_frame'` plus `selected_model_id`; a video model is
  `selected_video_model_id = <its id>`. Recipes, the queue and old settings need no migration.

## Layout

### Video task, Upscale, a frame-by-frame model (default)

The Model section loses the select and its notes. Nothing else moves.

```
MODEL
Upscale ×4   SPAN ×4 NomosUni                    Change…
             11 more models
```

`11 more models` counts the frame-by-frame models as today plus the video models that can
run here.

### Video task, Upscale, a video model chosen

```
ENHANCE                                          Custom
TASK      [ Upscale ] [ Restore ] [ Edit ]
AUDIO     [ ] Clean up audio
QUALITY   [ Quick ]  [ Best ]                    (neither pressed)
                                                 ("Your video" section hidden, as today)
MODEL · CHOSEN BY YOU                            Back to Best
Upscale ×4   FlashVSR v1.1                       Change…
             11 more models
             Labs · several frames at once
Chosen by you. Restore first, De-flicker and Frame rate need a
frame-by-frame model: pick Quick or Best to go back.

┌───────────────────────────────────────────────────────────┐
│ Apple GPU · automatic memory management                   │
│ Large frames are split into tiles sized to this Mac's     │
│ memory, so there is no memory setting. The video is       │
│ processed in windows of 41 frames; each starts with 8     │
│ frames of run-up from the window before.                  │
│ Requested output: 7680 × 4320 · 4×. Jobs on large frames  │
│ can take hours.                                           │
└───────────────────────────────────────────────────────────┘
Output resolution   [ Match 4× scale        v ]
Smaller output takes less time. For a first test, try 256 or
512 px. FlashVSR works best up to 4× the source: above that,
it runs at 4× and resizes the rest.
```

With SeedVR2 the panel and the Output resolution text are SeedVR2's, unchanged. The stage
label is `Upscale ×N` while Output resolution is "Match N× scale", and `Upscale` once a
resolution in pixels is set.

### The chosen video model cannot run here

```
MODEL · CHOSEN BY YOU                            Back to Best
Upscale ×4   FlashVSR v1.1                       Change…
             11 more models
             Labs · several frames at once
! FlashVSR needs 16 GB of memory. This Mac has 8 GB.
Chosen by you. Restore first, De-flicker and …
                                                 [ Start selected video ]  (off)
```

The warning is `<p class="inline-warning" role="status">`, placed directly after the plan
list (where `hasNothingToRun` shows, App.svelte:5094). A FlashVSR memory panel is not shown
in this state (it describes "this Mac"); SeedVR2's panel shows as today.

### Model library, opened with Change… in video Upscale (a 16 GB Apple silicon Mac)

```
Model library                                              1.2 GB on this computer  ✕
┌──────────────────────┬──────────────────────────────────────┬──────────────────────┐
│ For this job      12 │ FRAME BY FRAME      sorted by quality │ FlashVSR v1.1 — Labs │
│ Upscale photos     9 │ SPAN ×4 NomosUni   ●●○○ ●●●○  4 MB    │ Junhao Zhuang et al. │
│ Illustration       3 │   [Quick]                  Installed  │  · … · Apache-2.0    │
│ Fix noise, blur …  6 │ HAT-S ×4           ●●●● ●○○○ 38 MB    │ (description)        │
│ Video (Labs)       3 │   [Best]              Not installed   │ Download   6.0 GB    │
│ Audio              2 │ …                                     │ Memory  16 GB unified│
│ Edit with a prompt 9 │ SEVERAL FRAMES AT ONCE · LABS         │ Engine     FlashVSR  │
│ Installed    1 · 4 MB│      use neighbouring frames · slower │ Works on  41 frames  │
│ Your own file        │ SeedVR2-3B — Labs            6.8 GB   │             at once  │
│                      │   Experimental 3B FP16 …  Not downl.  │                      │
│                      │   [Labs] [Apache-2.0]                 │ ✓ Installed · …      │
│                      │ SeedVR2-3B FP8 — Labs        3.6 GB   │ [ Use for this job ] │
│                      │   …                                   │                      │
│                      │ FlashVSR v1.1 — Labs         6.0 GB   │                      │
│                      │   Experimental one-step …  Installed  │                      │
│                      │   [Labs] [Apache-2.0] [Chosen]        │                      │
└──────────────────────┴──────────────────────────────────────┴──────────────────────┘
```

### The same list on a Windows PC with a CUDA engine, FlashVSR selected

```
│ SEVERAL FRAMES AT ONCE · LABS             │ FlashVSR v1.1 — Labs                 │
│ SeedVR2-3B — Labs               6.8 GB    │ …facts…                              │
│   [Labs] [Apache-2.0]                     │ ● FlashVSR needs an Apple silicon    │
│ SeedVR2-3B FP8 — Labs           3.6 GB    │   Mac with 16 GB of memory.          │
│   [Labs] [Apache-2.0]                     │                                      │
│ FlashVSR v1.1 — Labs            6.0 GB    │ [ Download 6.0 GB ]                  │
│   [Labs] [Apache-2.0] [Apple silicon only]│ [ Use for this job ]  (off)          │
```

The dot is the existing `.fit` line with `<i class="warn">`; the tag is a `pill bad`.

## Copy

Every string, exactly. Strings marked *WIP* are already in `89f1cc6f`.

### Unavailable: tag and reason (`lib/videoEngines.ts`)

`N` is the model's `min_unified_memory_gb` (16), `M` the Mac's memory in whole GB.

| Model | Case, checked in this order | Tag | Reason |
| --- | --- | --- | --- |
| SeedVR2 | engine not listed, or no engine yet | `Unavailable` | `This installation supports frame-by-frame models. SeedVR2 requires a compatible CUDA, ROCm or Metal engine.` |
| SeedVR2 | device `directml`, `xpu`, `openvino` | `Unavailable` | `SeedVR2 cannot run on this device. Choose a frame-by-frame model or a compatible CUDA, ROCm or Metal device.` |
| FlashVSR | no engine yet (`snapshot.engine` null) | `Unavailable` | `FlashVSR is unavailable until the engine has started.` *WIP* |
| FlashVSR | not listed, no `mps` device | `Apple silicon only` | `FlashVSR needs an Apple silicon Mac with N GB of memory.` *WIP* |
| FlashVSR | memory known and below `N` | `Needs N GB` | `FlashVSR needs N GB of memory. This Mac has M GB.` *WIP* |
| FlashVSR | not listed, `mps` device present | `Unavailable` | `FlashVSR needs the MLX runtime, which this installation does not have.` *WIP* |
| FlashVSR | device is not `mps` | `Apple GPU only` | `FlashVSR cannot run on this device. Choose a frame-by-frame model or the Apple GPU.` |
| other engine | not listed | `Unavailable` | `This installation cannot run <engine_kind>.` *WIP* |

The two SeedVR2 sentences and FlashVSR's device sentence differ from today only where they
named the removed select ("frame-by-frame video" becomes "frame-by-frame models" / "a
frame-by-frame model").

The Mac's memory is `capabilities.system_ram_total`, rounded to whole GiB. It is **not** the
`mps` device's `total_memory`: that is PyTorch's recommended maximum, about two thirds of
the installed memory, and would lock out every 16 GB Mac. Unknown (0) is not a reason.

### Main panel

| Where | String |
| --- | --- |
| Plan row, stage label, video model | `Upscale ×4` (the chosen scale); `Upscale` when Output resolution is in pixels |
| Plan row, name | the catalog name before ` — `: `FlashVSR v1.1`, `SeedVR2-3B`, `SeedVR2-3B FP8` |
| Plan row, note | `Labs · several frames at once` |
| Change… `aria-label` | `Change the upscaler` (as for image models) |
| Under the plan, video model chosen | `Chosen by you. Restore first, De-flicker and Frame rate need a frame-by-frame model: pick Quick or Best to go back.` |
| Under the plan, image model chosen by hand | `Chosen by you. Pick Quick or Best to go back.` (unchanged) |
| Header, video model chosen | `Custom` (no ` · Photo`: content does not apply) |
| HDR source, model cannot keep HDR | `SDR output selected. This model cannot preserve HDR.` (unchanged, moves under the plan list) |
| HDR section warning (App.svelte:4487) | `Choose a HAT model for HDR preservation.` |
| Output resolution text, FlashVSR | `Smaller output takes less time. For a first test, try 256 or 512 px. FlashVSR works best up to 4× the source: above that, it runs at 4× and resizes the rest.` *WIP* |
| Output resolution text, SeedVR2 | unchanged |

### FlashVSR memory panel (`VideoMemory.svelte`, all *WIP*)

- `aria-label`: `FlashVSR memory` (SeedVR2: `SeedVR2 memory`).
- Title: `Apple GPU · automatic memory management`
- `Large frames are split into tiles sized to this Mac's memory, so there is no memory
  setting. The video is processed in windows of 41 frames; each starts with 8 frames of
  run-up from the window before.` (41 and 8 are the model's `temporal_window` and
  `temporal_overlap`.)
- `Requested output: <dimensions>. Jobs on large frames can take hours.`
- No checkbox, no clip, VAE or offload numbers, no capacity paragraph.
- Readings of a FlashVSR job: `<w> × <h> · windows of <clip_frames> frames · automatic
  memory management` (the windows part is left out when `clip_frames` is 0); stage labels
  `Preparing window`, `Encoding`, `Decoding`, `Finishing window`, the others as SeedVR2;
  the footnote without the PyTorch sentence. Readings keep the wording of the engine that
  produced them, whichever model is selected now.

### Advanced settings (*WIP*)

- FlashVSR: `FlashVSR · Labs. One-step diffusion upscaling that works best up to 4× the
  source. Memory is managed automatically; jobs on large frames can take hours.`
- SeedVR2: unchanged.

### Model library

| Where | String |
| --- | --- |
| "For this job", first list head in video Upscale | `Frame by frame` · right: `sorted by quality` |
| "For this job", second list head | `Several frames at once · Labs` · right: `use neighbouring frames · much slower` |
| "For this job" head in every other job | `For this job` · `sorted by quality` (unchanged) |
| "Video (Labs)" list head | `Video · Labs` · right: `3 models` (`1 model`) *WIP* |
| Video row, second line | `model.summary`, else the first sentence of `model.description` |
| Video row pills | `Labs`, the license name, `Chosen` (the job's model, class `pill quick`), the tag (class `pill bad`) |
| Details, fourth fact | label `Works on`, value `41 frames at once` (replaces `Window` / `41 frames`) |
| Details, Engine fact | `SeedVR2` / `FlashVSR` *WIP* |
| Details, reason line | the reason, as a `.fit` line with the warn dot *WIP* |
| Details, button | `Use for this job` |

## Behaviour

`temporal` below means `usingTemporalVideo` (App.svelte:737): Video task, not Restore,
`selected_video_model_id !== 'frame_by_frame'`.

### What each choice writes

| Action | Settings patch |
| --- | --- |
| **Use for this job** on a video model | `selected_video_model_id: <id>`, `quality: 'custom'`, `output_scale` clamped to 2–4, `enable_face_model: false`. Nothing else: `selected_model_id`, `fix_stages`, `fixes`, `deflicker`, `video_interpolation`, `video_hdr_mode`, `custom_model_path` keep their values and return when a frame-by-frame model is chosen again. Then `termsAccepted = {}` and the library closes. |
| **Use for this job** on an image model, or a file of your own, in the Video task | today's patch (`selectPrimaryModel` 3120, `selectCustomModel` 3078), plus `selected_video_model_id: 'frame_by_frame'` **only when it is not already that**. Adding it always would switch off the HDR rule in `updateSettings` (2404–2405), which runs only when the patch leaves that field alone. |
| **Quick** or **Best** | unchanged: `resolveRecipe` sets `'frame_by_frame'` (2133). |
| **Back to Best** | unchanged (`applyPreset('best')`). |
| Upscale → Restore | unchanged: `'frame_by_frame'` (2539). |

### What follows from a video model being chosen

All of this exists today behind `usingTemporalVideo` and stays; check each after the switch.

| Area | Rule | Where |
| --- | --- | --- |
| Quality | shown as Custom even when `settings.quality` still says quick or best (old settings): test `usingTemporalVideo` first | `activeQuality` 1272 |
| "Your video" (Content, Restore first) | hidden | 4668 |
| Fix stages | not in the plan, sent as `[]`; kept in settings | plan.ts:243, `makeStartInput` 3214 |
| De-flicker, Frame rate | hidden in Advanced, sent as off; kept in settings | AdvancedSettings 401, `makeStartInput` 3227, 3241–3244 |
| HDR | Colour output shows "Convert to SDR", Preserve is off for this model; `video_hdr_mode` is sent as `tone_map` and the saved value is kept | 4459, 3232 |
| Output resolution | shown; honoured by both engines | 5149 |
| Memory panel | shown; FlashVSR's or SeedVR2's by `engine_kind`; not FlashVSR's when FlashVSR is unavailable | 5128–5147 |
| Output scale | 2×–4×; hidden while a pixel resolution is set | AdvancedSettings 245–247 |
| Hardware | all devices listed, as for SeedVR2 today | AdvancedSettings 61, 69, 510 |
| Tiles, precision, safe memory | hidden | AdvancedSettings 566, 607 |
| Start | off while the chosen model is unavailable or not installed and not downloadable | `modelReady` 845, `prepareReady` 1309 |
| Download in the plan row | off while unavailable | 5070 |
| Presets download size | fixes not counted | plan.ts `presetDownload` 533 |

### Recipes, queue, old settings

- **Saving a recipe** stores `video_model_id` as today (3495–3506). **Applying** one sets it
  as today (3623–3625). A recipe whose video model cannot run here applies, shows the
  warning line and cannot start. `recipeMatches` (recipes.ts:60–75) is unchanged.
- **The queue** reads `makeStartInput` at start; no change.
- **Old settings with a video model selected** load as they are: the plan row shows the
  model, Quality shows Custom. `chooseInitialModel` keeps the choice
  (`keepVideoEngine: true`, 2021). No migration.
- **A saved id the catalog no longer has**: as today there is no primary stage, so the
  header's **Change…** (4769–4774) opens the library.
- **Photo task and Restore**: no video rows in "For this job", no **Use for this job** on
  video models anywhere. The "Video (Labs)" group stays for browsing and downloading.

### Library rules

- Video rows join "For this job" only when `context.task === 'video'`, not restoring, and
  `context.slot === 'primary'`.
- Opened while the job uses a video model, the library selects that model's row
  (`selectedId`, 121).
- **Use for this job** on a video model shows in "For this job" and in "Video (Labs)" under
  the same condition; it is disabled while `busy` or while the model is unavailable. No
  "Use for Quick/Best" for video models. A model that is not installed can be taken: Start
  downloads it, as for image models.
- The rail's "For this job" count includes the listed video rows.
- "N more models" under the plan row: today's count plus the video models that can run here
  and are not the chosen one; with a video model chosen, every listed frame-by-frame model
  counts.

## Files to change

`desktop/src/lib/videoEngines.ts` (107 lines, *WIP*)
- `VideoUnavailable`: drop `quiet`. `videoAvailability`: drop the `selectedId` parameter
  and `notes`; return the per-model map only. Change the three sentences in the copy table.
- Add `videoModelsForJob(catalog, task, videoTask)`: the catalog's video models for
  `task === 'video'` and `videoTask !== 'denoise'`, otherwise `[]`.
- Add `videoModelShortName(model)` (name before ` — `) and `videoModelSummary(model)`.

`desktop/src/lib/plan.ts`
- `resolvePlan`, the video stage (280–291): label as in the copy table. Keep `kind: 'video'`.

`desktop/src/App.svelte`
- 748–755: `videoAvailable` without the selected id; `temporalUnavailableReason` stays.
- 1272: `activeQuality`, temporal first.
- 3115: delete `selectVideoEngine`. Add `useVideoModelForJob(model: CatalogVideoModel)`.
- 3078, 3120: the conditional `'frame_by_frame'` in both patches.
- 4392: header text.
- 4487: HDR warning copy.
- 4777–4807: delete the label, the select and the notes loop. Move the "SDR output
  selected" note to after the plan list, under the same conditions (Video task, not Restore,
  HDR source, `!hdrPreservationAvailable`).
- 4821–4828: `changeSlot` is `'primary'` for a video stage too.
- 4829–4841: `moreModelCount` as specified.
- 4843–4845, 4867–4868: name and note of a video stage.
- before 5094: the warning line, `{#if usingTemporalVideo && temporalUnavailableReason}`.
- 5119: the two "Chosen by you" sentences.
- 5426: pass `useVideoForJob={useVideoModelForJob}` to the library.

`desktop/src/ModelLibrary.svelte`
- New prop `useVideoForJob`. `jobVideoModels` from `videoModelsForJob`.
- 121: initial `selectedId`.
- 235–241: the "For this job" count.
- 683–721: the video row markup is needed twice. Factor it so the `video` group and the
  `job` group render the same rows; in the `job` group they follow the image rows (list
  head 896–904, rows 905–998) under the second list head. Change the first head's text when
  video rows follow.
- 1086: details for `selectedVideo` in any group; the `Works on` fact; the button.

`desktop/src/VideoMemory.svelte`, `desktop/src/AdvancedSettings.svelte`: done in the WIP.

`desktop/e2e/fake-host.ts`: `videoEngines` scenario option, done in the WIP (34, 406).

## Tests

At the WIP commit: `./local-ci.sh frontend` passes with 758 tests in 59 files; the e2e suite
passes with 736 passed, 12 skipped. Both must pass at the end.

### vitest

`lib/videoEngines.test.ts` (exists; adapt to the smaller type and the new sentences)

| Case | Expected |
| --- | --- |
| FlashVSR, engine lists it, 16 GB (and 64 GB) Mac, device `mps` | available |
| same, `mps` device reports 10.7 GB `total_memory` | available |
| same, device `cpu` | tag `Apple GPU only`, device sentence |
| engine lists it, device `cuda:0` / `cuda` | tag `Apple GPU only` |
| 8 GB Mac, device `mps` or `cpu`, listed or not | tag `Needs 16 GB`, `… This Mac has 8 GB.` |
| `system_ram_total` 0, listed, `mps` | available |
| not listed, PC | tag `Apple silicon only` |
| not listed, 16 GB Mac | tag `Unavailable`, MLX sentence |
| engine `null` / `undefined` | tag `Unavailable`, "until the engine has started" |
| SeedVR2 listed on `mps`, `cpu`, `cuda`, `cuda:0`, `cuda:1` | available |
| SeedVR2 on `directml:0`, `xpu:0`, `xpu`, `openvino:GPU` | device sentence |
| SeedVR2 not listed, or engine `null` | "This installation supports frame-by-frame models. …" |
| SeedVR2 on an 8 GB Mac | available (no memory rule) |
| shipped catalog | engine kinds are exactly `seedvr2` and `flashvsr`; all three available on a 16 GB Mac |
| unknown engine kind | available when listed, else `This installation cannot run <kind>.` |
| `videoModelsForJob` | 3 models for video + upscale; `[]` for video + denoise, upscale, denoise, edit, no task |

`lib/plan.test.ts`: a temporal plan has one primary stage with `kind: 'video'`, label
`Upscale ×4`; with `video_target_resolution: 720` the label is `Upscale`; saved fix stages
are not in it.

`App.test.ts` (rewrite every test that uses `getByLabelText('Video engine')`: 1535, 1554,
1668, 1698, 1847, 1883, 1902, 1962, 3048, 3054; choose models through
`Change the upscaler` → the row → `Use for this job`)

| Case | Expected |
| --- | --- |
| Video Upscale | no control labelled `Video engine`; no `.model-output-note` for unavailable models |
| Take SeedVR2 in the library | `saveSettings` gets `selected_video_model_id: 'seedvr2_3b'`, `quality: 'custom'`, the old `selected_model_id`; plan row `SeedVR2-3B` with `Labs · several frames at once`; De-flicker and Safe memory gone; no "Your video" section |
| Then take an image model | `selected_video_model_id: 'frame_by_frame'`; De-flicker and Frame rate show their saved values (both values of de-flicker) |
| Then click Quick | `'frame_by_frame'`, Quick pressed |
| HDR video, HAT with Preserve, take SeedVR2, set 512 px, untick Reduce GPU memory, Start | Colour output reads `tone_map`, Preserve disabled, the SDR note shows, `startJobs` gets `video_model_id: 'seedvr2_3b'`, `video_hdr_mode: 'tone_map'`, `video_target_resolution: 512`, `video_low_memory: false`; taking the HAT model again restores Preserve |
| FlashVSR on a 16 GB Mac, Start | library row enabled; `FlashVSR memory` panel with the windows sentence and none of `SeedVR2`, `VAE`, `per clip`, `offload`; Advanced shows the FlashVSR line; `startJobs` gets `video_model_id: 'flashvsr_v11'`, `fix_stages: []`, `deflicker: false`, `video_interpolation: 'off'`, `video_hdr_mode: 'tone_map'` |
| FlashVSR unavailable: `cpu`, `cuda:0`, 8 GB, not listed | row pill is the tag; reason in the details; `Use for this job` disabled; SeedVR2's enabled |
| Saved FlashVSR choice in each of those four states | warning line with the reason, Start disabled, no `FlashVSR memory` panel; after taking SeedVR2 Start is enabled and the warning is gone |
| SeedVR2 on `directml:0`, `xpu:0`, `openvino:…`, or not listed, as saved choice or recipe | warning line, Start disabled, no Download button, `downloadModel` not called; taking an image model enables Start |
| Old settings: `quality: 'quick'`, `selected_video_model_id: 'seedvr2_3b'` | header `Custom`, Quick not pressed, plan row `SeedVR2-3B` |
| Library opened from a job that uses FlashVSR | its row is selected and carries `Chosen` |
| Photo task, and video Restore | "For this job" has no video rows; "Video (Labs)" has no `Use for this job` |
| "N more models" | grows by the number of video models that can run here |
| Library, 8 GB Mac | head `Video · Labs` / `3 models`, pill `Needs 16 GB` once, reason in details, `FlashVSR` as Engine |
| Cancelling a video job (was 3048) | assert on another locked control, such as `Change the upscaler` |

`VideoMemory.test.ts`: the five cases added in the WIP stay as they are.

### e2e (`desktop/e2e/video-engines.e2e.ts`, rewrite; scenario `mac` in the file)

| Case | Expected |
| --- | --- |
| Mac, both engines listed, FlashVSR installed | no `#video-engine`; Change… → both list heads; take FlashVSR; `FlashVSR memory` panel; Advanced line; Start → `start_jobs` with `video_model_id: 'flashvsr_v11'` |
| Same Mac, `device_id: 'cpu'` | FlashVSR row pill `Apple GPU only`, `Use for this job` disabled; SeedVR2's enabled; no warning in the main panel |
| Saved `selected_video_model_id: 'flashvsr_v11'` on the CPU | warning line with the device sentence, no memory panel, Start disabled |
| Windows scenario, engines `seedvr2` only | FlashVSR pill `Apple silicon only`, both SeedVR2 rows `Unavailable`, reasons in the details, no `.model-output-note` in the main panel |
| Photo task | no video rows in "For this job" |

Screenshot baselines: update only those that show the Model section of video Upscale (the
select is gone); check `advanced-video` and the video cases in `layout.e2e.ts`. No new
baseline is required.

## What the worker and host must provide

Assumed, not checked here:

1. `engine.video_engines` contains `"flashvsr"` only where the worker can run it (Apple
   silicon with MLX loaded).
2. `capabilities.system_ram_total` is the Mac's installed memory in bytes.
3. The FlashVSR engine refuses to start below 16 GB instead of swapping; the UI check is a
   convenience, not the guard.
4. `runtime.video_memory` for FlashVSR jobs: `device: 'mps'`, `shared_memory: true`,
   `clip_frames` = the window length (41) or 0, stage ids from the existing set
   (`loading_model`, `reading_frames`, `preparing_clip`, `encoding`, `enhancing`,
   `decoding`, `finishing`, `saving`). An unknown stage id is shown as it comes.
5. A temporal job's `model_id` in `jobs` is the video model's id (it decides which wording
   the memory readings get).

Outside this change, for whoever owns them:

- Two host messages still name the removed control (`commands.rs` 3769 and 3777: "Restore
  uses the Frame-by-frame video engine…", "Frame interpolation works with the
  Frame-by-frame video engine… Choose Frame-by-frame or turn interpolation off."). The UI
  does not reach them, but the wording should follow.
- The three video models have no `summary` in the catalog; one short line each would read
  better in the rows than the first sentence of the description.
- The library's video details show `min_unified_memory_gb` as "unified" on every platform.
