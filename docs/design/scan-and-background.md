# The Scan and Backgr. rows

Build spec for LocalSR 0.7.0, on `rc/0.7.0`. It fills the two rows that
`docs/design/enhance-panel.md` reserved (lines 77, 79, 1040, 1043) and follows that spec's
layout, copy rules and components.
Line numbers refer to `bfccd2db`; every reference also names its function or its text. Paths
are relative to `desktop/` unless they start with `docs/`, `src/localsr/` or `tests/`.

Two rows, independent of each other:

1. **`Backgr.`** on a photo: `Keep` / `Remove`. Ready to build: the engine step exists with
   its tests (`tests/test_background_removal.py`); it was not run again for this spec.
2. **`Scan`** on a video: one native select. Its main choice, double rate, does not work in the
   engine yet (measured, see "Engine first"). The row ships only with that fix.

Most of the plumbing exists and is not rebuilt: `step_stages` in the settings and the start
request (`src/lib/types.ts:314-320`, `:353`), the plan row of a step in both planners
(`src/lib/plan.ts:319-340`, `src-tauri/src/job_plan.rs:300-336`, `:438-443`), download on Start
(`pendingDownloads`, `plan.ts:526-545`), Start waiting for a step's model (`stepStagesReady`,
`plan.ts:218`; `App.svelte:1008`), a step running alone at Same size (`hasStepWork`,
`App.svelte:987-997`), and PNG in place of JPEG (`output_format_keeping_alpha`,
`job_plan.rs:283-298`; `outputFormatKeepingAlpha`, `plan.ts:229-238`). No UI writes
`step_stages` today; these two rows are the first.

## Decisions

| Point | Decision | Reason |
| --- | --- | --- |
| Backgr. control | Two segments, `Keep` / `Remove`. No `Colour ▾` in 0.7.0. | The engine has no fill. A fill needs a colour picker, changes the format rule (a filled picture may be a JPEG again) and has to be shown in the patch and the step pictures. The viewer already shows a cut-out on checker, black, grey or white (`viewer_background`, `types.ts:340`). |
| Backgr. place | Last row of Result, under the model line. It moves up one row at Same size, as Frames does on a video today (`EnhancePanel.svelte:673-734`). | The board. The model line is conditional and sits above both. |
| Backgr. model | BiRefNet (`birefnet_general`, 444,473,596 bytes, `src-tauri/resources/model-catalog.json`), chosen by the row, no `Change…`. | One model serves the step (`model_task: "background"`, `src/localsr/core/steps.py:372`). Built: the library keeps it out of every choosing group and count and lists it under `Installed` once it is on disk, where its licence and source can be read and it can be removed (`isStepModel`; fast-edit-and-large-marks.md, Part 2, "The library"). |
| Backgr. download | Follows the fixes: nothing downloads on the click; the cost line and Start say `444 MB`. The size is also in the `Remove` segment's `title`, as Quality does it (`EnhancePanel.svelte:667`). | A segment has no room for a value; the foot is always in view. |
| Transparent output | The Output line names the saved format and adds `· transparent`. JPEG and HEIC are saved as PNG; the reason is in the line's `title`, the Backgr. tip and the Format tip. In the Output sheet the Format select shows the saved format and greys the two that cannot keep transparency. | Explanations live behind the `?`. Today's line appends a whole sentence (`ALPHA_FORMAT_NOTE`, `lib/enhancePanel.ts:120`), which is cut off at 340 px. |
| Scan control | One native select with six options: `Automatic`, `Double rate`, and four under `Field order by hand`. | As Frames: one select that writes one setting (`EnhancePanel.svelte:700-733`). |
| Scan `Automatic` | No step stage: today's behaviour, flagged frames deinterlaced at the file's rate (`src/localsr/core/video_normalization.py:16-41`). | Nothing changes for anyone who never opens the select. |
| Scan off | Not offered. | The engine cannot switch the decoder's deinterlacing off. |
| When Scan shows | Frame-by-frame video whose file says interlaced (`tff`, `bff`) or does not say (`unknown`), or any stored Scan choice. Not on a file that says `progressive`. | `unknown` is what old AVI, WMV and raw DV report; H.264, HEVC, VP9, AV1 and ProRes report `progressive` (probed on 2026-11-28, table under "Engine first"). A row on every phone video would be noise. |
| No `Tape` | The trigger is the file only. | `content` is `photo` or `illustration` (`types.ts:401`). |
| Recipes, workflows | Neither row is saved in a recipe, and no workflow sets one. `in_recipe_stages` stays false for both steps. Applying a recipe or workflow leaves both rows as they are. | Scan describes the file, like Output and hardware (previews-and-recipes.md, "Workflows and output"). Backgr. belongs in a recipe, but that needs a new field in the recipes file that older builds must be able to read; see "Later". Recipes do not write `step_stages` today (`recipeSettingsPatch`, `lib/recipes.ts:13-92`) and do not compare it (`recipeMatches`, `:137-191`). |
| Names in the plan | `Background`; `Deinterlace`, or `Deinterlace to 50 fps` at double rate. | The plan line names what is removed (`Blur`, `Noise`) or the result (`Upscale to 1080 px`, `lib/planLine.ts:39-42`). |
| Fixtures | The e2e fixtures' `FEATURES` (`e2e/fixtures.ts:3`) stays as it is; two new scenarios list the new features. | No existing screenshot changes, and a changed one means a row leaked into a state it does not belong to. |

## Engine first (Scan only)

Measured on 2026-11-28 with this branch's engine (`WorkerServer._run_video_job`, stage
`{"step": "deinterlace", "params": {"mode": "field"}}`, as `tests/test_video_deinterlace.py:124-156`
does) on clips made with ffmpeg from `testsrc2=size=720x576:rate=50`, interlaced with
`tinterlace=interleave_top` (H.264: `-flags +ilme+ildct -x264opts tff=1`; PAL DV: `fieldorder=bff -c:v dvvideo`).

**E1. Double rate adds no motion on a flagged file.** The 50 fps output of a flagged 25i H.264
clip has mean absolute differences between consecutive frames of 0.9, 4.7, 1.0, 4.6, 0.9, 5.1:
pairs of the same moment. `ffmpeg -vf bwdif=mode=send_field` on the same clip gives 3.0 to 3.7
at every step. DV in AVI gives the same pairs. Cause: decoding already deinterlaces flagged
frames to one frame each (`decode_timed_frames`, `src/localsr/core/video_io.py:416`, through
`display_frames`, `video_normalization.py:34-41`), so the step's own bwdif
(`_build_deinterlace_stage`, `src/localsr/core/video_chain.py:374-378`) splits a frame that no
longer holds two moments. The existing tests feed the step raw arrays or a progressive clip
(`test_video_deinterlace.py:13-33`, `:79-121`, `:124-156`), so they pass.

**E2. Field order with parity `auto`.** The step takes the order from the probe
(`video_chain.py:322-323`), and the probe maps the stream value 4 to `bff` and 5 to `tff`
(`_video_probe`, `video_io.py:195-197`). Two ffmpeg-written files disagree with their own
frames: H.264 top-field-first in MKV reads stream `tb` (4, so `bff`) while every frame is
flagged top first; PAL DV in MOV reads `bt` (5, so `tff`) while every frame is flagged bottom
first (`ffprobe -show_entries stream=field_order` against `frame=interlaced_frame,top_field_first`).
DV in AVI and raw DV read `unknown` with every frame flagged bottom first; the frames the step
builds carry no flag (`video_chain.py:367-369`), so bwdif assumes top first. E1 hides this
today; once fields are real, a wrong order shows as motion that jumps back and forth.

What the probe reports, same clips (PyAV 18.1.1 of the worktree's `.venv`):

| File | Stream field order | Frames flagged interlaced |
| --- | --- | --- |
| H.264 25i in MP4, TS | `tt` | yes, top first |
| H.264 25i in MKV | `tb` | yes, top first |
| PAL DV in MOV | `bt` | yes, bottom first |
| PAL DV in AVI, raw `.dv` | unknown | yes, bottom first |
| MJPEG, MPEG-4 in AVI, WMV, GIF | unknown | no |
| H.264, HEVC, VP9, AV1, ProRes (progressive) | progressive | no |

Needed from the engine before the row ships (task S0; the tests are named under Tests):

1. `mode: field` on a flagged file gives each field its own moment.
2. With parity `auto`, a flagged frame is split in its own flagged order; the stream's value
   decides only when no frame is flagged.
3. `VideoProbe.field_order` says `tff` or `bff` when the first decoded frame is flagged, even
   if the stream says `unknown`.

One way that does all three: when a job has a deinterlace stage, `display_frames` builds its
bwdif from that stage on the decoder's own frames, where the flags still are (`parity=auto`
then follows each frame; `deint=interlaced` for `auto`, `deint=all` for a hand-set order or an
unflagged stream), and the chain stage passes frames through and only retimes.

Unchanged engine rules the row relies on: parity `auto` on a file that says `progressive`
passes through with the note `Already progressive · kept as it is`
(`src/localsr/core/video_pipeline.py:360-366`); a hand-set order always runs
(`video_chain.py:314`); double rate doubles the rate and the frame count before interpolation
is planned (`video_pipeline.py:367-370`, `:381-382`).

## Layout

Notation as in enhance-panel.md: 340 px, 16 px padding, label column 76 px with the `?`.

### Photo, background removed, BiRefNet not downloaded

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
│ [ ] JPEG artifacts                                   │
│ [✓] Noise                        RealPLKSR Denoise ▾ │
│ [ ] Colour                                           │
├──────────────────────────────────────────────────────┤
│ RESULT                                   6400 × 4268 │
│ Size ?     [ Same ][ 2× ][ 3× ][● 4× ]               │
│ Quality ?  [● Quick           ][ Best              ] │
│            LocalSR Photo S                   Models… │
│ Backgr. ?  [ Keep             ][● Remove           ] │
╞══════════════════════════════════════════════════════╡
│ Background → Noise → Upscale 4×                    ▸ │
│ Output     PNG · transparent · Desktop             ▸ │
│                                 444 MB to download ? │
└──────────────────────────────────────────────────────┘
 status bar, unchanged:    [ Download 444 MB, then upscale ]
```

At Same size with nothing ticked the cut-out is the whole job:

```
│ RESULT                                   1600 × 1067 │
│ Size ?     [● Same ][ 2× ][ 3× ][ 4× ]               │
│ Quality ?  [● Quick           ][ Best              ] │
│ Backgr. ?  [ Keep             ][● Remove           ] │
╞══════════════════════════════════════════════════════╡
│ Background                                         ▸ │
│ Output     PNG · transparent · Desktop             ▸ │
```

### Video, interlaced file, double rate

```
┌──────────────────────────────────────────────────────┐
│ Enhance                                    Recipes ▾ │
│ Task ?     [● Upscale & restore ][░░░░░ Edit ░░░░░░] │
├──────────────────────────────────────────────────────┤
│ YOUR VIDEO                  768 × 576 · 25 fps · 42s │
│ Content ?  [● Video           ][ Animation         ] │
│ Scan ?     [ Double rate · 50 fps                ▾ ] │
├──────────────────────────────────────────────────────┤
│ Fix ?                                                │
│ [ ] Blur                                             │
│ [ ] Compression                                      │
│ [✓] Noise                        RealPLKSR Denoise ▾ │
│ [ ] Colour                                           │
│ [ ] De-flicker                                  Labs │
├──────────────────────────────────────────────────────┤
│ RESULT                          1536 × 1152 · 50 fps │
│ Size ?     [ Same ][● 2× ][ 3× ][ 4× ]               │
│ Quality ?  [● Quick           ][ Best              ] │
│            LocalSR Photo S                   Models… │
│ Frames ?   [ Original                            ▾ ] │
╞══════════════════════════════════════════════════════╡
│ Deinterlace to 50 fps → Noise → Upscale 2×         ▸ │
│ Output     MP4 · AV1 · CRF 18 · Desktop            ▸ │
└──────────────────────────────────────────────────────┘
 status bar, unchanged:              [ Start selected video ]
```

The select's list, on the same file before anything is chosen:

```
│ Scan ?     [ Automatic · interlaced              ▾ ] │
│            ┌─────────────────────────────────────┐   │
│            │ ✓ Automatic · interlaced            │   │
│            │   Double rate · 50 fps              │   │
│            │ Field order by hand                 │   │
│            │   Top field first · 25 fps          │   │
│            │   Top field first · 50 fps          │   │
│            │   Bottom field first · 25 fps       │   │
│            │   Bottom field first · 50 fps       │   │
│            └─────────────────────────────────────┘   │
```

## When each row shows

`features` is `snapshot.engine?.features`; "stored" means the step is in `settings.step_stages`.

| | `Backgr.` | `Scan` |
| --- | --- | --- |
| Shows when | the task is Upscale & restore on a photo (`task` is `upscale` or `denoise`), `features` has `image_background_removal` (`steps.py:370`) and the catalog has a model whose `purposes` include `background` | the task is `video` with a frame-by-frame model (`!usingTemporalVideo`), `features` has `video_deinterlace` (`steps.py:352`) and the selected file's `field_order` is `tff`, `bff` or `unknown` |
| Also shows when | `Remove` is stored, whatever the engine lists | a Scan choice is stored, whatever the file and the engine say |
| Never | Edit; a video; no task chosen | Edit; a photo; SeedVR2 or FlashVSR chosen by hand (they take no steps, `stepStageMedia`, `plan.ts:179-183`) |
| A file probed by an older build (`field_order` empty) | - | counts as `progressive`: no row until it is added again |
| Engine lacks the feature, choice stored | `Remove` stays pressed, `Keep` can be pressed; group `title` `This engine cannot remove backgrounds. Update LocalSR.` | every option but `Automatic` is disabled; select `title` `This engine cannot deinterlace. Update LocalSR.` |
| Locked (`settingsLocked`) | disabled with the body's `fieldset` (`EnhancePanel.svelte:428-432`); the pressed segment and the chosen option stay readable | the same |
| Several files | one choice for all photos of the start; a video in the same queue ignores it (`active_step_stages`, `job_plan.rs:240-258`) | the row follows the selected file; `Upscale all N` sends the one choice with every video. `Automatic` and `Double rate` act only on interlaced files, a hand-set order on every file |

The stored-choice rules keep a running choice visible: the host refuses a start whose step the
engine does not list (`validate_step_stages_engine`, `src-tauri/src/commands.rs:4705-4735`),
and a hand-set field order deinterlaces a progressive file too.

## Copy

| Where | String |
| --- | --- |
| Backgr. label | `Backgr.`, `title` `Background`; the group's accessible name is `Background`; tip label `Background` |
| Backgr. segments | `Keep`, `Remove` (radios) |
| Segment `title` | Keep `Leaves the background as it is` · Remove `Makes the background transparent`, and ` · 444 MB download` while the model is missing (`formatBytes(model.size_bytes)`) |
| Backgr. tip | `Remove makes everything behind the main subject transparent. The subject is found on the original picture, before the fixes and the upscale, and its outline is enlarged with the picture. The outline is worked out at 1024 × 1024 pixels, so single hairs and other very fine edges are not followed exactly. The result needs transparency: PNG, TIFF and WebP keep it, and a JPEG or HEIC choice is saved as PNG. BiRefNet does the work.` and, while the model is missing, ` The first use downloads 444 MB.` |
| Output line, background removed | `PNG · transparent · Desktop` · `TIFF · transparent · Desktop` · `WebP 98 · transparent · Desktop` |
| Output line `title`, format replaced | the line's text, then ` · Saved as PNG to keep the transparent background.` (`ALPHA_FORMAT_NOTE`, unchanged) |
| Output sheet, Format options while removed | `JPEG · no transparency`, `HEIC · no transparency`, both disabled |
| Format tip, added while removed | ` While the background is removed, JPEG and HEIC are not offered: they cannot store transparency, so the result is saved as PNG.` |
| Scan label | `Scan` (a `<label for="scan">`); tip label `Scan` |
| Scan options | `Automatic` (on a `tff` or `bff` file: `Automatic · interlaced`) · `Double rate · 50 fps` · group `Field order by hand`: `Top field first · 25 fps`, `Top field first · 50 fps`, `Bottom field first · 25 fps`, `Bottom field first · 50 fps`. The rates are the file's rate and twice it, at most three decimals (`59.94 fps`). Without a known rate: `Double rate`, `Top field first`, `Top field first · double rate`, and the same for bottom. |
| `Double rate` on a file that says `progressive` | `Double rate`, and the select's `title` `This file says it is not interlaced, so it is left as it is. To deinterlace it anyway, set the field order by hand.` |
| Scan tip | `Old camcorder, TV and DVD recordings are interlaced: each frame holds two half-pictures taken a moment apart, which shows as fine stripes on anything that moves. Automatic removes the stripes when the file says it is interlaced, and keeps the frame rate. Double rate makes a frame from every half-picture, so motion is as smooth as it was on a TV: 25 fps becomes 50. That is twice the frames to process, so it takes about twice as long. A file that says it is not interlaced is left as it is. Set the field order by hand when stripes remain, or when motion jumps back and forth at the doubled rate: then pick the other order. A hand-set order is applied to every file you start, interlaced or not.` |
| Result head | ` · 50 fps` whenever the final rate differs from the file's (double rate, Frames, or both: double rate with Frames `2×` reads ` · 100 fps`) |
| Frames options | computed from the rate after Scan: with double rate on a 25 fps file `2× · 100 fps`, `4× · 200 fps` |
| Plan line | `Background` · `Deinterlace` (hand-set order at the file's rate) · `Deinterlace to 50 fps` (without a known rate: `Deinterlace 2×`) |
| Plan sheet rows | stage `Remove background`, name `BiRefNet` · stage `Deinterlace`, note line the chosen option's text (`Double rate · 50 fps`). No arrows, no `Change…`. |
| Step label while running | `Step 1 of 3 · Background` · `Step 1 of 3 · Deinterlace` |
| Step chip after a job | `Background`, `title` `The picture with the background removed` |
| Preview patch chip | `Background`, disabled and struck through; `title` is the engine's `This step changes transparency, which the preview patch cannot show.` (`src/localsr/worker/patch_preview.py:49`, `:509-511`). Scan has no chip. |

## Behaviour

New `src/lib/stepRows.ts`, pure, holds everything both rows decide:

```ts
export type ScanChoice = 'auto' | `${'frame' | 'field'}:${'auto' | 'tff' | 'bff'}`;

/** `stages` with the entry of `step` replaced by `entry`, appended, or removed (`null`). */
export function withStepStage(stages: readonly StepStageSelection[] | undefined, step: string,
  entry: StepStageSelection | null): StepStageSelection[];

export function backgroundModel(models: readonly CatalogModel[]): CatalogModel | undefined;
export function backgroundRemoved(settings: Pick<UiSettings, 'step_stages'>): boolean;
export function backgroundRowShown(o: { task: UiSettings['task']; features: readonly string[];
  model: CatalogModel | undefined; removed: boolean }): boolean;

export function scanChoice(settings: Pick<UiSettings, 'step_stages'>): ScanChoice;
export function scanStage(choice: ScanChoice): StepStageSelection | null;
/** False only for parity auto on a file that says progressive (video_pipeline.py:360-362). */
export function scanApplies(choice: ScanChoice, fieldOrder: string | undefined): boolean;
/** The rate after Scan: twice `fps` for a `field` choice that applies, else `fps`. */
export function scanRate(choice: ScanChoice, fieldOrder: string | undefined, fps: number): number;
export function scanRowShown(o: { task: UiSettings['task']; usingTemporalVideo: boolean;
  features: readonly string[]; fieldOrder: string | undefined; choice: ScanChoice }): boolean;
export function scanOptions(fieldOrder: string | undefined, fps: number, choice: ScanChoice):
  { value: ScanChoice; label: string; group: '' | 'hand' }[];
```

### Backgr.

- Markup: a `.panel-row` after the Frames block and before `{#if usingTemporalVideo}`
  (`EnhancePanel.svelte:734-735`): `.panel-key` with the label and `<HelpTip label="Background">`,
  then `<div class="seg-inline" role="radiogroup" aria-label="Background">` with two
  `role="radio"` buttons, `aria-checked`, `class:active`, `on:keydown={radioGroupKeydown}`, as
  Content has them (`:531-565`). No new CSS.
- `backgroundModel`: the first catalog model whose `purposes` include the step's `model_task`
  (`servesStep`, `plan.ts:203-205`).
- `Remove` calls `updateSettings({ step_stages: withStepStage(settings.step_stages,
  'remove_background', { step: 'remove_background', model_id: model.model_id }) })`; `Keep`
  passes `null`. Other entries keep their place. Default is `Keep`: no entry.
- Nothing else is wired by hand. The plan row, the cost line, `Download 444 MB, then upscale`,
  Start waiting for the model and the job at Same size come from the plan as they do for a fix.
- The step runs before the fixes (phase `cleanup`, `steps.py:364-365`; `stepStage`,
  `lib/steps.ts:128-130`), so `Background` is the first name on the plan line.
- Output line: `outputSummary` (`lib/enhancePanel.ts:92-122`) puts `transparent` after the
  format whenever an active step sets alpha, and no longer appends the sentence. New
  `outputTitle(settings, options)` returns the summary plus the sentence when the format was
  replaced; the foot's trigger takes it as `triggerTitle` (`EnhancePanel.svelte:1042`).
- Output sheet (`OutputSheet.svelte:99-117`): with an alpha step active, the Format select's
  `value` is the saved format, the options that cannot keep alpha are `disabled` with the
  suffix, the quality slider follows the saved format (`qualityName`, `:54`), and the tip gets
  the added sentence. `settings.output_format` is not changed, so `Keep` brings JPEG back.
- File name: a job that is the cut-out alone (task `denoise`, empty `model_id`, no
  `fix_stages`, a step with `output_alpha`) ends in `_cutout` in place of `_denoised`
  (`output_ending`, `commands.rs:5868-5890`).

### Scan

- Markup: a `.panel-row` after the Content row, inside the same `{#if !usingTemporalVideo}`
  (`EnhancePanel.svelte:517-566`), when `scanRowShown`: `<label class="panel-label"
  for="scan">Scan</label>`, `<HelpTip label="Scan">`, `<select id="scan">` with the options of
  `scanOptions`, the `hand` ones in an `<optgroup label="Field order by hand">`. No new CSS.
  It stays enabled at Same size, where Content is disabled.
- Value and writing: the select's value is `scanChoice(settings)`. A change calls
  `updateSettings({ step_stages: withStepStage(settings.step_stages, 'deinterlace',
  scanStage(choice)) })`. `scanStage('auto')` is `null`; every other choice is
  `{ step: 'deinterlace', params: { mode, parity } }` with both parameters written
  (`steps.py:355-358`).
- `scanChoice` reads the stored entry; none is `auto`. A stored `frame:auto` (the UI never
  writes it) is listed as a seventh option `Deinterlace · 25 fps` while it is the value, as
  `withCurrent` does for formats (`lib/outputFormats.ts:24-27`).
- `field_order` comes from the new field on the media item (below).
- Rate: `baseFps = scanRate(choice, selectedMedia?.field_order, fps)`. `frameLabel`, `outputFps`
  and the Result head use `baseFps` where they use `fps` today (`EnhancePanel.svelte:332-345`,
  `:590-597`); the head shows the final rate whenever it differs from `fps`. The Plan sheet's
  interpolation note does the same (`PlanSheet.svelte:106-110`).
- The step is the plan's first row (phase `geometry`, `steps.py:346`); Clean audio still goes
  in front of it (`job_plan.rs:661-664`).
- Preview patch: the patch shows one decoded frame, so Scan is not a step of it.
  `'deinterlace'` joins `VIDEO_ONLY_KINDS` (`lib/previewPatch.ts:135-141`), and `patch_plan`
  drops step stages whose step does not run on `image`, beside the lines that switch
  De-flicker, added frames and audio off (`commands.rs:5763-5766`).

### The media item's field order

Additive, like `has_audio`: `field_order?: '' | 'progressive' | 'tff' | 'bff' | 'unknown'`.

| Layer | Place |
| --- | --- |
| Engine | `MediaInfo` gains `field_order: str = ""` (`src/localsr/protocol/messages.py:385-397`); the probe message sends `probe.field_order` (`src/localsr/worker/server.py:832-846`); photos send nothing |
| Host | `worker.rs:2063-2077` reads `string(data, "field_order")` into `update_media_probe` (`database.rs:442`); column `field_order TEXT NOT NULL DEFAULT ''` in the loop at `database.rs:145`; `MEDIA_COLUMNS` (`:16`) and the row reader (`:1262`); `MediaItem.field_order` with `#[serde(default)]` (`types.rs:134-154`) |
| UI | `MediaItem.field_order` (`lib/types.ts:170-189`); fake host scenario field `videoFieldOrder` (`e2e/fake-host.ts:19`, `:586`), which also makes the fake video 768 × 576 at 25 fps |

### Names

`lib/planLine.ts` (`stageStepName`, `:25-59`): `remove_background` is `Background`;
`deinterlace` is named from `scanChoice(settings)` and the media's `fps`. `planLine`'s picks
gain `step_stages` and `fps`. `lib/jobSteps.ts` (`stepName`, `:25-61`) returns the same two
names for a running or finished step, and `resultStepChips` (`:153-159`) uses the Copy table's
`title` for `remove_background`. `PlanStage['kind']` (`plan.ts:141-151`) gains the two
literals; the cast at `plan.ts:320` stays.

## Components and files

New: `src/lib/stepRows.ts`, `src/lib/stepRows.test.ts`.

| File | Change |
| --- | --- |
| `src/localsr/core/steps.py:353`, `:371` | `dev_only=True` goes from `deinterlace` (task S2) and `remove_background` (task B1) |
| `src-tauri/resources/steps.json` | regenerated: `scripts/export_desktop_steps.py`, then `--check` |
| `src/localsr/core/video_normalization.py`, `video_io.py`, `video_chain.py`, `video_pipeline.py` | S0, as "Engine first" asks |
| `src/localsr/protocol/messages.py`, `src/localsr/worker/server.py` | `field_order` on `MediaInfo` |
| `src-tauri/src/worker.rs`, `database.rs`, `types.rs` | `field_order`, as the table above |
| `src-tauri/src/commands.rs` | `patch_plan` (5750) drops non-image step stages; `output_ending` (5868) `_cutout` |
| `src/EnhancePanel.svelte` | the two rows; `baseFps`; `outputTitle` on the Output trigger |
| `src/OutputSheet.svelte` | Format while the background is removed |
| `src/PlanSheet.svelte` | note line of the `deinterlace` row; interpolation note from `baseFps` |
| `src/lib/enhancePanel.ts` | `outputSummary`, `outputTitle` |
| `src/lib/planLine.ts`, `jobSteps.ts`, `previewPatch.ts`, `plan.ts`, `types.ts` | as above |
| `e2e/fake-host.ts`, `e2e/fixtures.ts` | `videoFieldOrder`; scenarios `cutout` and `tape` |
| Docs | `CHANGELOG.md` under 0.7.0; `KNOWN_LIMITATIONS.md:55-57` (interlacing); `docs/architecture/steps.md:28` (only `grain` is dev-only); `docs/design/foundation-c.md:268-275`; `docs/design/enhance-panel.md:77`, `:79`, `:1040`, `:1043` point here; this file moves to `docs/design/enhance-panel-rows.md` |

Not touched: `App.svelte`, `FixList.svelte`, `lib/fixRows.ts`, the planners in
`job_plan.rs` and `resolvePlan`, `lib/recipes.ts`, `lib/workflows.ts`, `styles.css`.

## Tasks

B1 to B4 build Backgr. and can ship alone. S0 to S4 build Scan; S0 is engine work and gates
the rest: without it `deinterlace` stays dev-only and no Scan row is built. Each task ends
with `./local-ci.sh frontend` green, and `rust` or `pytest` where it touches them.

| # | Task | Touches | Done when |
| --- | --- | --- | --- |
| B1 | Ship the step: `dev_only` off for `remove_background`, export, update the gate tests | `steps.py`, `steps.json`, `tests/test_background_removal.py:27-35`, `tests/test_grain_step.py:153-157`, `src-tauri/src/steps.rs:683-685`, `src/lib/steps.test.ts:119-122` | The engine lists `image_background_removal` without `LOCALSR_DEV_STEPS`; the four test files pass. |
| B2 | `stepRows.ts` (background half), the names, the Output line | `lib/stepRows.ts`, `planLine.ts`, `jobSteps.ts`, `plan.ts`, `enhancePanel.ts`, their tests, `lib/stepStages.test.ts:202-227` | vitest cases "stepRows", "planLine", "jobSteps", "output" pass. |
| B3 | The row and the Output sheet | `EnhancePanel.svelte`, `OutputSheet.svelte`, `App.test.ts`, `OutputSheet.test.ts` | App.test block "Backgr." passes. By hand in `npm run tauri dev`: Remove on a portrait, Start downloads BiRefNet, the result shows on the checkerboard and the file is a PNG with transparency; the `Background` step chip shows the cut-out. |
| B4 | `_cutout`, e2e, screenshot, docs | `commands.rs`, `commands/tests.rs`, `e2e/fixtures.ts`, `enhance-panel.e2e.ts`, `screens.e2e.ts`, docs | e2e green on Chromium and WebKit at both sizes; no existing baseline differs. |
| S0 | Engine: write the three failing tests first, then fix E1 and E2 and the probe | the four `core/video_*.py` files, `tests/test_video_deinterlace.py` | The three tests fail on `bfccd2db` and pass after; the rest of the file still passes. |
| S1 | `field_order` from the probe to the UI; the patch leaves Scan out | `messages.py`, `server.py`, `worker.rs`, `database.rs`, `types.rs`, `commands.rs`, `lib/types.ts`, `previewPatch.ts`, `fake-host.ts` | Host and vitest cases "field order" and "patch" pass. |
| S2 | Ship the step: `dev_only` off for `deinterlace`, export, gate tests | `steps.py`, `steps.json`, `tests/test_steps_registry.py:190-205`, `tests/test_video_deinterlace.py:70-76`, the files of B1 | The engine lists `video_deinterlace` without the switch; only `grain` is dev-only. |
| S3 | `stepRows.ts` (scan half), plan names | `lib/stepRows.ts`, `planLine.ts`, `jobSteps.ts`, tests | vitest cases "scan" pass. |
| S4 | The row, the rates, the Plan sheet note, e2e, screenshot, docs | `EnhancePanel.svelte`, `PlanSheet.svelte`, `App.test.ts`, `e2e/*`, docs | App.test block "Scan" and the e2e cases pass. By hand: a real interlaced clip at `Double rate` plays smoothly at twice the rate, with no jumping back. |

## Tests

### pytest

| Test | Expected |
| --- | --- |
| `test_background_removal.py::test_background_step_is_listed_without_the_dev_switch` (replaces `test_background_step_is_developer_only`) | `dev_only` is false; `_engine_features([])` has `image_background_removal` with the variable unset |
| `test_grain_step.py`, the registry assertion at 153-157 | `grain` is the only dev-only step |
| `test_steps_registry.py::test_deinterlace_is_a_video_geometry_step` (renamed, S2) | not dev-only; modes and parities as today |
| `test_video_deinterlace.py::test_feature_is_listed_without_the_dev_switch` (replaces `test_dev_gate`, S2) | `video_deinterlace` listed with the variable unset |
| `::test_field_mode_on_a_flagged_clip_gives_every_field_its_own_moment` (S0) | a flagged 25i clip with steady motion, encoded in the test: the 50 fps output's smallest consecutive-frame difference is at least half its largest (on `bfccd2db`: about 0.2) |
| `::test_auto_parity_follows_the_frames_own_flag` (S0) | a clip whose frames are flagged bottom first while the stream says `unknown`: at double rate a bar moving right never moves left between consecutive frames |
| `::test_probe_reports_flagged_frames_as_interlaced` (S0) | the same clip probes as `bff`, a progressive clip as `progressive` |
| `::test_media_info_carries_the_field_order` (S1) | the probe message of a video has `field_order`; a photo's has `""` |

### Host (`cargo test`)

| Test | Expected |
| --- | --- |
| `steps.rs`, the assertion at 683-685 | after B1 and S2 only `grain` is dev-only; `step_stage("deinterlace")` and `step_stage("remove_background")` are `BeforeFixes` |
| `job_plan.rs::remove_background_is_the_first_row_of_a_photo_plan` | real registry and catalog: kind `remove_background`, role `step`, `model_id` `birefnet_general`, `size_bytes` 444473596 while missing; `output_format` `png` for a `jpg` setting, `tif` kept |
| `job_plan.rs::deinterlace_is_the_first_row_of_a_frame_by_frame_video_plan` | present with fixes and at Same size alone; absent for SeedVR2 and for a photo task |
| parity fixture (`UPDATE_JOB_PLAN_FIXTURE=1`, `lib/jobPlanParity.test.ts`) | new cases: photo with `remove_background`; video with `deinterlace` and interpolation before upscaling; both stored on a photo task |
| `commands/tests.rs::a_cut_out_alone_is_named_cutout` | `_cutout.png`; with a fix `_denoised`, with an upscale `_upscaled_4x` |
| `commands/tests.rs::the_patch_plan_leaves_video_only_steps_out` | a video patch request with `deinterlace` stored has no such stage; a photo's `remove_background` stays |
| `database/tests.rs::media_keep_the_field_order_and_old_databases_gain_the_column` | round trip of `bff`; a database without the column loads with `""` |
| `worker/tests.rs::media_info_stores_the_field_order` | `tff` in the message is `tff` on the media item; a message without it stores `""` |

### vitest

`lib/stepRows.test.ts`

| Case | Expected |
| --- | --- |
| `withStepStage` | replaces in place, appends, removes; other entries untouched; `undefined` in gives `[]` or one entry |
| `backgroundModel`, real catalog | `birefnet_general`; a catalog without a `background` model gives `undefined` |
| `backgroundRowShown` | true for `upscale` and `denoise` with feature and model; false for `video`, `edit`, `''`, no feature, no model; true without the feature when removed |
| `scanChoice` / `scanStage` | every one of the six choices round-trips; no entry is `auto`; `{mode: 'field'}` without `parity` reads `field:auto` |
| `scanApplies` | false only for `auto` parity on `progressive`; true for `field:tff` on `progressive`; `auto` choice is always false |
| `scanRate` | 50 for `field:auto` on `bff` at 25; 25 on `progressive`; 25 for `frame:tff`; 59.94 for `field:bff` at 29.97 |
| `scanRowShown` | true for `tff`, `bff`, `unknown` with the feature; false for `progressive`, `''`, `undefined`, a temporal model, a photo task, no feature; true for `progressive` once a choice is stored |
| `scanOptions` at 25 fps, `bff` | the six labels of the Copy table, the first `Automatic · interlaced` |
| `scanOptions`, `unknown` | first label `Automatic` |
| `scanOptions`, `progressive`, `field:auto` stored | second label `Double rate` |
| `scanOptions`, fps 0 | the labels without rates |
| `scanOptions`, `frame:auto` stored | seven options, the extra one `Deinterlace · 25 fps` |

`lib/planLine.test.ts`: `remove_background` is `Background`; `deinterlace` with `field:auto` at
25 fps is `Deinterlace to 50 fps`, at 0 fps `Deinterlace 2×`, with `frame:tff` `Deinterlace`.
`lib/jobSteps.test.ts`: `stepName` for both kinds; the `Background` chip's `title`.
`lib/previewPatch.test.ts`: a `deinterlace` stage is no entry; a `remove_background` stage is the
entry `Background`. `lib/steps.test.ts:119-122`: the dev-only ids are `['grain']`.
`lib/stepStages.test.ts:202-227` and `lib/enhancePanel.test.ts`: `outputSummary` gives
`PNG · transparent · Desktop` for a `jpg` setting and `TIFF · transparent · Desktop` for `tif`;
`outputTitle` ends with `ALPHA_FORMAT_NOTE` only for the first.

`App.test.ts`

| Block | Case | Expected |
| --- | --- | --- |
| Backgr. | photo, feature listed | radiogroup `Background` with `Keep` checked, after the model line; the `About Background` tip holds the tip text |
| | feature not listed; video selected; Edit | no such group |
| | press `Remove` | `saveSettings` gets `step_stages: [{ step: 'remove_background', model_id: 'birefnet_general' }]`; plan line `Background → Upscale 4×`; Output line `PNG · transparent · Desktop` |
| | model missing | cost line `444 MB to download`; Start reads `Download 444 MB, then upscale`; `Remove`'s `title` ends ` · 444 MB download` |
| | `output_format: 'jpg'`, removed | Output line `PNG · transparent · Desktop`, its `title` ends with the sentence; the sheet's Format shows PNG, `JPEG · no transparency` is disabled, no quality slider |
| | press `Keep` | `step_stages: []`; Output line `JPEG 98 · Desktop` |
| | Same size, nothing ticked, removed | plan line `Background`; no `Nothing to run`; Start enabled |
| | a `grain` stage stored beside it | pressing `Keep` leaves the `grain` entry |
| | job running | both radios disabled |
| Scan | video `field_order: 'bff'`, 25 fps | select `Scan` with value `auto`, text `Automatic · interlaced`, between Content and the Fix head |
| | `progressive`; no `field_order`; feature not listed; SeedVR2 chosen; photo | no select |
| | `unknown` | select, first option `Automatic` |
| | choose `Double rate · 50 fps` | `step_stages: [{ step: 'deinterlace', params: { mode: 'field', parity: 'auto' } }]`; head ends ` · 50 fps`; plan line starts `Deinterlace to 50 fps`; Frames lists `2× · 100 fps` |
| | choose `Bottom field first · 25 fps` | `params: { mode: 'frame', parity: 'bff' }`; no rate in the head; plan line starts `Deinterlace` |
| | back to `Automatic` | `step_stages: []` |
| | `field:auto` stored, file `progressive` | the select shows `Double rate` with the `title` of the Copy table; no rate in the head |
| | Plan sheet with double rate | row `Deinterlace` with the note `Double rate · 50 fps` |
| | Same size, nothing ticked, double rate | no `Nothing to run`; Start enabled |

### e2e (Playwright)

Scenarios in `e2e/fixtures.ts`: `cutout` (`ready` plus `image_background_removal` and
`stage_params_and_masks`) and `tape` (`video` plus `videoFieldOrder: 'bff'`, `video_deinterlace`
and `stage_params_and_masks`).

`enhance-panel.e2e.ts`, new:

| Case | Expected |
| --- | --- |
| `Backgr.: Remove fills the plan, the Output line and the cost` | scenario `cutout`: after the click the plan line starts `Background`, the Output line reads `PNG · transparent · Desktop`, the cost line `444 MB to download`; the top of the Size and Quality rows is where it was (± 0.5 px) |
| `Backgr.: arrow keys move between Keep and Remove, and both show a focus ring` | as Content |
| `Backgr.: nothing is cut at 1024 × 700` | `scrollWidth <= clientWidth` for the label and both segments at both sizes |
| `Scan: an interlaced video gets the row, a progressive one does not` | `tape` against `video` |
| `Scan: Double rate changes the head, the plan line and Frames` | ` · 50 fps`, `Deinterlace to 50 fps`, option `2× · 100 fps`; no horizontal scrollbar in the pane; the select's text is not cut (`scrollWidth <= clientWidth`) |

`preview-patch.e2e.ts`, new: `a removed background is a struck-through chip and the other
steps still show` (the fake host reports the step `skipped` with the engine's note).

`screens.e2e.ts`, new shots: `panel-background` (scenario `cutout`, Remove pressed) and
`panel-scan` (scenario `tape`, Double rate chosen).

Screenshots: no existing baseline changes, in any of the four projects (`chromium-1024`,
`chromium-1440`, `webkit-1024`, `webkit-1440`): the shared fixtures list neither feature and
the fake video has no field order. The two new shots add eight files. A differing old baseline
is a bug in the "shows when" rules.

## Later

| Part | Appears when | Where and how |
| --- | --- | --- |
| `Colour ▾` | the step takes a fill colour | third segment; white, black and a picker in a row popover; a filled picture keeps the chosen format |
| Backgr. in recipes; a `Product cut-out` workflow | the recipes file gains a field for step stages that older builds read past | `recipeSettingsPatch`, `recipeMatches`, `recipePlanLine`, and `in_recipe_stages` for the step |
| A second background model (BiRefNet HR, matting) | the catalog has two models with `background` | the row keeps its segments; the model moves to a line under it with `Change…`, as the upscaler has |
| Scan off; inverse telecine | the engine can leave flagged frames alone, or detects 3:2 pulldown | further options of the same select |
| The plan knows the file | `plan_job` takes the field order | the plan line lists the silent `Automatic` deinterlace and drops a stage the engine will skip |

## What may not work as drawn

- **The fold.** The photo panel grows by one 30 px row. At 1024 × 700 with the cost line
  showing, `Backgr.` can be under the edge of the scrolling body; Size and Quality are above
  it and stay in view.
- **A stage the engine skips.** With `Double rate` stored and a file that says `progressive`,
  the plan line still reads `Deinterlace to 50 fps`; the row's `title` and the result's note
  (`Already progressive · kept as it is`) say what happens.
- **`unknown` is broad.** A GIF and an old progressive AVI show the Scan row. `Double rate`
  on such a file doubles its frames from half-pictures it never had.
- **A hand-set order cannot be previewed.** The patch shows one decoded frame without Scan.
- **A file that says `progressive` but has stripes** (a tape captured to MP4 by a converter)
  gets no row; see the first open question.

## Open questions

Each has a default the spec builds.

1. **Scan on files that say progressive.** No row (built), so a wrongly marked file cannot be
   forced; or the row on every frame-by-frame video, one line in `scanRowShown`.
2. **Scan without the engine fix.** If S0 does not land, nothing of Scan ships (built this
   way); or ship the row without `Double rate`, which leaves only the hand-set order.
3. **Backgr. in recipes.** Left out of 0.7.0 (built); a recipe then neither sets nor clears it.
4. **`Colour ▾`.** Waits (built); the row is drawn so a third segment fits.
