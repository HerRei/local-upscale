# Previews and recipes

Build spec for LocalSR 0.7.0. It follows `enhance-panel.md` and `downloads.md` and builds what
Hermes decided on 2026-11-27 after testing the 0.7.0 build:

- "All of the fix noise etc. stuff doesn't have a nice preview at all."
- "We need to handle how people can save a lot of them so that they don't clutter the whole
  space on the UI."
- "Maybe it makes sense to have a workflow that we recommend somewhere, e.g. for old scans? Or
  prompts that are part of it for the edit?"
- During JPEG → Colour → Noise → 4× the live preview "wasn't how I expected" between the steps.

Line numbers refer to `63f2a341` on `rc/0.7.0`. Every reference also names its function,
selector or text. Paths are relative to `desktop/` unless they start with `docs/`, `src/localsr/`,
`protocol/` or `tests/`.

Seven things change:

1. **Between the steps of a job the picture never gets worse and never resets**, and the
   viewer, the status bar and the plan line say which step runs.
2. **After a photo job, each step's result can be looked at**: step chips above the viewer.
3. **A preview patch**: a square of the open photo that already shows what the ticked fixes
   and the upscale will do, with one chip per step.
4. **The Model library shows an example picture** at the top of a model's details, and its
   grey cell goes.
5. **Recipes**: a short menu, and a Recipes library dialog for the long list.
6. **Four built-in workflows**, read-only, under Recommended.
7. **Prompt examples** beside "What to change" in Edit.

The worker protocol gains, additively and behind engine features: two optional fields on a
photo job and one message (`stage_result`), optional fields on `stage_started`, and one request
with five messages (`patch_request`). The settings file gains four optional fields. Nothing
existing changes or goes.

## Decisions

| Point | Decision | Reason |
| --- | --- | --- |
| Where a fix is previewed | On the user's own photo, in the viewer. Canned examples only in the Model library and where no photo or model is there. | Whether a denoiser smears texture depends on the photo. A sample in a 230 px popover shows nothing of that. |
| The patch | 384 × 384 source pixels, drawn in place over the photo, one chip per step. It runs the whole plan, upscale included. | One runner serves "what does this fix do", "what does each step add" and "which of two models". Drawn in place there is nothing to explain: this square is already done. |
| Starting it | One click: `Preview` in the viewer head, or `See it on this photo` in a fix popover. Then it stays on (remembered) and follows ticks, models and its position. | A tick that silently starts the GPU is a surprise. One click is the consent. |
| Automatic or on click | LocalSR decides per update from measured times: an update that should take 5 s or less runs by itself, a slower one waits for `Update preview`. Settings › General can force either, or turn the patch off. | Hermes: "we can decide that for the user and let him do it in the settings nevertheless". |
| Never by itself | The patch never downloads a model and never runs while a job runs or waits. | Downloads cost data; a job owns the GPU. |
| Comparing models | Two at a time, flipped in place (`A` / `B`, key `X`). The full-image `Try others…` stays in the Plan sheet. | Flipping shows small differences that side by side hides. |
| Colour in the patch | The colours are guessed from the whole picture, reduced, then applied to the patch. | A tight crop changes the model's guess. DDColor works at 512 × 512 anyway (`resources/model-catalog.json`, `ddcolor_modelscope`). |
| Video | Frame-by-frame videos only, on the frame the viewer shows, labelled `This frame`. No De-flicker, added frames or audio in the patch. Not for SeedVR2 and FlashVSR. | A still cannot show changes over time. |
| Between steps | The sharp source stays until a step's finished picture has arrived at viewer size; then that picture is the base. The 320 px picture is never a base. | Today's cause, below. |
| Step-end pictures | The engine sends them itself at the step's end as a JPEG of at most 2048 px; the host stores it as a preview file. | See "How a step's picture gets to viewer size". |
| Step name | `Step 2 of 4 · Colour`, with the plan line's own names, in three places. | Today the bar says `Restoring · stage 2/4` for every fix. |
| Step results after a job | Photos only, the last job only, in the work folder, deleted when the next job starts and on quit. 8-bit pictures to look at, not exports. | Hermes's answer 2. Bounded disk use; no new kind of saved file. |
| Example pictures | One per fix and four upscalers, 14 files, at most 1 MB, WebP. | Fills the detail pane's empty space honestly; small enough to bundle. |
| Recipes in the panel | The menu shows at most six recipes. Everything else is in the Recipes library. | The panel stays calm at 100 recipes. |
| No delete in the menu | Delete, rename, duplicate and pin are in the library. | The menu launches; a ✕ per row is a mis-click away from data loss. |
| `Update "<name>"` | In the menu when the applied recipe was changed. | Without it people save near-duplicates, which is where the clutter comes from. |
| A recipe's second line | Its plan line (`JPEG → Noise → Upscale 4×`). | The same words as the panel foot. `FP16` is not how anyone remembers a recipe. |
| Shortcuts | ⌘3 to ⌘9 follow the pins in pin order. | Today they follow storage order (`native_menu.rs:139-144`), so deleting one recipe moves six shortcuts. |
| Workflows | Four, read-only, defined by Content, ticks, Size and Quality. Quality picks the models, as it does for the user. | The Quality row then shows a pressed segment, remembered fix models are respected, and a better Quick model improves the workflow. |
| Workflows and output | A workflow never changes Output, hardware or tile settings. | Those belong to the computer and the user, not to the kind of picture. |
| Prompt examples | Five, replace the text, ⌘Z restores it, a `[slot]` is selected after insert. Shown only for the model families they were tested on. | Stacked instructions contradict each other. |

## Today

**Between steps.** Three facts produce what Hermes saw:

| # | Fact | Code |
| --- | --- | --- |
| 1 | Live pictures are capped at 320 px on the long side. The host sends `preview_max_dimension: 320`; the encoder clamps to 64-640. | `ImageJobRequest`, `src-tauri/src/commands.rs:5286`; `LatestPreviewEncoder(max_dimension=…)`, `src/localsr/worker/image_job.py:571`; `encode_preview_jpeg`, `src/localsr/core/live_preview.py:68`; schema `src/localsr/protocol/worker-protocol.schema.json:236` |
| 2 | Each fix step ends by sending the whole picture through that encoder (`force=True`), so a 24-megapixel photo arrives as 320 × 213. | `_run_stage`, `image_job.py:770-786` |
| 3 | The viewer takes that picture as the base under the next step and shows it in place of the source. | `stageBasePreview`, `src/PreviewPane.svelte:607`, `:650`; `displayedSourcePreview`, `:365-370` |

So after a "fix" the photo looks softer than before it. Two more: the Colour step and a skipped
Blur step send no picture (`_skip_preview`, `image_job.py:361`, `:379`), so colour appears only
when upscale tiles arrive; and the status bar reads `Restoring · stage 2/4` for every fix
(`"stage_started"`, `src-tauri/src/worker.rs:2130-2137`; `src/lib/workerEvents.ts:222-229`).

**Fix previews.** None. `Try others…` queues whole jobs for two to four models
(`start_model_trials`, `commands.rs:1804`). The viewer already fetches sharp crops of the
original while zoomed in (`request_preview_regions`, `commands.rs:2281`; `cropStyle`,
`src/lib/compareRegions.ts:36`), which is the geometry the patch reuses.

**Model library.** Five facts in a two-column grid leave a sixth, empty cell
(`<dl class="facts">`, `src/ModelLibrary.svelte:1718-1741`; `.facts`, `src/styles.css:4250-4264`).
No model shows a picture.

**Recipes.** The menu lists every saved recipe, each with a ✕ (`src/RecipesMenu.svelte:54-140`).
The host allows 100 (`store_recipe`, `commands.rs:1046`). The Presets menu gives ⌘3 to ⌘9 to the
first seven in storage order (`native_menu.rs:139-144`). Bit depth and Edit were fixed in
`7c7736ab`.

## 1. Between the steps of a job

### Layout

The viewer during step 2 of 4. `◐` is the activity dot (`.activity-dot`, as in
`PreviewActivity.svelte`).

```
┌ IMG_2041.jpg                       1600 × 1067 ──────────────────────────────┐
│ ┌──────────────────────────┐                                                 │
│ │ ◐ Step 2 of 4 · Colour   │                                                 │
│ └──────────────────────────┘                                                 │
│                 (the picture after step 1, sharp; tiles of                   │
│                  step 2 paint over it where the step sends tiles)            │
└──────────────────────────────────────────────────────────────────────────────┘
 ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
 ● Step 2 of 4 · Colour · 31%                                       [ Cancel ]
   1600 × 1067 → 6400 × 4268 · Neural Engine
```

The panel foot while that job runs:

```
╞══════════════════════════════════════════════════════╡
│ ✓ JPEG → Colour → Noise → Upscale 4×               ▸ │
│ Output     PNG · Desktop                           ▸ │
```

`✓ JPEG` in `--secondary`, `Colour` in `--text` at weight 600, the waiting steps in
`--secondary`.

### Copy

| Where | String |
| --- | --- |
| Viewer label, status bar title | `Step 2 of 4 · Colour` (the bar adds ` · 31%` as today). Names are the plan line's (`stageStepName`, `src/lib/planLine.ts:26-58`): `Blur`, `JPEG`, `Noise`, `Colour`, `Upscale 4×`, `Fix`. |
| One-step job | Unchanged: `Enhancing`, `Editing`. No viewer label. |
| A skipped step, viewer label for 4 s, then the next step's label | `Step 1 of 4 · Blur skipped` |
| Status bar detail when a step ends | `Colour done.` (was `restore stage complete.`) |
| Status bar detail when a step starts | `Preparing DDColor.` with the model's display name (`modelDisplayName`, `src/lib/modelNames.ts`), not its id |
| Plan line, accessible name while running | `Plan: JPEG done, Colour running, Noise, Upscale 4×` |

### Behaviour

The rule: the viewer shows the best version so far at full sharpness. A step change does not
clear the picture, does not swap in a smaller one, and does not move zoom or pan.

1. **Base.** While a photo job runs, the base under the tile canvas is the newest step picture
   (`runtime.job_steps[n].preview_url`, below) or, before the first one exists, the source
   preview. `displayedSourcePreview` (`src/PreviewPane.svelte:365-370`) reads that instead of
   `activeStagePreview`. The prop `stagePreview` (`:91`, fed at `src/App.svelte:4927-4929`)
   carries the step picture's URL.
2. **The 320 px full-image picture is ignored.** `queueProgressiveTile` no longer stores a
   full-image tile as a base (`:607`, `:650`) and does not paint it. `stageBasePreview`
   (`:336`), `stageBaseStage` and `App.svelte`'s copy go.
3. **Step change.** When a tile of a later step arrives (`stage > progressiveStage`, `:633-640`),
   the canvas is not cleared at once. It is cleared in the same frame in which the finished
   step's picture has decoded (`HTMLImageElement.decode()`) and become the base. If no picture
   arrives within 2 s (an older engine, a skipped step), the canvas is cleared when the next
   step's first tile is painted, and the base stays what it was.
4. **The upscale step** sizes the canvas for the output (`ensureProgressiveCanvas`, `:789`).
   The base stays under it; the fitted size is the same because the aspect is the same.
   `scheduleViewportReset` is not called for a step change.
5. **A step without tiles** (Colour): the previous picture stays, the label's dot pulses, and
   the coloured picture appears when the step's picture arrives.
6. **A skipped step** keeps the picture, shows the skipped label, and stays skipped in the
   chips after the job (section 2). The existing warning note is unchanged
   (`image_job.py:371-377`).
7. **Hold `M`** shows the original during a job, as it does after one
   (`holdsOriginalKey`, `:975`): `canPeek` (`:185`) also holds while `processing` and a step
   picture or tiles are shown.
8. **Label.** A `<div class="step-label">` at the top left of `.canvas-well`, `aria-hidden`
   (the status bar is the live region). Shown for a job of two or more steps while its file is
   the selected one. Hidden while `M` is held.
9. **Plan line.** While `settingsLocked` and `job_steps` has as many entries as `planSteps`
   (`src/EnhancePanel.svelte:253`), each name gets its state. Otherwise the line is as today.
10. **Video and Edit** are unchanged, apart from the step label and title for a video job of
    several stages.

### How a step's picture gets to viewer size

The cap of 640 is on `preview_max_dimension`, which sizes every tile picture, up to two a
second. Raising it would enlarge all of them. The app already has a second path for large
pictures: a finished result is decoded by the worker to at most 2048 px
(`render_result_preview`, `src/localsr/worker/result_preview.py:29-66`;
`RESULT_PREVIEW_MAX_DIMENSION`, `worker.rs:3244`), sent once as `preview_ready`, and stored by
the host as a file in the preview folder (`save_jpeg_base64`, `src-tauri/src/preview_cache.rs:26`;
`"preview_ready"`, `worker.rs:2032-2076`), which the window loads by asset URL.

Step-end pictures use that file path, not the tile path, with one difference: at a step's end
there is no file to decode yet, so the engine encodes from the pixels it holds.

| Side | Addition |
| --- | --- |
| Engine feature | `stage_results` in `_engine_features` (`src/localsr/worker/server.py:200-243`) |
| Request, `job_request` | `stage_picture_max_dimension`: integer 64-2048, optional. The host sends 2048 (the constant above) when the engine lists the feature. |
| Message | `stage_result`: `job_id`, `stage_index`, `stage_count`, `step`, `fix`, `skipped`, `note`, `width`, `height`, `jpeg_base64`, `preview_width`, `preview_height`, `image_path` (empty unless section 2 applies). Sent at the end of every step before the main model, also a skipped one (then without a picture) and Colour. |
| `stage_started`, `stage_completed` | optional `step` and `fix` |
| Worker | In `_run_stage` (`image_job.py:717-787`), after the existing `progress.preview.submit(…)`: when the request has the field, reduce the step's pixels to the limit, encode as JPEG quality 82 (as `result_preview.py:58`) and emit. `_skip_preview` no longer suppresses this message, only the 320 px one. The encode is 2048 px once per step; it runs on the job thread. |
| Host | `"stage_result"` in `worker.rs` beside `"preview_ready"`: store the JPEG with `save_jpeg_base64`, put the URL into `runtime.job_steps[stage_index]`, release the previous URL through `cleanup_stale_previews`. The `worker-message` event (`worker.rs:1426`) carries the message without `jpeg_base64`. `preview_cache.rs:133` counts the URLs as referenced. |
| Older engine | No field is sent, no message comes; rule 3's fallback applies. The picture then goes back to the source between steps, never to a smaller one. |

`runtime.job_steps`, new on `RuntimeStatus` (`src-tauri/src/types.rs:774`), empty outside a job
of two or more steps:

```ts
interface JobStep {
  kind: string;            // 'restore', 'upscale', …
  fix: string;             // '' or 'blur' | 'jpeg' | 'noise' | 'colour'
  model_id: string;
  state: 'waiting' | 'running' | 'done' | 'skipped';
  note: string;            // why it was skipped
  preview_url: string;     // '' until its picture arrived
  kept: boolean;           // a full-size file exists (section 2)
}
```

The host fills it when it dispatches a photo job, from the stages it built
(`image_pipeline_stages`, `commands.rs:5488`), and moves the states on `stage_started`,
`stage_completed` and `stage_result`. `status_title` becomes `Step {n} of {m} · {name}` in the
host (`worker.rs:2130-2137`) and in the window's mirror (`src/lib/workerEvents.ts:222-233`);
both take the name from one table per side that a test compares with `planLine.ts`.

The share of the bar each step gets was fixed in `341f42ad` and is not touched.

## 2. Step results after a job

### Layout

Above the canvas, left of the `Original | Split | Enhanced` switch, when the result shown is
that of the last job and that job had two or more steps:

```
┌ IMG_2041.jpg        6400 × 4268 ──────────────────────────────────────────────┐
│  Steps  [ JPEG ][ Colour ][ Noise ][● 4× ]        [ Original | Split | Enhanced ]
```

The chips choose what the result side shows; the switch still chooses how it is compared with
the original. `● 4×` is the finished result and is pressed by default. A skipped step is a
disabled chip with its reason as `title`.

### Copy

| Where | String |
| --- | --- |
| Group label | `Steps`; accessible name `Result after each step` |
| Chips | the plan-line names; the last one is `4×` (`2×`, `3×`) for an upscale, else the last fix's name |
| Chip `title` | `The picture after the Noise fix` · last: `The finished result` · skipped: the step's note |
| Result side label (`showingLabel`) | `After Noise` |
| Chip while its picture loads | the pane's existing loading note (`Loading After Noise…`) |

### Behaviour

- **What is kept.** For a photo job that is alone (not part of a batch, not a trial run, and
  nothing else queued when it is sent), the host adds `stage_results_directory`:
  `<work_root>/steps/<job_id>`. The worker writes each finished step there as `<index>.png`:
  8-bit RGB (with alpha when the picture has it), converted to sRGB like the previews, PNG
  compression level 1, on a background writer so the next step starts at once (the pattern of
  `SavePipeline`, `image_job.py:89`). The job waits for the writer before `job_completed`.
  `stage_result.image_path` names the file; `job_steps[n].kept` is true once it is reported.
- **Not exports.** A step file is never offered to `Use as input`, `Reveal` or `Open`, and is
  not in the result history.
- **How a chip shows it.** A chip calls `request_image_comparison` (`commands.rs:2613`) with
  `showing` set to `<job_id>#<index>`. The host resolves such an id to the step file instead of
  asking the result history (`media_result_output`): one function,
  `resolve_pair_target(database, runtime, media_id, target)`, replaces the three lookups at
  `commands.rs:2635-2641`, `src-tauri/src/compare.rs:525-528` and `compare.rs:716-721`. From
  there the existing path does everything: a 2048 px preview and sharp regions while zoomed.
- **Lifetime.** `runtime.last_steps` (`{ job_id, media_id, steps: JobStep[] }`) is set when the
  job completes and cleared, with its folder, when: the next job is dispatched
  (`dispatch_once`, `worker.rs:922`), the app quits (`RunEvent::Exit`, `src-tauri/src/lib.rs:322`),
  the file or its results are removed (`remove_media`, `clear_results`, `clear_media`,
  `commands.rs:470-585`), or the job's result is deleted. `<work_root>/steps` is emptied at
  start-up, for a crash.
- **A cancelled or failed job** keeps nothing.
- **Disk.** A 24-megapixel photo is about 30 to 70 MB per step. If a step file cannot be written,
  that chip is disabled with the title `This step's picture could not be kept.`; the job goes
  on.
- **Video, Edit, batches:** no chips.

## 3. The preview patch

### Layout

Viewer head and canvas, photo, three fixes ticked and 4×, everything installed. The viewer is
at 100 %; the patch is the framed square, its tab sits above it.

```
┌ IMG_2041.jpg   1600 × 1067                                       [● Preview ] ┐
│                                                                               │
│              ┌ ⠿ [ Original ][ JPEG ][ Colour ][● Noise ][ ◐ 4× ]   4× · 3 s ┐│
│              ├──────────────────────────────────────────────┬────────────────┘│
│              │                                              │                 │
│              │     this square: the photo after Noise       │                 │
│              │     (the newest finished step)               │                 │
│              │                                              │                 │
│              └──────────────────────────────────────────────┘                 │
│                 the rest: the original, sharp                                 │
└───────────────────────────────────────────────────────────────────────────────┘
```

`⠿` is the grip. The tab's right side is the state text. Tab states:

```
 all done            ⠿ [ Original ][ JPEG ][ Colour ][ Noise ][● 4× ]
 waits for a click   ⠿ [ Original ][ JPEG ][ Noise ]        [ Update preview · about 12 s ]
 model missing       ⠿ [ Original ][ JPEG ][ ↓ Noise ][ 4× ]  [ Download 30 MB and preview ]
 failed              ⠿ [ Original ]                            Preview failed.  Try again
 video               ⠿ This frame  [ Original ][ Noise ][● 2× ]
 two models          ⠿ [ Original ][ JPEG ][● 4× ]
                       [● A  LocalSR Photo S ][ B  SwinIR ×4 Real ]      [ Use B ] [ Done ]
```

The frame is 1 px `--accent` (dashed while its picture is older than the settings); the tab is
`--dialog-bg` with `--surface-border`, 28 px high, above the frame, or below it when the frame
touches the top of the canvas.

### Copy

| Where | String |
| --- | --- |
| Viewer head button | `Preview`, `aria-pressed`; `title` `Show what the ticked fixes do on a part of this photo` (`video`: `on this frame`) |
| Button, disabled while a job runs or waits | `title` `Available when the job finishes.` |
| Button, nothing to run | `title` `Tick a fix or choose a larger size first.` |
| Fix popover link | `See it on this photo` (video: `See it on this frame`) |
| Fix popover link, no photo open or model missing | `See an example` (opens the library on the fix's model) |
| Tab, accessible name | `Preview patch`; grip `title` `Drag to move · arrow keys move it` |
| Chips | `Original`, then the plan-line names; the upscale is `4×` |
| Running | chip with the dot; state text `Noise · 3 s` |
| Done | state text empty; `title` of a chip `After Noise · 2.1 s` |
| Stale, waiting for a click | button `Update preview`, with ` · about 12 s` when a time is known |
| Model missing | chip `↓ Noise`, `title` `RealPLKSR Denoise is not downloaded.`; button `Download 30 MB and preview` |
| Model needs a licence tick or an own file | no button; state text `Noise needs a step in the panel first.` |
| Skipped step | chip disabled, struck through; `title` the engine's reason |
| Failed | `Preview failed.` and the link `Try again`; the message as `title` |
| Video label | `This frame`; `title` `De-flicker, added frames and sound cannot be shown on one frame.` |
| Compare switch | `A  <model>`, `B  <model>`; buttons `Use B`, `Done`; `title` of the switch `Press X to flip` |
| Library button | `Try on this photo` (video: `Try on this frame`); disabled `title` `Open a photo first.` · `Download the model first.` |

### Behaviour

**Availability.** The button shows when: the engine lists `patch_preview`; the setting is not
`off`; the task is Upscale & restore; the selected file is a photo or a frame-by-frame video.
It is disabled while a job runs or is queued and when the plan is empty.

**Turning on.** `preview_patch_shown` becomes true (saved). The comparison view becomes
`Original`; while the view is `Split` or `Enhanced` the patch is hidden and comes back with
`Original`. If the zoom is below 100 % (`actualPixelZoom`, the `1:1` button, `src/PreviewPane.svelte:1359`)
the view zooms to 100 % centred on the patch. Turning off changes neither zoom nor view.

**Region.** 384 × 384 source pixels, or the picture's shorter side when that is smaller. The
first position per file comes from `pickPatchRegion` (`lib/previewPatch.ts`): on the source
preview, the cell with the highest luminance variance weighted by `1 − 0.5 × distance from the
centre`; the centre when the preview cannot be read. Positions snap to 16 px. The position is
kept per file for the session.

**Moving.** Drag the tab. While dragging, the frame shows the original; 250 ms after release a
new preview is asked for. With the tab focused, arrow keys move it by 32 px, with Shift by
128 px.

**Drawing.** The chosen chip's picture is an `<img>` placed over the photo with `cropStyle`
(`src/lib/compareRegions.ts:36`), `Original` being the viewer's own sharp region. An upscaled
chip has 4× the pixels in the same square, so at 400 % zoom it is pixel for pixel. Holding `M`
shows `Original` in the patch too.

**Which chip shows.** The newest finished step, following the run, until the user presses a
chip; then that step, for as long as the plan keeps it.

**When it computes.** A request goes out when the patch is on and any of these changes: the
ticks, their order, a model, Size, Content, Quality, the file, the position. Settings changes
are collected for 400 ms. A newer request replaces an older one; the older run is cancelled.
The request carries the same `StartBatchInput` Start would send (`buildStartInput`,
`src/lib/startInput.ts:68`), so the patch runs on the same hardware route with the same tile
settings.

**What runs.** The steps up to the first one whose model is missing or needs the user. Later
steps are chips without a picture. Results are kept per file, region and step prefix
(below), so unticking a fix is immediate and a new last step runs only itself.

**Automatic or on click.** The host decides for each request (setting below). When it decides
to wait, the frame keeps the last picture with a dashed border and the tab offers
`Update preview`.

**While it runs.** The frame keeps its picture; chips fill in as steps finish; the state text
names the running step with whole seconds. No spinner over the photo.

**A job starts.** The running preview is cancelled, the patch hides, the button is disabled.
When the queue is empty again the patch returns with what it had.

**Two models.** `Try on this photo` in the Model library (beside `Use for this job`, for an
installed model that fits the slot the library was opened for) closes the library, turns the
patch on and asks for variant B: the plan with that slot's model replaced. The tab gets its
second row. `X` flips A and B when focus is not in a text field. `Use B` applies the model the
way `Use for this job` does and drops the second row; `Done` drops B. Trying another model
replaces B.

**Video.** The frame is the one the viewer shows before a job: the first frame
(`frame_index` 0). The plan's De-flicker, frame and audio stages are left out. Choosing another
frame is later.

**Edit, SeedVR2, FlashVSR:** no button.

### The setting

Settings › General, directly under `Show previews while a job runs`
(`src/SettingsSheet.svelte:332-353`), a row with a select (the pattern of `When you stop a job`,
`:355-383`):

```
 Preview fixes on your photo                          [ Automatic            ▾ ]
 Shows what the ticked fixes do on a part of the open
 photo before you start. Automatic updates the preview
 by itself when that takes about 5 seconds or less on
 this computer, and waits for a click when it takes
 longer.
```

| Field | Values (label) | Default | Notes |
| --- | --- | --- | --- |
| `preview_patch` | `"auto"` (`Automatic`), `"live"` (`Always update by itself`), `"manual"` (`Update when I click`), `"off"` (`Off`) | `"auto"` | Host-owned preference like `viewer_background` (`types.rs:411-414`, `apply_preference`, `commands.rs:846`). An unknown value loads the default. |
| `preview_patch_shown` | boolean | `false` | The remembered on/off of the viewer button. Saved by `set_preference`. Allowed while a job runs. |

The default rule, in the host, for a request without `force`:

1. `off`: refused. `manual`: state `stale`. `live`: runs.
2. `auto`: the host adds up the measured seconds of the steps that would run, that is the steps
   after the prefix the patch already shows for this file and position. A step whose model has
   never been measured on this device counts as 0. 5.0 s or less: runs. More: state `stale`
   with `estimate_seconds`.
3. `Update preview` sends `force: true`.

Measured seconds: for each `(model_id, device)` the mean of the last three `elapsed_seconds`
the engine reported for that step in a patch run that was not answered from the cache. Kept in
the database beside the job timings (`estimate_request_seconds`, `src-tauri/src/database.rs:777`),
in their own table: a patch is a cold load and a small crop, and must not move job estimates.

### Engine and host

| Side | Addition |
| --- | --- |
| Engine feature | `patch_preview` |
| Request | `patch_request`: `patch_id`, `slot` (`"a"`, `"b"`), `image_path`, `frame_index` (−1 for a photo), `region` (`x`, `y`, `width`, `height` in source pixels), `padding` (32), `stages` (the job's `pipelineStage` list, cut after the last step that can run), `output_scale`, and the job's `device`, `precision`, `tile_size`, `halo`, `neural_engine`, `safe_memory` |
| Messages | `patch_started` (`patch_id`); `patch_stage` (`patch_id`, `stage_index`, `stage_count`, `state`: `running`, `done`, `skipped`; for `done`: `png_base64`, `width`, `height`, `elapsed_seconds`, `cached`; for `skipped`: `note`); `patch_completed` (`patch_id`, `elapsed_seconds`); `patch_failed` (`patch_id`, `error_message`); `patch_cancelled` (`patch_id`) |
| Worker, new `src/localsr/worker/patch_preview.py` | Decode the file with the job's decoder (`ImageManager.load`, `image_job.py:578`; the first frame for a video), cut the region plus padding, run the stages with the job's own step functions (`_run_stage` and the primary), trim the padding (times the scale) for the picture sent, keep the padded working picture for the next step. PNG, lossless. No output file, no scratch file. |
| Colour | A step whose model is DDColor runs on the whole decoded picture reduced to 512 px on the long side to get the colour planes, then applies them to the padded crop. The planes are cached per file and model, so moving the patch does not run the model again. |
| Worker cache | In memory, keyed by file identity (as `compare_regions.py:87-88`), frame, region, padding and the step prefix (for each step: step id, fix, model file identity, parameters, device, precision). Holds the working picture and the encoded PNG of each step. 128 MB, oldest out first. A request starts from its longest cached prefix and answers cached steps at once with `cached: true`. |
| Scheduling in the worker | `patch_request` is taken by the main loop like a job (`server.py:812-822`), so it never overlaps one, and runs under `_activate_job(patch_id)` so `cancel_request` stops it between tiles and steps. The reader thread (`_read_line`, `server.py:580`) drops a queued `patch_request` when a newer one for the same slot arrives. |
| Host, new `src-tauri/src/patch_preview.rs` | Command `request_patch_preview(media_id, slot, region, input, replace, stage_limit, force)` and `cancel_patch_preview(slot)`. Under `state.scheduler`: refuse with state `busy` while `runtime.active_job_id` is set or a job is queued; check the file's grant (`check_media_input`) and validate `input` as Start does (`validate_start_input`, `commands.rs:4658`, without the output checks); build the stages with `image_pipeline_stages` (`commands.rs:5488`); apply the default rule; cancel the slot's running patch; send. `replace` is `{ slot, fix, model_id }` for variant B. |
| A job is dispatched | `dispatch_once` (`worker.rs:922`) first cancels running patches and marks both slots `busy`. |
| Pictures | `patch_stage` PNGs are stored in the preview folder like previews. `preview_cache.rs` accepts a second extension, `.png` (`PREVIEW_EXTENSION`, `:19`; `confined_preview_path`, `:85-129`); its tests are extended for it. The asset scope already covers the folder (`lib.rs:176-177`). |
| Snapshot | `runtime.patches: { a: PatchState, b: PatchState }` |

```ts
interface PatchState {
  state: 'idle' | 'running' | 'ready' | 'stale' | 'busy' | 'failed';
  media_id: string;
  region: Region | null;
  estimate_seconds: number;        // 0 when unknown
  error: string;
  steps: {
    kind: string; fix: string; model_id: string;
    state: 'waiting' | 'running' | 'done' | 'skipped' | 'missing';
    url: string; width: number; height: number; seconds: number; note: string;
  }[];
}
```

The window never names models to the engine and never sends paths; it sends what Start sends.

## 4. Example pictures in the Model library

### Layout

The detail pane is 344 px wide with 16 px padding (`.sheet-body`, `src/styles.css:3965-3968`;
`.detail`, `:4228-4236`), so a picture is 312 × 195.

```
│ RealPLKSR Denoise                    │
│ Fast photo denoising                 │
│ ┌──────────────────────────────────┐ │
│ │                                  │ │
│ │   (312 × 195, pixel for pixel)   │ │
│ │                                  │ │
│ └──────────────────────────────────┘ │
│ [ Before ][● After ]    Example · Noise
│ A very fast, lightweight denoising … │
│ ┌────────────────┬─────────────────┐ │
│ │ Scale  ×1      │ Download  30 MB │ │
│ │ Memory …       │ Speed  Fast     │ │
│ │ Runs on   Apple GPU (Metal)      │ │   ← the fifth fact spans the row
│ └──────────────────────────────────┘ │
```

### Copy

| Where | String |
| --- | --- |
| Switch | `Before`, `After` (a radio group named `Example picture`); pressing and holding the picture shows `Before` |
| Caption, the model's own example | `Example · Noise` · for an upscaler `Example · 4×` |
| Caption, another model's example for the same fix | `Example of the Noise fix, made with RealPLKSR Denoise` |
| `alt` | `Example: a noisy photo before and after the Noise fix` (one sentence per example, in the manifest) |

### Behaviour

- A model with its own example shows it. A fix model without one shows its fix's example with
  the second caption. An upscaler without one shows no picture.
- The files are 624 × 390 and are shown at one file pixel per device pixel: at 312 × 195 CSS
  pixels on a 2× display, and as the centre 312 × 195 of the file on a 1× display. Never
  scaled.
- `.facts > div:last-child:nth-child(odd) { grid-column: 1 / -1; }` removes the empty cell in
  all five fact lists (`ModelLibrary.svelte:1359`, `:1435`, `:1500`, `:1574`, `:1718`).

### The files

`src/assets/examples/`, bundled by Vite. WebP, quality 88, 624 × 390, no metadata. 14 files,
1,000,000 bytes at most in total (about 70 KB each); a test adds them up. `examples.json`
beside them is the manifest:

```json
{ "id": "noise", "kind": "fix", "fix": "noise", "model_id": "denoise_realplksr_1x",
  "before": "noise-before.webp", "after": "noise-after.webp", "alt": "…",
  "source": { "title": "…", "author": "…", "url": "…", "licence": "CC0-1.0" },
  "made": { "app": "0.7.0", "settings": "Same size · Noise · RealPLKSR Denoise" } }
```

| Example | Model | The picture must show |
| --- | --- | --- |
| `noise` | RealPLKSR Denoise | A high-ISO photo with a flat dark area and a fine texture (fabric, hair, foliage) in the same crop, so the cleaning and the smoothing are both visible. |
| `jpeg` | FBCNN Color | A JPEG saved at about quality 20: 8 × 8 blocks in a smooth area (sky, wall) and ringing along a hard edge or lettering. |
| `blur` | NAFNet GoPro Deblur | Camera shake on a scene with edges or lettering. Not a sharp picture and not line art (the model is unstable there). |
| `colour` | DDColor | A black-and-white photo with things of known colour (skin, sky, grass) and one thing the model has to guess (clothing, a car). |
| `photo-quick`, `photo-best` | LocalSR Photo S, RealPLKSR ×4 NomosWebPhoto | The same source crop for both, 156 × 98 px enlarged 4×: foliage, a building edge, fabric. `Before` is the source enlarged with bicubic resampling; the two share one before file. |
| `art-quick`, `art-best` | Real-ESRGAN ×4 Anime, ESRGAN ×4 HFA2k | The same source crop for both: line art with flat colour and a screentone area. One shared before file. |

Sources are our own photos or CC0 / public-domain pictures. Every after picture is made by us
with the released model in LocalSR and cropped; nothing is retouched. Each entry records its
source and how it was made.

## 5. Recipes

### The menu

Under `Recipes ▾`, 260 px, as today (`src/EnhancePanel.svelte:345-358`). Three shapes:

```
 six recipes or fewer            seven or more                  none of your own
┌────────────────────────────┐  ┌────────────────────────────┐  ┌────────────────────────────┐
│ ✓ Phone photos             │  │ ✓ Phone photos           ⌃ │  │   Old scan                 │
│   Noise → Upscale 4×       │  │   Noise → Upscale 4×       │  │   Upscale 2×               │
│   Old slides               │  │   Old slides             ⌃ │  │   Low-light phone photo    │
│   JPEG → Upscale 2×        │  │   JPEG → Upscale 2×        │  │   Noise                    │
│ ────────────────────────── │  │ ────────────────────────── │  │   Drawing or anime         │
│ Browse recipes…            │  │   Web banners              │  │   Upscale 4×               │
│ ────────────────────────── │  │   JPEG                     │  │   Web image (JPEG)         │
│ Update “Phone photos”      │  │   (three more recent)      │  │   JPEG → Upscale 2×        │
│ Save current as recipe…    │  │ ────────────────────────── │  │ ────────────────────────── │
└────────────────────────────┘  │ Browse recipes…            │  │ Browse recipes…            │
                                │ ────────────────────────── │  │ ────────────────────────── │
                                │ Save current as recipe…    │  │ Save current as recipe…    │
                                └────────────────────────────┘  └────────────────────────────┘
```

`⌃` is the pin mark. Rules, in `menuRows` (`lib/recipeList.ts`):

1. Entries are the user's recipes and any pinned workflow.
2. Six or fewer: all of them, pinned first in pin order, then the rest in saved order. No
   separator between them.
3. Seven or more: the pinned ones in pin order (the first six), a separator, then the most
   recently applied ones that are not pinned and fit the open file's kind, until there are six
   rows.
4. A pinned row that does not fit the open file (a video recipe on a photo) stays, disabled,
   as today. A recent one is left out.
5. None of the user's own and nothing pinned: the workflows that fit the open file.
6. `Update “<name>”` shows when the recipe applied last in this session is one of the user's,
   still exists, and no longer matches the settings (`recipeMatches`, `src/lib/recipes.ts:54`).
   It saves the current settings under that recipe's id and name.
7. No ✕ on a row. `Save current as recipe…` is as today; at 100 recipes it is disabled with
   the `title` `100 recipes is the limit. Delete one in Browse recipes… first.`
8. A row's second line is `recipePlanLine(recipe, catalog)`: the names of `planLine.ts` for the
   recipe's stages, plus `2× frames`, `De-flicker`, `Clean audio` for a video recipe. A
   same-size recipe without fixes reads `Same size`.

### The Recipes library

`RecipesLibrary.svelte`, in the Model library's shell: `.modal-backdrop.library-backdrop`,
`.sheet`, `.sheet-head`, `.sheet-body`, `.rail`, `.list`, `.row`, `.detail`, `use:modal`
(`src/ModelLibrary.svelte:553-606`, `:720-740`, `:1272`; `src/styles.css:3821-4104`).

```
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│ Recipes                                                                              ✕  │
├──────────────┬────────────────────────────────────────────┬─────────────────────────────┤
│ My recipes 23│ [ Search recipes                         ] │ Phone photos              ⌃ │
│ Recommended 4│ ────────────────────────────────────────── │ Photo recipe                │
│ ──────────── │ ⌃ Phone photos                     Active  │                             │
│ Photo      19│   Noise → Upscale 4×                       │ 1  Noise    RealPLKSR Denoise
│ Video       8│ ⌃ Old slides                               │ 2  Upscale 4×  LocalSR Photo S
│              │   JPEG → Upscale 2×            290 MB       │                             │
│              │   Web banners                              │ Output     PNG              │
│              │   JPEG                                     │ Quality    Quick            │
│              │   Clips for the family       Video         │ This run   Tile 256 · Halo 32 · FP16
│              │   Noise → Upscale 2× → 2× frames           │ Nothing to download         │
│              │   …                                        │                             │
│              │                                            │ [ Apply ]  [ Unpin ]        │
│              │                                            │ Duplicate · Rename · Delete │
└──────────────┴────────────────────────────────────────────┴─────────────────────────────┘
```

| Part | Content |
| --- | --- |
| Rail | `My recipes` n · `Recommended` 4 · separator · `Photo` n · `Video` n. `Photo` and `Video` list the user's recipes and the workflows for that kind. Opens on `My recipes`, or on `Recommended` when the user has none or came from the empty screen. |
| Search | Above the list, `data-autofocus`. Matches every typed word, without case, in the name, the plan line and the models' names. It searches the rail's current group. |
| Order | Pinned in pin order, then by last use, then by name. |
| Row | Name with the pin mark; the plan line. On the right one of: `Active` (it matches the settings), the download size when a model is missing, `Video` or `Photo` when it does not fit the open file. |
| Empty | `My recipes`, none: `No recipes yet. Set up the panel, then choose Recipes › Save current as recipe…` · no match: `No recipes match “slides”.` |
| Detail, a recipe | Name; `Photo recipe` or `Video recipe`; the steps, numbered, each with its model's name and, when missing, its size (`Needs your file` for an own file that is gone); `Output` (`outputSummary`, `src/lib/enhancePanel.ts`, without the folder); `Quality` (`Quick`, `Best`, `Your own choice`); `This run` (tile, halo, precision; `Safe memory` when set); `290 MB to download` or `Nothing to download`. |
| Actions, a recipe | `Apply` (primary), `Pin` / `Unpin`, then the links `Duplicate`, `Rename`, `Delete`. |
| Actions, a workflow | `Use workflow` (primary), `Pin` / `Unpin`, `Duplicate to My recipes`. See section 6 for its detail. |
| `Apply` | Applies (`applyRecipe`, `src/App.svelte:4033`), records the use, closes the dialog, returns focus to `Recipes`. Disabled when it does not fit the open file: `title` `Select a video first.` / `Select a photo first.` |
| `Rename` | The name becomes a field (`Recipe name`, 60 characters as the host cuts, `commands.rs:982`), Enter saves, Escape leaves it. Saved with `save_recipe` under the same id. |
| `Duplicate` | Saves a copy as `<name> copy` (`copy 2`, …) and selects it. Disabled at 100. |
| `Delete` | Today's question in place of the actions: `Delete Phone photos?` `This can’t be undone.` `Delete` / `Cancel`. |
| Keys | Focus starts in the search. ↓ from the search goes to the list; ↑ and ↓ move through rows (`tabFromKey`, `src/lib/keyboard.ts`); Enter on a row, or in the search with a row selected, applies it when it fits; a double click applies; Escape closes. |
| While a job runs | The dialog is not offered, as the menu is not (`Locked while running`). |

### Pins, recent, shortcuts

| Field (`UiSettings`) | Value | Notes |
| --- | --- | --- |
| `recipe_pins` | list of ids, pin order, at most 20 | A recipe's id or a workflow's (`workflow.old-scan`). Absent in an older settings file: the host fills it with the ids of the first seven recipes, so today's ⌘3 to ⌘9 keep their targets. |
| `recipe_recent` | list of ids, newest first, at most 12 | Written by the window in the same settings save as the apply. |

Both are ordinary settings fields, checked in `validate_settings` (`commands.rs:4300`): text ids of
at most 64 characters, no duplicates, the limits above. `delete_recipe_checked`
(`commands.rs:1192`) removes the id from both. The window ignores ids it does not know.

Presets menu (`menu_layout`, `native_menu.rs:132-144`): `Quick Preset (Fast)` ⌘1, `Best Quality
Preset` ⌘2, separator, the first seven pins with ⌘3 to ⌘9 in pin order, separator, `Browse
Recipes…` ⇧⌘R. A recipe that is not pinned has no menu item. The menu is rebuilt when the pins
change, as it is when a recipe is saved (`commands.rs:971-975`); the host reads the workflows'
names from the same file the window does. The ids stay `preset.recipe.<id>`
(`src/App.svelte:4280-4284`), and the handler also accepts a workflow id.

Workflows are not stored and do not count toward the 100 (`store_recipe`, `commands.rs:1046`).

## 6. Workflows

### What they are

`src-tauri/resources/workflows.json`, read by the window (as `lib/steps.ts:1` reads
`steps.json`) and by the host for the menu names:

```json
{ "id": "workflow.web-jpeg", "name": "Web image (JPEG)", "media": "image",
  "for": "For small, blocky pictures saved from websites or chats.",
  "note": "Removes compression blocks, then enlarges 2×. Detail the compression destroyed does not come back.",
  "content": "photo", "quality": "quick", "fixes": ["jpeg"], "output_scale": 2,
  "example": "jpeg" }
```

`workflowSettings(workflow, snapshot, settings)` in `lib/workflows.ts` turns one into the
settings a user would reach by hand: Content, the ticks in the listed order, Size, and Quality,
with the models Quality picks (`choosePresetModel`, `src/lib/plan.ts:647`; `chooseFixModel`,
`src/lib/fixStages.ts:90`), the user's remembered models included. At Same size the task is
Restore, as `setSize` makes it. Output, hardware, tiles and precision are not touched.
`workflowMatches` is true when the settings equal that result; then the workflow has the ✓.
Changing anything drops the ✓; `Save current as recipe…` keeps the changed version.
`Duplicate to My recipes` applies nothing: it saves the workflow's settings, merged over the
current ones, as an ordinary recipe named like the workflow.

### The four

Models and sizes are what the bundled catalog resolves to with nothing remembered
(`resources/model-catalog.json`; sizes as `formatBytes` prints them). A test holds this table.

| | Old scan | Low-light phone photo | Drawing or anime | Web image (JPEG) |
| --- | --- | --- | --- | --- |
| id | `workflow.old-scan` | `workflow.low-light` | `workflow.drawing` | `workflow.web-jpeg` |
| Content | Photo | Photo | Illustration | Photo |
| Fix | none | Noise | none | JPEG artifacts |
| Size | 2× | Same | 4× | 2× |
| Quality | Best | Quick | Quick | Quick |
| Plan line | `Upscale 2×` | `Noise` | `Upscale 4×` | `JPEG → Upscale 2×` |
| Models | RealPLKSR ×4 NomosWebPhoto (`realplksr_nomoswebphoto_x4`, Labs) | RealPLKSR Denoise (`denoise_realplksr_1x`) | Real-ESRGAN ×4 Anime (`realesrgan_x4plus_anime_6b`) | FBCNN Color (`fbcnn_color`), then LocalSR Photo S (`localsr_quick_x4_v1`) |
| Download | 30 MB (29,683,482 bytes) | 30 MB (29,559,554) | 18 MB (17,938,799) | 290 MB (287,755,111 + 2,479,984) |
| `for` | `For scanned prints, slides and negatives.` | `For phone photos taken indoors or at night that look grainy.` | `For drawings, anime frames, comics and flat artwork.` | `For small, blocky pictures saved from websites or chats.` |
| `note` | `Enlarges and sharpens. Does not remove scratches or dust, and does not change colours.` | `Removes grain at the original size. Very fine texture, such as hair or fabric, can get smoother.` | `Keeps lines clean and colours flat. Not for photos: it smooths skin and foliage.` | `Removes compression blocks, then enlarges 2×. Detail the compression destroyed does not come back.` |
| Example | `photo-best` | `noise` | `art-quick` | `jpeg` |

Colourising is not part of Old scan. Its detail ends with one more grey line:
`Black and white? Tick Colour afterwards. The colours are the model's guess.`

### The card (the library's detail pane for a workflow)

```
│ Old scan                             │
│ Recommended workflow                 │
│ ┌──────────────────────────────────┐ │
│ │      (the example, 312 × 195)    │ │
│ └──────────────────────────────────┘ │
│ [ Before ][● After ]                 │
│ For scanned prints, slides and       │
│ negatives.                           │
│ Enlarges and sharpens. Does not      │
│ remove scratches or dust, and does   │
│ not change colours.                  │
│                                      │
│ 1  Upscale 2×   RealPLKSR ×4 NomosWebPhoto · Labs
│ Quality    Best                      │
│ 30 MB to download                    │
│                                      │
│ [ Use workflow ]  [ Pin ]            │
│ Duplicate to My recipes              │
└──────────────────────────────────────┘
```

Name, `for`, the plan (steps with models), the download, the `note`, one example. No badge, no
rating. `Use workflow` fills the panel, closes the dialog and starts nothing.

### The empty screen

One text link under the drop area, outside its button (`<button class="well">`,
`src/EmptyStage.svelte:103-128`, cannot contain another control):

```
          ┌───────────────────────────────────────────┐
          │        Drop photos or videos here         │
          │  or click to choose · upscaled and …      │
          │  Upscale 2–4×   Remove noise, blur, JPEG  │
          └───────────────────────────────────────────┘
                   or start from a workflow…
```

`.link-button`, `--secondary`, 12 px, 14 px under the well. It opens the Recipes library on
`Recommended`. Nothing else on the stage changes. With no file open, `Use workflow` sets the
panel and the stage stays as it is.

## 7. Prompt examples

### Layout

```
│ WHAT TO CHANGE                          Examples ▾ │
│ ┌────────────────────────────────────────────────┐ │
│ │ Replace the background with a sunset beach. …  │ │
│ └──────────────────────┌───────────────────────────┐
│                        │ Scratches and dust        │
│                        │ Remove the scratches, …   │
│                        │ Colour                    │
│                        │ Add natural colour to …   │
│                        │ Repaints the photo. To    │
│                        │ only add colour, use      │
│                        │ Fix › Colour.             │
│                        │ Remove something          │
│                        │ Remove [the person on …   │
│                        │ New background            │
│                        │ Replace the background …  │
│                        │ Film look                 │
│                        │ Give the photo the look … │
│                        │ ───────────────────────── │
│                        │ Edit redraws the picture  │
│                        │ and can change faces.     │
│                        │ Compare before you keep   │
│                        │ it.                       │
│                        │ Replaces your text.       │
│                        │ ⌘Z brings it back.        │
│                        └───────────────────────────┘
```

`Examples ▾` sits on the label row of `What to change` (`src/EnhancePanel.svelte:406-414`), right
aligned, and opens a `Popover` with `placement="row"`, 260 px, named `Prompt examples`. Each
entry is a button: the title at weight 600 and the prompt in `--secondary`, two lines at most,
the whole text as `title`.

### The five

| Title | Prompt |
| --- | --- |
| `Scratches and dust` | `Remove the scratches, dust specks and stains. Keep the faces, the grain and everything else unchanged.` |
| `Colour` | `Add natural colour to this black-and-white photo. Keep the faces, the lighting and every detail unchanged.` |
| `Remove something` | `Remove [the person on the left] and fill the gap with what is behind. Keep everyone and everything else unchanged.` |
| `New background` | `Replace the background with [a plain light grey wall]. Keep the person, their clothing and the light on them unchanged.` |
| `Film look` | `Give the photo the look of colour film: slightly warm highlights, soft contrast and fine grain. Keep the subject and the framing unchanged.` |

One sentence for the change, one for what to keep, as the field's placeholder is written.

### Behaviour

- **Insert.** Choosing an entry replaces the whole text and closes the popover with focus in
  the field. The brackets are not inserted; the words between them are selected, so typing
  replaces them. Without a slot the caret is at the end.
- **⌘Z.** The text is replaced through the field's own editing (`select()` and
  `document.execCommand('insertText')`), so the WebView's undo holds the old text. Where that
  call reports failure, the value is set directly and the component keeps the old text for one
  ⌘Z (Ctrl+Z) in the field.
- **`Replaces your text. ⌘Z brings it back.`** shows only while the field holds text that is
  not one of the examples. `Ctrl+Z` on Windows and Linux.
- **The Colour entry** carries its extra line: `Repaints the photo. To only add colour, use
  Fix › Colour.`
- **Families.** `PROMPT_EXAMPLE_FAMILIES` lists the edit model families the examples were
  tested on; the link shows only when the chosen edit model's family is in it. Each entry can
  carry its own `families` when a prompt passes on one and not the other.
- **Shipping.** The list starts empty. The link appears in 0.7.0 only after task E0: every
  prompt run on FLUX.2 klein 4B and Qwen Image Edit 2511 (`src/localsr/core/edit_catalog.json`)
  on three photos each. A prompt passes when the change happens, the kept things stay by eye,
  and no face changes. A prompt that fails on a family is reworded or leaves that family.
  Mage-Flow-Edit-Turbo and the user's own edit model get no examples.

## Files

New:

| File | Holds |
| --- | --- |
| `src/lib/jobSteps.ts` | `stepName(step, settings, isVideo)`, `stepLabel(steps)` (`Step 2 of 4 · Colour`), `planMarks(steps, names)` |
| `src/StepChips.svelte` | A row of chips; used by the viewer head (section 2) and the patch tab |
| `src/lib/previewPatch.ts` | `PATCH_SIZE`, `pickPatchRegion`, `snapRegion`, `movePatch`, `patchChips(state, plan)`, `shownStep`, `patchRequestKey` |
| `src/PreviewPatch.svelte` | Frame, tab, chips, state text, A/B row |
| `src/ModelExample.svelte` | Picture, `Before | After`, caption; used by the Model library and the workflow card |
| `src/lib/examples.ts`, `src/assets/examples/` | The manifest's types and lookups (`exampleForModel`, `exampleForFix`); the 14 files and `examples.json` |
| `src/lib/recipeList.ts` | `menuRows`, `recipePlanLine`, `searchRecipes`, `sortRecipes`, `duplicateName`, `recordUse`, `togglePin` |
| `src/RecipesLibrary.svelte` | The dialog |
| `src/lib/workflows.ts`, `src-tauri/resources/workflows.json` | `WORKFLOWS`, `workflowSettings`, `workflowMatches`, `workflowDownload` |
| `src/lib/promptExamples.ts`, `src/PromptExamples.svelte` | The five, `PROMPT_EXAMPLE_FAMILIES`, `insertPrompt(textarea, text)`; the popover's content |
| `src-tauri/src/patch_preview.rs` | The two commands, the default rule, the timing table's use |
| `src/localsr/worker/patch_preview.py` | The crop runner and its cache |
| `e2e/steps.e2e.ts`, `e2e/preview-patch.e2e.ts`, `e2e/recipes-library.e2e.ts` | The cases below |

Changed:

- `src/PreviewPane.svelte`: base rules (`:336`, `:365-370`, `:537-560`, `:595-660`); the step
  label; `canPeek` during a job (`:185`); in `.preview-header` (`:1187-1208`) the `Preview`
  button and the step chips; `<PreviewPatch>` inside the canvas beside the original region
  (`:1286-1297`).
- `src/App.svelte`: `stagePreview` from `job_steps` (`:4927-4929`); the status title is the
  host's (`:4999`); patch requests, the 400 ms collection and the A/B state; `applyWorkflow`;
  `lastAppliedRecipeId`; `addRecipe` (`:3937`) split into `buildRecipe(id, name)` and the save;
  the Presets handler (`:4280-4284`); the Recipes library's open state; `editPrompt` is passed
  on as today.
- `src/EnhancePanel.svelte`: plan-line marks (`:253-265`); `Examples ▾` (`:406-414`).
- `src/FixList.svelte`: the popover link (`:175-198`).
- `src/RecipesMenu.svelte`: rows from `menuRows`; no delete; `Browse recipes…`; `Update “…”`.
- `src/ModelLibrary.svelte`: `<ModelExample>` after the title block (`:1709-1716` and the other
  four detail blocks); `Try on this photo` in `.actions`.
- `src/EmptyStage.svelte`: the link; prop `openWorkflows`.
- `src/SettingsSheet.svelte`: the row after `:353`.
- `src/styles.css`: the `.facts` rule; `.step-label`, `.step-chips`, `.patch-frame`,
  `.patch-tab`, `.example`; the recipe-delete rules go with the ✕.
- `src/lib/types.ts`, `src/lib/api.ts`, `src/lib/workerEvents.ts` (`:222-233`), `src/lib/demo.ts`.
- `src-tauri/src`: `types.rs` (`JobStep`, `PatchState`, the four settings fields with their
  defaults), `worker.rs` (`stage_result`, `patch_*`, `stage_started`, dispatch), `commands.rs`
  (`build_job_request` `:5233`, `validate_settings`, `apply_preference`, `resolve_pair_target`,
  recipe pins), `compare.rs`, `preview_cache.rs`, `native_menu.rs`, `protocol.rs`, `database.rs`
  (patch timings), `lib.rs` (commands, exit clean-up), `settings.rs` (pins for an older file).
- `src/localsr/worker/image_job.py`, `server.py`, `request_validation.py` (`validate_image_job`,
  `:392`, and a `validate_patch_request`), `src/localsr/protocol/messages.py`, and both copies
  of the schema (`protocol/worker-protocol.schema.json`,
  `src/localsr/protocol/worker-protocol.schema.json`), `protocol/README.md`.
- `e2e/fake-host.ts`: `job_steps`, `last_steps`, `patches`, the two patch commands with a
  scenario (`patch?: { delayMs, fail?, missing? }`), pins and recent in settings.
- Docs: `CHANGELOG.md` under 0.7.0; `THIRD_PARTY_NOTICES.md` if an example's source asks for
  a credit.

## Tasks

Each UI task is under an hour for a UI worker and ends with `./local-ci.sh frontend` green; it
updates the vitest and e2e tests its change breaks and adds the ones named. **ENGINE** tasks
touch the worker or the host and end with `./local-ci.sh rust` and `./local-ci.sh test-only`
green. Tasks marked **US** are done by us, not by a worker. Screenshot baselines wait for the
last task of each track.

S1, S3, S4, R1, R2, X1, W1 and E1 need nothing and can start at once. The UI tasks of track S
run against the fake host, which sends `job_steps` from S3 on.

| # | Task | Touches | Needs | Done when |
| --- | --- | --- | --- | --- |
| S1 | **ENGINE.** `stage_result`, `stage_picture_max_dimension`, `step` and `fix` on the stage messages, feature `stage_results` | `image_job.py`, `messages.py`, `server.py`, `request_validation.py`, both schemas, `protocol/README.md` | | Python tests "Steps" pass. |
| S2 | **ENGINE.** Host: `job_steps`, the request field, storing step pictures, title and detail wording | `types.rs`, `worker.rs`, `commands.rs`, `protocol.rs`, `preview_cache.rs` | S1 | Host tests "Steps" pass. By hand: a four-step photo job shows no small picture between steps. |
| S3 | Types, reducer, fake host for `job_steps`; `lib/jobSteps.ts` | `lib/types.ts`, `lib/workerEvents.ts`, `lib/jobSteps.ts`, `e2e/fake-host.ts` | | vitest "jobSteps" and the reducer cases pass. |
| S4 | Viewer base rules: no 320 px base, no clear before the step picture, `M` during a job | `PreviewPane.svelte`, `App.svelte`, `PreviewPane.test.ts` | | vitest "Viewer between steps" passes. Independent of S3 for rules 2 to 4; rule 1 uses S3's type. |
| S5 | Step label in the viewer, title in the bar, marks on the plan line | `PreviewPane.svelte`, `App.svelte`, `EnhancePanel.svelte`, `styles.css` | S3 | vitest "Step names" passes. |
| S6 | **ENGINE.** Full-size step files, `last_steps`, clean-up, `resolve_pair_target` | `image_job.py`, `commands.rs`, `compare.rs`, `worker.rs`, `lib.rs` | S2 | Python and host tests "Step results" pass. |
| S7 | `StepChips.svelte`; chips in the viewer head | `StepChips.svelte`, `PreviewPane.svelte`, `App.svelte`, `fake-host.ts` | S3, S5 | vitest "Step chips" passes. |
| S8 | `steps.e2e.ts`; baselines | `e2e` | S4, S5, S7 | e2e "Steps" green on Chromium and WebKit. |
| R1 | `lib/recipeList.ts` | the file and its test | | vitest "recipeList" passes. |
| R2 | **ENGINE.** Host: `recipe_pins`, `recipe_recent`, the older-file rule, Presets menu, delete removes ids | `types.rs`, `settings.rs`, `commands.rs`, `native_menu.rs` | | Host tests "Recipes" pass. |
| R3 | Menu: rows from `menuRows`, no ✕, `Browse recipes…`, plan-line subtitle | `RecipesMenu.svelte`, `App.svelte`, `App.test.ts`, `recipes.e2e.ts` | R1 | vitest "Recipes menu" passes. |
| R4 | Library: shell, rail, search, list, keys | `RecipesLibrary.svelte`, `App.svelte` | R1 | vitest "Recipes library", list rows, passes. |
| R5 | Library: detail and actions (apply, pin, duplicate, rename, delete); recent on apply | `RecipesLibrary.svelte`, `App.svelte`, `fake-host.ts` | R4 | vitest "Recipes library", detail rows, passes. |
| R6 | `Update “<name>”`; the 100 limit's wording; Presets handler | `App.svelte`, `RecipesMenu.svelte` | R3 | vitest "Update" passes. |
| R7 | `recipes-library.e2e.ts`; baselines | `e2e` | R3 to R6, W2, W3 | e2e "Recipes" green. |
| W0 | **US.** Run the four workflows on three pictures each and compare with the other Quality; confirm or change the table | | | The table in section 6 is confirmed in writing. |
| W1 | `workflows.json`, `lib/workflows.ts` | the two files and the test | | vitest "workflows" passes, including the model table. |
| W2 | `Recommended` in the library; the workflow card; `applyWorkflow`; `Duplicate to My recipes`; pinned workflows in the menu | `RecipesLibrary.svelte`, `App.svelte`, `RecipesMenu.svelte` | W1, R5, X1 | vitest "Workflows in the library" passes. |
| W3 | Empty-screen link; the menu's empty state | `EmptyStage.svelte`, `EmptyStage.test.ts`, `RecipesMenu.svelte`, `App.svelte` | W1, R3 | vitest "Empty screen" passes. |
| X0 | **US.** Choose the sources, make the 14 files with the real models, write the manifest | `src/assets/examples/` | | The size test passes on the real files. |
| X1 | `ModelExample.svelte`, `lib/examples.ts` with placeholder files, the library's detail, the `.facts` rule | the two files, `ModelLibrary.svelte`, `styles.css`, `ModelLibrary.test.ts`, `library.e2e.ts` | | vitest "Examples" passes; the grey cell is gone in the baseline. |
| E0 | **US.** Test the five prompts on both families; fix the texts; fill `PROMPT_EXAMPLE_FAMILIES` | `lib/promptExamples.ts` | E1 | A short note of what passed per family. |
| E1 | `PromptExamples.svelte`, `lib/promptExamples.ts`, the link in the panel | the two files, `EnhancePanel.svelte`, `App.test.ts` | | vitest "Prompt examples" passes with a test family listed. |
| P1 | **ENGINE.** Worker: `patch_request`, the runner, the cache, Colour on the whole picture, cancel, feature `patch_preview` | `patch_preview.py`, `server.py`, `messages.py`, `request_validation.py`, both schemas | | Python tests "Patch" pass. |
| P2 | **ENGINE.** Host: the two commands, the scheduling slot, `patches` in the snapshot, `.png` in the preview folder | `patch_preview.rs`, `worker.rs`, `types.rs`, `preview_cache.rs`, `lib.rs` | P1 | Host tests "Patch" pass. |
| P3 | **ENGINE.** Timings table, the default rule, the two settings fields | `database.rs`, `patch_preview.rs`, `types.rs`, `commands.rs` | P2 | Host tests "Patch default" pass. By hand on the slowest Mac we support: note the seconds per fix; if a single fix is over 5 s there, tell Hermes. |
| P4 | `lib/previewPatch.ts`; types, api, fake host | the file and its test, `lib/types.ts`, `lib/api.ts`, `fake-host.ts` | | vitest "previewPatch" passes. Can start with the types of this spec, before P2. |
| P5 | `PreviewPatch.svelte`: frame, tab, chips, states, drag and keys | `PreviewPatch.svelte`, `PreviewPane.svelte`, `styles.css` | P4, S7 | vitest "Patch" rows 1 to 9 pass. |
| P6 | `Preview` button, requests from the app, the popover link, the Settings row | `PreviewPane.svelte`, `App.svelte`, `FixList.svelte`, `SettingsSheet.svelte` and its test | P5 | vitest "Patch" rows 10 to 16 pass. |
| P7 | `Try on this photo`, variant B, `X`, `Use B` | `ModelLibrary.svelte`, `PreviewPatch.svelte`, `App.svelte` | P6 | vitest "Patch, two models" passes. |
| P8 | **ENGINE**, small: a video's first frame as the patch source. UI: `This frame`, stages left out | `patch_preview.py`, `patch_preview.rs`, `lib/previewPatch.ts`, `PreviewPatch.svelte` | P2, P6 | Python test "Patch on a video" and vitest "Patch, video" pass. |
| P9 | `preview-patch.e2e.ts`; baselines; CHANGELOG for all tracks | `e2e`, `CHANGELOG.md` | P6, P7, P8, S8, R7 | `npm run e2e` green on Chromium and WebKit at both sizes. |

## Tests

### Python (`tests/`)

| Group | Test | Expected |
| --- | --- | --- |
| Steps (`test_live_preview.py`, `test_worker_protocol_contract.py`, `test_request_validation.py`) | A job with two fixes and an upscale, `stage_picture_max_dimension: 2048`, a 3000 × 2000 source | two `stage_result` messages, each before the next step's `stage_started`; `preview_width` 2048; `width` 3000 |
| | The same without the field | no `stage_result`; the messages of today, unchanged |
| | Colour as a step | a `stage_result` with a picture; still no 320 px full-image tile |
| | An unstable Blur | `stage_result` with `skipped: true`, a `note`, no picture |
| | `stage_picture_max_dimension` 4096 or `"x"` | `job_failed` naming the field |
| | `worker_ready` features | contain `stage_results` and `patch_preview` |
| Step results | `stage_results_directory` set | `0.png`, `1.png` exist when `job_completed` is sent, 8-bit, the source's size; `image_path` in each message |
| | The folder cannot be written | the job completes; `image_path` is empty |
| | A cancelled job | the folder holds no partial file |
| Patch (`test_patch_preview.py`, new) | One Noise step on a 384 × 384 region | `patch_started`, `patch_stage` running, `patch_stage` done with a 384 × 384 PNG, `patch_completed` |
| | Noise then 4× | the second PNG is 1536 × 1536 |
| | The same request again | both stages `cached: true`; the model is not loaded (spy) |
| | Noise, then Noise and JPEG in the order JPEG, Noise | nothing cached for the second request; then Noise alone again: cached |
| | Region at the picture's corner | no error; the picture has the region's size |
| | The patch equals the job | for a tile-sized picture, the centre of the patch equals the same pixels of a full job's step within 1/255 (padding works) |
| | Colour | the model runs once for two regions of one file |
| | `cancel_request` during a run | `patch_cancelled`; the next request runs |
| | A `job_request` queued behind a patch after its cancel | the job starts |
| | Two `patch_request`s for slot `a` queued | only the second runs |
| | Patch on a video | the first frame is the source; a 384 × 384 PNG |

### Host (`cargo test`)

| Group | Test | Expected |
| --- | --- | --- |
| Steps | Dispatch of a photo job with two fixes and an upscale, engine with `stage_results` | the request has `stage_picture_max_dimension: 2048`; `job_steps` has three entries, all `waiting` |
| | Engine without the feature | no field; `job_steps` is still filled |
| | `stage_started` index 1 | `job_steps[1].state` is `running`, `[0]` is `done`; title `Step 2 of 3 · Noise` |
| | `stage_result` with a JPEG | `preview_url` is an asset URL of a file in the preview folder; the emitted `worker-message` has no `jpeg_base64` |
| | `stage_result` skipped | state `skipped`, the note kept |
| | Step names | one case per name of `planLine.ts`'s table, read from a shared fixture |
| Step results | A lone photo job | `stage_results_directory` under `work_root/steps/<job_id>` |
| | A batch; a trial run; a second job queued | no directory |
| | `resolve_pair_target` | `"job#1"` gives the step file of `last_steps`; an unknown index or another job is an error; `"original"` and a job id as today |
| | The next dispatch; `clear_results`; `remove_media`; shutdown | the folder is gone and `last_steps` is empty |
| | Start-up with a leftover folder | emptied |
| Patch | `request_patch_preview` while a job runs or waits | state `busy`; nothing is sent |
| | A valid request | one `patch_request` whose stages equal those of the job request for the same input |
| | `stage_limit` 1 of 3 | one stage sent; the others `waiting` or `missing` in the state |
| | A second request for the slot | a `cancel_request` for the first, then the second |
| | A job is dispatched during a patch | `cancel_request` for the patch first; both slots `busy` |
| | `patch_stage` done | a `.png` in the preview folder; `confined_preview_path` accepts it and still refuses what it refused |
| | Media without a grant | refused |
| Patch default | `off` · `manual` · `live` | refused · `stale` · sent |
| | `auto`, no timings | sent |
| | `auto`, timings 2 s and 2 s for the two steps to run | sent |
| | `auto`, 4 s and 3 s | `stale`, `estimate_seconds` 7 |
| | `auto`, the first step already shown, the second 4 s | sent |
| | `force` | sent in every mode but `off` |
| | Timings | three runs of 2, 4 and 6 s give 4; a cached step changes nothing; job estimates are unchanged |
| | Settings | `preview_patch: "x"` loads `auto`; both fields survive a full save |
| Recipes | A settings file without `recipe_pins`, nine recipes | the pins are the first seven ids |
| | With `recipe_pins: []` | stays empty |
| | `menu_layout` with pins `[b, workflow.old-scan, a]` | ⌘3 `b`, ⌘4 `Old scan`, ⌘5 `a`; an unpinned recipe has no item; `Browse Recipes…` last |
| | `delete_recipe` | the id leaves both lists |
| | `validate_settings` | 21 pins, a duplicate, a 65-character id: each refused |
| | 100 recipes and a pinned workflow | a 101st recipe is refused; the pin is allowed |

### vitest

| Group | Case | Expected |
| --- | --- | --- |
| jobSteps | steps JPEG, Colour (running), Noise, Upscale at 4× | `Step 2 of 4 · Colour`; marks `done`, `running`, `waiting`, `waiting` |
| | a skipped first step | `Step 1 of 4 · Blur skipped` |
| | one step | no label |
| Viewer between steps | a full-image tile of a finished step arrives | the source `<img>` keeps its `src`; nothing is painted |
| | tiles of step 2 arrive, no step picture yet | the canvas still shows step 1's tiles |
| | step 1's picture decodes | the base is its URL and the canvas is cleared, in one update |
| | no step picture for 2 s (fake timers) | cleared at the next tile; the base is the source |
| | a step change | zoom and pan as before |
| | `M` held during a job | the original shows; the label hides |
| Step names | a four-step job running step 2 | the viewer label, the bar's title and the plan line's accessible name `Plan: JPEG done, Colour running, Noise, Upscale 4×` |
| | job of another file selected | no label, no marks |
| Step chips | `last_steps` for the shown result | group `Result after each step` with `JPEG`, `Colour`, `Noise`, `4×`; `4×` pressed |
| | click `Noise` | `request_image_comparison` with `showing: "<job>#2"`; the side label `After Noise` |
| | a skipped step | disabled, the note as `title` |
| | a video result; a batch | no group |
| previewPatch | `pickPatchRegion` on a picture with one textured cell off-centre and a flat rest | that cell; on a flat picture the centre |
| | `snapRegion` | multiples of 16, inside the picture; a 300 × 200 picture gives a 200 × 200 region |
| | `movePatch` with Shift | 128 px, clamped |
| | `patchChips` for a plan with a missing second model | `Original`, `JPEG`, `↓ Noise` (missing), `4×` (waiting) |
| | `shownStep` | the newest done step; after a pick, the pick; after the pick leaves the plan, the newest again |
| Patch | 1. button | shows for a photo with a plan; hidden in Edit, for SeedVR2, with the setting `off`, without the feature |
| | 2. job running | disabled with its `title`; the patch is not rendered |
| | 3. turn on at 40 % zoom | zoom is 100 %, the view is `Original`, `set_preference('preview_patch_shown', true)` |
| | 4. state `running`, step 2 | chip 2 has the dot; the state text `Noise · 3 s` |
| | 5. state `ready` | the newest chip pressed; its picture placed with the region's `cropStyle` |
| | 6. state `stale` with 12 s | dashed frame; `Update preview · about 12 s`; a click sends `force: true` |
| | 7. missing model | `Download 30 MB and preview` calls the download, then asks again when it is installed |
| | 8. failed | `Preview failed.`, `Try again` |
| | 9. drag the tab, arrow keys | the original shows while dragging; one request 250 ms after release; arrows move 32 px |
| | 10. tick a fix while on | one request after 400 ms; three changes in 400 ms give one |
| | 11. `M` held | the patch shows the original |
| | 12. `Split` chosen | the patch hides; back to `Original` shows it |
| | 13. popover link | `See it on this photo` turns the patch on and presses that fix's chip; with the model missing the link is `See an example` and opens the library |
| | 14. another file selected | its own region; the request carries the new file |
| | 15. Settings row | the select saves `set_preference('preview_patch', …)`; `Off` removes the button and the link |
| | 16. the plan becomes empty | the button is disabled; the patch hides |
| Patch, two models | `Try on this photo` | the library closes; a request for slot `b` with `replace`; the second row shows both names |
| | `X` | flips the pressed side; not while typing in a field |
| | `Use B` | calls `useForJob` with B's model; the second row goes |
| Patch, video | frame-by-frame video with De-flicker and 2× frames | `This frame`; chips `Original`, `Noise`, `2×` only |
| Examples | the manifest | every file exists; the sum is at most 1,000,000 bytes; every `model_id` is in the catalog; `noise`, `jpeg`, `blur`, `colour` each have one |
| | RealPLKSR Denoise selected | the picture, `Example · Noise`; `Before` swaps the file |
| | NAFNet SIDD selected | `Example of the Noise fix, made with RealPLKSR Denoise` |
| | SwinIR selected | no picture |
| | a model with five facts | the fifth spans both columns (computed style) |
| recipeList | six recipes, two pinned | all six, pinned first, no separator |
| | nine recipes, two pinned, recent `[d, a, e, f, g]` with `a` pinned | pinned `a`, `b`; separator; `d`, `e`, `f`, `g` |
| | a video recipe among the recent, photo open | left out |
| | eight pins | six rows, all pinned |
| | none of the user's own, photo open | the four workflows |
| | `recipePlanLine` | `JPEG → Noise → Upscale 4×`; a restore recipe `Noise`; a video recipe `Noise → Upscale 2× → 2× frames`; same size without fixes `Same size` |
| | `searchRecipes('jpeg slides')` | recipes with both words in name, plan line or model names |
| | `duplicateName` | `Old slides copy`, then `Old slides copy 2` |
| Recipes menu | open with three recipes | names with plan lines; no button named `Delete …`; `Browse recipes…` opens the library |
| | at 100 recipes | `Save current as recipe…` disabled with its `title` |
| Update | apply `Phone photos`, change Size | `Update “Phone photos”`; a click calls `saveRecipe` with the same id and name and the new scale |
| | apply a workflow, change Size | no `Update` |
| Recipes library | open | search has focus; `My recipes` selected; pinned rows first |
| | type `slide` | only matching rows; clearing restores them; no match shows the sentence |
| | ↓, ↓, Enter | the second row is applied; the dialog closes; `recipe_recent` starts with its id |
| | a video recipe, photo open | row tag `Video`; `Apply` disabled with its `title` |
| | detail | steps with model names; `290 MB to download` with FBCNN missing |
| | `Pin` | `recipe_pins` gains the id; the row gets the mark and moves up |
| | `Rename` | field, Enter saves under the same id |
| | `Duplicate` | `saveRecipe` with a new id and `… copy` |
| | `Delete` | the question; `Delete` calls `deleteRecipe` |
| workflows | the table of section 6 | for each: plan line, model ids and download bytes from the bundled catalog with empty pins |
| | a remembered Noise model | Low-light uses it |
| | `workflowSettings` | never contains `output_format`, `tile_size`, `halo`, `precision`, `device` |
| | `workflowMatches` after applying | true; after changing Size false |
| Workflows in the library | `Recommended` | four rows; the card shows `for`, `note`, the plan, the download, the example; `Use workflow` saves the settings and closes |
| | `Duplicate to My recipes` | `saveRecipe` named like the workflow |
| Empty screen | no file | a button `or start from a workflow…` outside the well; a click opens the library on `Recommended` |
| Prompt examples | family in the list | `Examples` shows; five entries |
| | family not in the list; the list empty | no link |
| | choose `Remove something` | the field holds the text without brackets; `the person on the left` is selected |
| | own text in the field | the `Replaces your text.` line shows; after choosing, undo restores the own text |

### e2e (Playwright)

| File | Case | Expected |
| --- | --- | --- |
| `steps.e2e.ts` | a four-step run from the fake host with step pictures | at no moment is the shown picture smaller than 1000 px wide; the label reads each step in turn; after the run the chips show and `Noise` changes the result side |
| | the same without step pictures (older engine) | the source is the base between steps; no error |
| `preview-patch.e2e.ts` | turn on, tick Noise, wait | the frame lies over the photo at the region's place (± 1 px); the tab does not leave the canvas at either window size; with the frame at the top edge the tab is below it |
| | keyboard only | Tab reaches the button, the tab and each chip; arrows move the patch |
| | start a job | the patch is gone and returns after the job |
| `recipes-library.e2e.ts` | 30 recipes | the menu has six rows; the library scrolls its list only; search narrows it; the dialog fits 1024 × 700 |
| | the empty-screen link | opens `Recommended`; `Use workflow` fills the panel: Fix ticks, Size and Quality as the table says |
| `library.e2e.ts` | a model with an example at both sizes | the picture is 312 × 195 and nothing in the detail pane is cut |
| `screens.e2e.ts` | new shots | the patch, the step label, the Recipes library, the workflow card, the prompt examples |

## Later

Not built now and not specified here: tags, import and export of recipes, folders; more
workflows (old video, screenshots and text) and suggestions from the picture itself; an Edit
step inside a workflow; a patch on any video frame and a short clip preview for De-flicker and
added frames; comparing a step with the step before it instead of the original; examples for
every model, downloaded with the catalog; assigning ⌘3 to ⌘9 by hand.

## What may not work as drawn

- **The 5 seconds.** A first run loads the model from disk; FBCNN is 288 MB and DDColor 912 MB.
  On a slow Mac the first preview of such a fix can take longer than a job's tile. The rule
  lets a never-measured model run once, so the first preview after a tick can be slow; it can
  be cancelled by any change. P3 measures this on the slowest Mac we support.
- **The patch is not the job, exactly.** Colour's guess comes from the original picture
  reduced, while the job colours the picture after the earlier fixes. A model that looks far
  around a pixel can differ near the patch's edge despite the 32 px padding. The test compares
  the centre.
- **Memory.** A patch loads models that Start would load anyway, one after another, and
  releases each (`_run_fix_model_stage`, `image_job.py:380-383`). With `Safe memory` on it runs
  the same way. It does not run beside a job.
- **100 % zoom on turning on** moves the view. It is the honest size for judging grain, and
  the alternative, a patch 70 px wide on a fitted 24-megapixel photo, shows nothing.
- **`execCommand('insertText')`** is deprecated but is what keeps the WebView's own undo on
  macOS (WKWebView), Windows (WebView2) and Linux (WebKitGTK). The fallback covers a WebView
  that drops it.
- **WebP** needs Safari 14; the app supports macOS 12 (Safari 15). AVIF would be smaller and
  needs Safari 16.
- **Step files** take a second or two of PNG writing per step for a large photo. It happens
  beside the next step, and the job's end waits for it.
- **`Best` for Old scan** resolves to a Labs model. That is what Best is for every user today;
  W0 confirms that it suits scans, or the workflow goes to Quick.
- **The tab** is wider than the frame when the plan has five steps and a second row. It is
  kept inside the canvas and may then not be centred on the frame.

## Outside this change

- The share of the progress bar per step (`341f42ad`).
- `Try others…` and the Compare view.
- The result history and the outputs list.
- Batches with different plans per file.
- The site and the release notes.

## Open questions

The spec builds the first answer of each.

1. **Low-light phone photo: Quick or Best?** Quick is RealPLKSR Denoise, 30 MB. Best is NAFNet
   SIDD Width64, 464 MB, trained on phone-camera noise. Built: Quick, and W0 compares the two.
2. **Old scan: Best or Quick?** Best resolves to RealPLKSR ×4 NomosWebPhoto (Labs, 30 MB),
   Quick to LocalSR Photo S (2 MB). Built: Best, pending W0.
3. **`Off` in Settings also hides `Try on this photo`?** Built: yes, off means no patch
   anywhere.
4. **Step files for a 16-bit job are 8-bit pictures.** Built: yes; they are for looking at.
   The alternative doubles the disk use.
5. **`Browse Recipes…` on ⇧⌘R.** ⌘R is Start. Built: ⇧⌘R; say if it should have none.
