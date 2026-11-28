# The Enhance panel

Build spec for LocalSR 0.7.0, branch `w/070-enhance-panel` (based on `rc/0.7.0`). Line numbers
refer to `dc2bb271`; every reference also names its function or its text, because the first
tasks move code. It builds the design Hermes chose on 2026-11-26: the merged council board
(`Merged.dc.html`), as drawn, with the Tweak "Size above Fix" off.

Five things change:

1. **The panel is rebuilt as the board draws it**: fixed rows with a label column, a Fix list
   of checkboxes, a Result block, and a foot that stays in view with the plan line, the Output
   line and the download cost. Quality becomes one row, recipes become a menu, Advanced leaves
   the panel.
2. **The panel moves out of `App.svelte`** (6606 lines) into `EnhancePanel.svelte` and a few
   small components.
3. **The plan comes from the host.** The plan line, the model names, the downloads and the
   Start label read the Rust job plan (`plan_job`, `commands.rs:1152`) instead of `resolvePlan`
   in `lib/plan.ts`.
4. **The accent is blue**, and on macOS it is the accent colour chosen in System Settings, with
   the text on it picked by contrast.
5. **The colour-blind palette becomes a measured mode**: its four colours are told apart under
   protan, deutan and tritan simulation, and a test holds that.

The board also draws tools that have no engine yet (Faces, Exposure, Stabilise, Scan, Remove
background, Creative, Auto, mixed batches). This spec builds the panel they will sit in and
says where each one appears when its step exists; see "What the board draws and the code cannot
run yet" and "Later".

The worker protocol does not change. The settings file gains one optional field. The host gains
one preference, one command and one plan stage; all are additive.

## Decisions

Each is settled here with its reason; Hermes can still change them. Points where the board and
the code disagree are marked **(deviation)** and come up again under "Open questions".

| Point | Decision | Reason |
| --- | --- | --- |
| Order of blocks | Task, Your photo (Content), Fix, Result (Size, Quality, model, Frames), foot | The board's default (`sizeFirst: false`, Merged.dc.html:60) and the settled order Content, Fix, Size (size-decides.md:25). |
| Rows never move | A row keeps its place and its height in every state. What cannot apply is disabled, not removed. | The board's rule ("Rows never move"). Size sits under the Fix list, so a row that vanished above it would move Size under the pointer. |
| Fix list | One checkbox row per fix the engine has for this kind of file, read from the step registry (`steps.json`). Today: Blur, JPEG artifacts, Noise, Colour, and De-flicker for video. | The registry already lists steps, media and order (`lib/steps.ts:6-12`); a new step then shows up without panel work. |
| Order of the rows **(deviation)** | The engine's default run order: Blur, JPEG artifacts, Noise, Colour (`steps.json` orders 10, 20, 30, 40 at 68-163; `DEFAULT_FIX_ORDER`, `lib/steps.ts:58`). The board draws Noise first. | The board's own note says "fixed rows in run order". List and plan line then agree. One function decides it (`fixRowOrder`), so the other answer is a one-line change. |
| Value on a ticked row **(deviation)** | The fix's model name, and its download size while it is missing. The board draws `Medium ▾`. | Built in 0.7.0: on Blur, JPEG artifacts and Noise the value is the strength and the model moves into the popover alone (`tools-wave.md`, section 1); Colour and an engine without `fix_strength` keep the model name. |
| More fixes | Shown only when at least two rows would be hidden. Today every row shows. | A link that hides one row costs a click and saves one line. |
| Content at Same size **(deviation)** | Stays visible, disabled, with a tooltip. Today it is hidden (`{#if !restoring}`, `App.svelte:4837`; size-decides.md:48). | Rows never move; see above. |
| Content on a video | The two segments read `Video` and `Animation`; the stored values stay `photo` and `illustration`. | The board, panel (b). Wording only. |
| Quality | One segmented row `Quick` / `Best`. The blurb and the download size move into each segment's tooltip. `Back to Best` goes: pressing Best does the same. | The board. The two cards are 56 px high (`styles.css:1721-1722`) and push Size out of view. |
| Recipes | A `Recipes ▾` menu in the panel head: the saved recipes, delete with the same question as today, `Save current as recipe…`. | The board. It replaces the header summary, which repeats the plan line. |
| Models | One line under Quality: the upscaler's name and `Models…`. Each ticked fix has `Change…` in its popover. The order arrows and `Try others…` move to the Plan sheet. | The board. The Model card's rows are covered by the Fix list and the plan line. |
| Frames (video) | One native select in the Result block: `Original`, then the four targets after upscaling, then the same four before upscaling. It writes the two existing settings. | The board draws one dropdown. A native select needs no popover code and the row never grows a second control. |
| Foot | Plan line, Output line, cost line. Always in view; the body scrolls above it. | The board. |
| Start button **(deviation)** | Stays in the status bar, directly under the panel. The board draws it inside the foot. | The bar's start slot also holds Cancel, Add to queue, Reveal, Open and Use as input (`App.svelte:5540-5630`), and it must be reachable while the Enhance pane is hidden in a narrow window (`class:compact-hidden`, `App.svelte:4552`). The bar's layout is covered by tests (`layout.e2e.ts:200-266`). |
| Advanced | Leaves the panel for Upscale & restore and for video. Export settings open from the Output line; hardware, priority, tiles and memory open from the plan line. Edit keeps its Advanced section unchanged. | The board ("Advanced moves to Settings") and the council (per-run overrides stay one click away). The saved defaults are already in Settings › Performance (`SettingsSheet.svelte:525-650`). |
| Popovers | Our own small component, placed like `HelpTip` places its text. No Popover API, no CSS anchor positioning, no `contrast-color()`. | The app supports macOS 12 (`tauri.conf.json:120`); the Popover API needs Safari 17, anchor positioning and `contrast-color()` Safari 26. No polyfill: the app has two runtime dependencies (`package.json`). |
| Panel width | Stays 340 px (`styles.css:1002`). The board is drawn at 400 px. | The window's minimum is 276 + 380 + 340. Every row below is sized for 340. |
| Section heads | `Your photo`, `Your video`, `Result` in sentence case in the DOM, shown in capitals by CSS (11 px, letter-spaced), with the file's facts or the result size on the right. | The board. The accessible names stay plain words. |
| Plan source | The host, always, inside Tauri. `resolvePlan` stays for the browser demo and as the parity check. | Brief item E2. See "The plan comes from the host". |
| De-flicker in the plan | The host plan gains a `deflicker` stage. | The plan line must not be assembled from settings in the UI; De-flicker has no model, so today it is in no plan (`job_plan.rs:287-547`, `plan.ts:180-363`). The same pattern serves Stabilise and Scan later. |
| Accent | `#3a6ea5` with white text on every platform. On macOS it is replaced by the system accent, unless the colour-blind palette or high contrast is on. | Hermes's pick. The two modes are tested palettes and stay fixed. |
| macOS accent source **(deviation)** | The host reads it; the WebView cannot. | Measured 2026-11-27 on macOS 27.2 in a plain `WKWebView` (a small Swift test program, not the app; Playwright's WebKit gives the same): with Pink set, `AccentColor` and `-apple-system-control-accent` both compute to `rgb(0, 122, 255)` while `NSColor.controlAccentColor` is `#f74f9e`. |
| Text on the accent | White when white reaches 4.5:1 on the accent, else black. | One of the two always reaches 4.58:1 or more, because the two ratios multiply to 21. |
| Multicolour **(deviation)** | LocalSR's own blue. | macOS means "each app's own colour" by Multicolour. Hermes listed it among the accents that need contrast-picked text; with the app's own blue it needs none. |
| Colour-blind mode | Same switch, new values, a measured rule, and the accent is fixed in this mode. | "A proper balanced mode". Today's palette is worse than the default one on its main pair: Danger and Warning are 7.3 apart in the dark theme and 1.4 in the light one (scale below). |

## What the board draws and the code cannot run yet

`rc/0.7.0` has the registry, model-less stages, parameters and masks, but no new tool. These
board elements are therefore absent in this build. None of them leaves a gap: the rows around
them are complete without them.

| On the board | Why not now | In this build |
| --- | --- | --- |
| Rows Faces, Exposure, Stabilise, Scratches & dust | No such step runs. `face_restore` exists in the registry (`steps.json:377`) but the UI sends `enable_face_model: false` (`startInput.ts:111`) and `plan.e2e.ts` asserts "Faces is not offered". | Not listed. A row appears when its step is in `steps.json` and the engine lists the step's `required_feature`. (Stabilise is built: `tools-wave.md`, section 2.) |
| `Natural ▾`, `Crop to fit ▾` | No parameters on Colour. | The model name is the value. (`Medium ▾` is built for Blur, JPEG artifacts and Noise: `tools-wave.md`, section 1.) |
| `Auto` switch, hints (`· looks compressed`, `2 found`) | No detector. "Nothing ticks itself" (size-decides.md:46) holds. | Absent. The Fix head has room on the right. |
| `More ▾` in Content (Scan, Tape, Film, Anime) | `content` is `photo` or `illustration` (`types.ts:60`, `:382`). | Two segments. |
| `Scan` row | Flagged frames are deinterlaced silently; 50 fps from fields is engine work. | Built as a native select between Content and the Fix head, see `docs/design/scan-and-background.md`. |
| `Detail: Faithful ▾` | No creative upscaler in the catalog. | Absent; Quality's row keeps the space. |
| `Backgr.` row | No background removal (#189). | Built as `Keep` / `Remove`, see `docs/design/scan-and-background.md`. |
| `…` in Size, `long edge 4000 px` | `output_scale` is a whole number from 1 to 4; only SeedVR2 and FlashVSR take a pixel target (`App.svelte:5345-5372`). | Four segments. |
| `ISO 3200`, `PAL DV` in the head | `MediaItem` carries size, frames, rate, duration and HDR (`types.ts:166-185`); no EXIF, no codec. | Size and file type for photos; size, rate and duration for videos. |
| Panel (e): `14 of 30`, `3 different plans`, `2 files set by hand` | One start carries one set of settings for all its files (`StartBatchInput`, `types.ts:722-730`), and there is no multi-selection: the choice is "this one" or "all" (`scopeChoice`, `App.svelte:1013-1018`). | A batch uses the panel as it is. `Only this one` and `Upscale all 30` stay in the status bar. |
| `[time estimate]` | The host can estimate from measured runs (`estimate_request_seconds`, `database.rs:777`) but only the Try others command exposes it, and that needs two to four models (`commands.rs:1796-1803`). | Last task, optional: a small command and the left half of the cost line. |
| `Output: … next to original` | Results go to one chosen folder, the Desktop at first (`AdvancedSettings.svelte:449-467`). | The Output line names that folder. |

## Layout

Width 340 px, 16 px padding, so 308 px of content. Label column 76 px including the `?`.
`●` chosen, `░` disabled, `▾` opens a popover or is a select, `▸` opens a sheet above the foot.

### Photo, one fix ticked, its model not downloaded

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
│ [✓] Noise                RealPLKSR Denoise · 28 MB ▾ │
│ [ ] Colour                                           │
├──────────────────────────────────────────────────────┤
│ RESULT                                   6400 × 4268 │
│ Size ?     [ Same ][ 2× ][ 3× ][● 4× ]               │
│ Quality ?  [● Quick           ][ Best              ] │
│            LocalSR Photo S                   Models… │
╞══════════════════════════════════════════════════════╡
│ Noise → Upscale 4×                                 ▸ │
│ Output     PNG · Desktop                           ▸ │
│                                  28 MB to download ? │
└──────────────────────────────────────────────────────┘
 status bar, unchanged:     [ Download 28 MB, then upscale ]
```

### The popover of a ticked fix

Opens under the row, right edge on the panel's content edge, 230 px wide.

```
│ [✓] Noise                        RealPLKSR Denoise ▾ │
│                     ┌──────────────────────────────┐ │
│ [ ] Colour          │ Noise                        │ │
├─────────────────────│ Model  RealPLKSR Denoise     │─┤
│ RESULT              │                      Change… │ │
│ Size ?     [ Same ][│ 2 more models                │ │
│                     └──────────────────────────────┘ │
```

With a hand-picked or unusual model one more line shows under the name, as the Model card shows
it today: `Your pick · Labs · Non-commercial`, and for the blur model `Can add artifacts to
sharp pictures and line art. Skipped if the result is unstable.`

### Video, frame by frame

```
┌──────────────────────────────────────────────────────┐
│ Enhance                                    Recipes ▾ │
│ Task ?     [● Upscale & restore ][░░░░░ Edit ░░░░░░] │
├──────────────────────────────────────────────────────┤
│ YOUR VIDEO                1920 × 1080 · 24 fps · 12s │
│ Content ?  [● Video           ][ Animation         ] │
├──────────────────────────────────────────────────────┤
│ Fix ?                                                │
│ [✓] Blur                       NAFNet GoPro Deblur ▾ │
│ [ ] Compression                                      │
│ [✓] Noise                        RealPLKSR Denoise ▾ │
│ [ ] Colour                                           │
│ [ ] De-flicker                                  Labs │
├──────────────────────────────────────────────────────┤
│ RESULT                          3840 × 2160 · 48 fps │
│ Size ?     [ Same ][● 2× ][ 3× ][ 4× ]               │
│ Quality ?  [● Quick           ][ Best              ] │
│            LocalSR Photo S                   Models… │
│ Frames ?   [ 2× · 48 fps                         ▾ ] │
╞══════════════════════════════════════════════════════╡
│ Blur → Noise → Upscale 2× → 2× frames              ▸ │
│ Output     MP4 · AV1 · CRF 18 · Desktop            ▸ │
└──────────────────────────────────────────────────────┘
 status bar:                       [ Start selected video ]
```

With every model installed and no time known there is no cost line.

### Video, SeedVR2 or FlashVSR chosen by hand

As today (size-decides.md:49, `App.svelte:4829`): Content, the Fix list, Size and Frames are not
shown, because these models take none of them.

```
│ YOUR VIDEO                1920 × 1080 · 24 fps · 12s │
├──────────────────────────────────────────────────────┤
│ RESULT                                   7680 × 4320 │
│ Size ?     [ 4× · 7680 × 4320                    ▾ ] │
│ Quality ?  [ Quick            ][ Best              ] │
│            FlashVSR v1.1 · Labs ?            Models… │
│ (memory card, VideoMemory.svelte, unchanged)         │
╞══════════════════════════════════════════════════════╡
│ Upscale 4×                                         ▸ │
```

`Size` here is today's Output resolution select with its options (`App.svelte:5345-5372`).
Neither Quality segment is pressed; pressing one goes back to a frame-by-frame model.

### Same size, nothing ticked

```
│ Content ?  [░ Photo ░░░░░░░░░][░ Illustration ░░░░░] │
│ Fix ?                                                │
│ [ ] Blur                                             │
│   …                                                  │
│ RESULT                                   1600 × 1067 │
│ Size ?     [● Same ][ 2× ][ 3× ][ 4× ]               │
│ Quality ?  [● Quick           ][ Best              ] │
╞══════════════════════════════════════════════════════╡
│ ! Nothing to run. Pick what to fix, or choose a      │
│   larger size.                                       │
│ Nothing to run                                     ▸ │
│ Output     PNG · Desktop                           ▸ │
```

The model line is the last line of the Result block and shows only while a model is upscaling
or an own file is in use, so its absence moves nothing.

### The foot when something is needed from the user

Above the plan line, only in these states. The markup is today's (`App.svelte:5209-5277`).

```
╞══════════════════════════════════════════════════════╡
│ Upscale · HAT-L ×4                                   │
│ [ ] I reviewed the model license and restrictions.   │
│ [ Choose externally downloaded checkpoint…         ] │
│ Blur → Upscale 4×                                  ▸ │
```

### Recipes menu

Under `Recipes ▾`, right edge on the panel's content edge, 260 px wide.

```
                         ┌────────────────────────────┐
                         │ ✓ Phone photos           ✕ │
                         │   upscale · 4× · FP16      │
                         │   Old slides             ✕ │
                         │   same size · FP32         │
                         │ ────────────────────────── │
                         │ Save current as recipe…    │
                         └────────────────────────────┘
```

`Save current as recipe…` turns into the name field and `Save` in the same place (today's
`.recipe-input`, `App.svelte:4812-4823`). With no recipe the list is one grey line, `No recipes
yet.`

### Plan sheet

Opens upward from the plan line and covers the lower part of the body, full panel width. It
shows the order and lets the fixes swap places; a model is changed where it is named, in the
fix's popover or on the model line.

```
┌──────────────────────────────────────────────────────┐
│ Plan                                                 │
│ 1  Blur         NAFNet GoPro Deblur                ↓ │
│ 2  Noise        RealPLKSR Denoise · 28 MB          ↑ │
│ 3  Upscale 4×   LocalSR Photo S        Try others… ? │
│ ──────────────────────────────────────────────────── │
│ This run                                             │
│ Hardware ?     [ Neural Engine                   ▾ ] │
│ [ ] Run at low priority ?                            │
│ Tile ? [256 ▾]  Halo ? [32 ▾]  Precision ? [Auto ▾]  │
│ [ ] Safe memory mode ?                               │
│ Normal memory pressure ?                             │
╞══════════════════════════════════════════════════════╡
│ Blur → Noise → Upscale 4×                          ▾ │
```

### Output sheet

Photo: Format, quality (for JPEG, WebP, HEIC), `Preserve safe metadata and color profile`, Save
to. Video: Container, Video format, External FFmpeg, Video quality · CRF, Colour output (only
for an HDR source), `Clean up audio` and Audio model, Save to. Every control is today's, with
today's ids and tips.

```
┌──────────────────────────────────────────────────────┐
│ Output                                               │
│ Format ?        [ PNG                            ▾ ] │
│ [✓] Preserve safe metadata and color profile ?       │
│ Save to ?   /Users/…/Desktop                 Choose… │
╞══════════════════════════════════════════════════════╡
│ Output     PNG · Desktop                           ▾ │
```

## Where everything goes

Nothing the panel does today is dropped except three links, named at the end.

| Today (`App.svelte`) | New place |
| --- | --- |
| Head summary `Quick · Photo · 4×` (4553-4573) | Gone; the plan line says it. `Locked while running` and `Moving models` stay in the same spot and replace `Recipes ▾` while they apply. |
| Task switch and caption (4592-4612, `taskCaption` 1468-1474) | Task row; the caption becomes the `?` text. |
| HDR section with its prose (4615-4666) | Output sheet, `Colour output`; the two paragraphs become its `?` text. `Choose a HAT model for HDR preservation.` becomes a foot note. |
| Audio section (4668-4712) | Output sheet. |
| Edit prompt (4714-4724) | Unchanged. |
| Quality cards (4734-4751) | Quality row. |
| `Save as recipe…`, the saved recipes, the name field (4728-4733, 4752-4824) | Recipes menu. |
| Content (4837-4861) | Content row. |
| Fix chips (4862-4898) | Fix list. |
| Size (4899-4925) | Size row; its result moves to the Result head. |
| Model card rows (4949-5277): name, state, arrows, `Change…` | Fix popover (model, `Change…`, `N more models`, notes); model line (the upscaler and its `Models…`); Plan sheet (order, arrows, download state, `Try others…`). |
| Terms tick, `Choose checkpoint…`, pickle consent, manual download, licence credit (5209-5277) | Foot, "needs you" block. |
| `Nothing to run…`, video model unavailable, SDR note, one-fix engine note, edit block reason (5279-5310) | Foot notes. |
| `Chosen by you. Pick Quick or Best to go back.` (5311-5316) | Tooltip of the model name. |
| `Models download once…` (5317-5324) | `?` beside the download size on the cost line. |
| `VideoMemory`, Output resolution and its prose (5325-5389) | Result block of a hand-picked video model; the prose becomes the Size `?` text. |
| Last error, model folder missing (5391-5425) | Unchanged, last in the body. |
| Advanced (5427-5450, `AdvancedSettings.svelte`): Format, quality, metadata (239-288), Container, Video format, External FFmpeg, CRF (289-370), Save to (449-467) | Output sheet. |
| Advanced: De-flicker (371-387) | Fix row. |
| Advanced: Frame rate, Interpolate (388-432) | Frames row. |
| Advanced: Hardware, low priority, session note, Tile, Halo, Precision, Safe memory, memory pressure (468-613) | Plan sheet, "This run". |
| Advanced in Edit: Edit size, Steps, Seed (174-238) and the rest | Unchanged: Edit keeps `AdvancedSettings`. |

Dropped: `Back to Best` (4934-4939; Best does it), the `SeedVR2, FlashVSR…` link under the
upscaler (5170-5189; `Models…` opens the same library, which lists them), and the two prose
lines about timing and Labs at the end of the video settings (`AdvancedSettings.svelte:433-447`),
which go into the Frames and Size tips.

## Copy

Every string that is new or changes. Strings not listed stay.

| Where | String |
| --- | --- |
| Head, right | `Recipes` with a chevron · `Locked while running` · `Moving models` |
| Task row | label `Task`; segments `Upscale & restore`, `Edit` |
| Task tip, photo or no file | `Upscale & restore enlarges photos and artwork, fixes them, or both. Edit changes a photo from a description in words.` |
| Task tip, video | `Upscale & restore enlarges videos, fixes them, or both. Edit is for photos.` |
| Section head | `Your photo` (was `Your image`) · `Your video` |
| Facts, photo | `1600 × 1067 · JPEG`: the size, then the file type from the name's extension (`JPEG`, `PNG`, `TIFF`, `WebP`, `HEIC`, else the extension in capitals); nothing before the probe is ready |
| Facts, video | `1920 × 1080 · 24 fps · 12s` and ` · HLG` or ` · PQ` (`formatDuration`, `lib/format.ts:11`; rate with at most three decimals) |
| Content, photo | `Photo`, `Illustration`; tip unchanged (4841) |
| Content, video | `Video`, `Animation`; tip `Video picks models trained on camera footage: skin, foliage, fabric, buildings. Animation picks models for drawings, anime and cartoons, which keep lines clean and colours flat. Choose what the video is, not the look you want.` |
| Content at Same size, `title` | `Content chooses the upscaler. Nothing is enlarged at Same size.` |
| Fix head | `Fix` |
| Fix tip, photo | `Tick what is wrong with the picture. Each problem gets its own fix at the original size, before the picture is enlarged. Quick picks a fast model for each problem, Best the most careful one. A ticked row names its model; click the name to change it, and LocalSR remembers your choice for that problem. The plan line at the bottom shows the order and lets you change it.` |
| Fix tip, video | the same with `video` for `picture`, ending `De-flicker runs after the frames are processed.` |
| Fix rows | `Blur`, `JPEG artifacts` (video: `Compression`), `Noise`, `Colour`, `De-flicker` |
| Row `title` | Blur `Reduces blur from motion or missed focus.` · JPEG artifacts `Removes blocks and ringing left by JPEG compression.` · Compression `Removes blocks and ringing left by video compression.` · Noise `Removes grain and sensor noise.` · Colour: today's (4880-4882) · De-flicker: today's tip text (`AdvancedSettings.svelte:382`) |
| Row value | `RealPLKSR Denoise` · missing `RealPLKSR Denoise · 28 MB` · downloading `RealPLKSR Denoise · 42%` · queued `… · Waiting` · `Needs your file` · `No model for this fix` |
| Row tag | `Labs` (De-flicker) |
| Row disabled, `title` | De-flicker with HDR kept: `Not available while HDR is preserved.` |
| Fix popover | head: the row's label · `Model` · `Change…` (accessible name as today: `Change the noise fix model`) · `2 more models` · notes as today (`meta` and `warning`, 5018-5060) |
| More fixes | `More fixes (3)` / `Fewer fixes` |
| Result head | `Result`, value `6400 × 4268`, on video ` · 48 fps` when Frames changes the rate |
| Size | label `Size`; segments `Same` (accessible name and `title` `Same size`), `2×`, `3×`, `4×`; tips unchanged (4903-4904) |
| Size, hand-picked video model | label `Size`, the select's accessible name stays `Output resolution`; tip `Smaller output takes less time and memory. For a first test, try 256 or 512 px. FlashVSR works best up to 4× the source: above that, it runs at 4× and resizes the rest.` (SeedVR2: without the last sentence) |
| Quality | label `Quality`; segments `Quick`, `Best` |
| Quality tip | `Quick picks fast models that give a clean result. Best picks the most careful ones and takes longer. Both choose the upscaler and the model for each fix.` |
| Quality segment `title` | the blurb and the download: `Fast, clean result · 287 MB download` (`qualityBlurb`, 2114-2123; `presetDownload`) |
| Model line | name, then ` · your pick`, ` · Labs`, ` · Non-commercial`, a fit label, as the card's note has them (5018-5053); link `Models…` (accessible name as today: `Change the upscaler`, `Change the restore model`, `Change the edit model`) |
| Model name `title`, hand-picked | `Chosen by you. Pick Quick or Best to go back.` (video model: today's longer sentence, 5314) |
| Frames | label `Frames`; options `Original`; group `Add frames after upscaling`: `2× · 48 fps`, `4× · 96 fps`, `60 fps`, `120 fps`; group `Add frames before upscaling (slower)`: `2× · 48 fps · before upscaling`, … (without a known rate: `2×`, `4×`) |
| Frames tip | `Adds in-between frames for smoother motion. 2× and 4× multiply the clip's own rate; 60 and 120 fps set a fixed rate and are skipped when the clip is already about that fast. Adding them after upscaling is faster; before upscaling can look smoother, but the added frames are upscaled too, which takes about twice as long at 2×. RIFE · Labs makes the frames: scene cuts stay cuts, very fast motion can smear. The first use downloads about 25 MB.` |
| Frames disabled, `title` | `This engine cannot add frames.` · `Not available while HDR is preserved.` |
| Plan line | short step names joined by ` → `; see "Plan line". Empty plan: `Nothing to run`. First load: `Working out the plan…`. Failure: `Could not work out the plan.` and the link `Try again` |
| Plan line, accessible name | `Plan: Blur, Noise, Upscale 4×` |
| Plan sheet | heads `Plan` and `This run`; the list keeps its accessible name `Stages`; rows are named as on the plan line; the arrows keep today's accessible names (`Move the blur fix later`); `Try others…` and its tip unchanged; no `Change…` here |
| Output line, photo | `Output` · `PNG · Desktop` · `JPEG 98 · Desktop` · no folder: `PNG · choose a folder` |
| Output line, video | `MP4 · AV1 · CRF 18 · Desktop`; ` · HDR` after the codec when HDR is kept; ` · sound cleaned` before the folder; codec names `Automatic`, `AV1`, `VP9`, `FFV1`, `H.264`, `HEVC`, `ProRes` |
| Output sheet | head `Output`; `Colour output` tip: the paragraphs of 4639-4657 as one text |
| Cost line | `287 MB to download` · with a time `about 40 s` on the left |
| Cost tip | `Models download once when you start and stay on this computer. Your photos never leave it.` (`videos` on a video) |
| Needs-you head | the step and the model: `Upscale · HAT-L ×4` |
| Settings › Appearance, macOS only | switch `Use the macOS accent colour`, description `Buttons and ticks take the colour chosen in System Settings. Off uses LocalSR's blue.` |

## Behaviour

### Fix rows

`lib/fixRows.ts` builds the list; nothing about a fix is written in the panel's markup.

```ts
export interface FixRow {
  id: string;                       // the step id: 'blur', 'jpeg', 'noise', 'colour', 'deflicker'
  source: 'fix_stage' | 'setting';  // settings.fix_stages, or one boolean setting
  label: string;                    // for this media kind
  title: string;
  tag: '' | 'Labs';
  common: boolean;
}
export function fixRows(media: 'image' | 'video', features: string[]): FixRow[];
export function fixRowOrder(): string[];
```

- A row exists for every step with `is_fix_stage`, plus the steps named in `SETTING_ROWS`
  (`{ deflicker: 'deflicker' }`, step id to settings field), when the step's `media` has this
  kind and its `required_feature` is empty or in `snapshot.engine.features`. `face_restore`,
  `interpolate` and `audio_denoise` are not rows.
- Order: `fixRowOrder()` returns the step ids by phase (`PHASES`, `steps.json:14`) and `order`.
- `tag` is `Labs` for the steps in `LABS_ROWS` (today `['deflicker']`, as its label says now,
  `AdvancedSettings.svelte:379`). `label` and `title` come from the Copy table; a step without
  an entry there uses the registry's `label` and no title.
- `common`: Blur, JPEG artifacts, Noise for a photo; these and De-flicker for a video. Rows
  that are not common, not ticked and not hinted are hidden behind `More fixes (n)` when there
  are two or more of them; otherwise every row shows. Closed, the link is the list's last line;
  open, the hidden rows take their places in the order and the link reads `Fewer fixes`. The
  state is kept for the session, not saved.
- The checkbox is a real `<input type="checkbox">`, named by its label, inside
  `role="group"` named `Fix`. The whole row is its click target, 30 px high. Ticked boxes are
  filled with `--accent`, the tick drawn in `--on-accent`.
- A `fix_stage` row calls today's `toggleFix` (`App.svelte:2243`): the fix goes on at its
  place in the default order and an order the user changed stays (`toggleFixStage`,
  `fixStages.ts:37-47`). Unticking keeps the remembered model (`preset_pins`), as today.
- A `setting` row writes its field with `updateSettings`.
- The value of a ticked `fix_stage` row is a button (`aria-expanded`, `aria-controls`) that
  opens the row's popover. Its text is the stage's model from the plan, found by `stage.fix`.
  Until the plan is there, it is the model name from `settings.fix_stages`; no size.
- De-flicker is disabled while `video_hdr_mode` is `preserve`
  (`AdvancedSettings.svelte:376`), and shows unticked then.
- The engine runs one fix at a time when it lacks `image_multi_fix` (`fixPlanSupported`,
  `App.svelte:760-761`): the rows stay as they are and the foot shows today's note.
- Colour's popover has no `Change…` (as today, `changeSlot`, 4961-4963) and shows the Colour tip's sentence
  under the model.

### Content, Size, Quality

All three keep today's handlers (`setContent` 2238, `setSize` 2604, `applyPreset` 3337) and
`radioGroupKeydown` (2622). Changes:

- Content is rendered at Same size too, with both radios `disabled` and the `title` above. The
  pressed one stays pressed.
- Size's result leaves the row for the Result head; it keeps the class `field-value`.
- Quality is `role="group"` named `Quality` with two `aria-pressed` buttons, as today, so
  names such as `/^Quick/` still match. With a hand-picked upscaler neither is pressed.

### Frames

One `<select id="frame-rate">`. Its value is `off` or `<target>:<order>`, for example
`x2:after_upscale`; choosing an option writes `video_interpolation` and
`video_interpolation_order` in one `updateSettings`. The option texts add the resulting rate
when the file's rate is known (`resultFps`, new in `plan.ts`, beside `frameRateChange` at 394;
a fixed target at or below the file's rate keeps the file's rate). Disabled when
`preservingHdr || !interpolationSupported` (`AdvancedSettings.svelte:396`).

### Popover and sheets

One component, `Popover.svelte`, in two placements.

| | `placement="row"` | `placement="sheet"` |
| --- | --- | --- |
| Used by | Fix value, Recipes | Plan line, Output line |
| Position | `position: absolute` in the row's wrapper, under the row, right edge on the content edge; above the row when it would be cut off below and fits above (the check `HelpTip.svelte:19-33` makes) | `position: absolute` in `.enhance-pane`, `left: 0; right: 0; bottom:` the foot's height; `max-height` the body's height, own scrolling |
| Width | 230 px (Recipes 260 px) | the panel |
| Surface | `--dialog-bg`, 1 px `--surface-border`, `--radius-surface`, shadow `--shadow-medium` | the same, no side borders |

Both: the trigger is a `<button>` with `aria-expanded` and `aria-controls`; the surface is
`role="group"` with an `aria-label` (`Noise options`, `Recipes`, `Plan`, `Output`). No
`aria-haspopup` and no `role="menu"`: the content is ordinary controls, not a menu with arrow
keys. Opening with the keyboard moves focus to the first control inside. Escape closes and
returns focus to the trigger; so does a click outside, and focus leaving the surface. One is
open at a time. The surface follows its trigger in the DOM, so Tab order is natural. Nothing is
animated.

### Foot

A `<div class="panel-foot">` after the scrolling body, outside the `fieldset` that a running
job disables (`App.svelte:4587-4591`), so the lines stay readable and the sheets open while
locked. Controls inside the sheets take `disabled={settingsLocked}`, except `Run at low
priority`, which stays live as it does today (`AdvancedSettings.svelte:490`).

From top to bottom, each only when it applies:

1. **Needs you**: one block per plan stage that Start cannot satisfy by itself, in plan order.
   The conditions and controls are those of `.plan-extra` (5209-5277) and of the Edit terms
   (5290-5310).
2. **Notes**, `role="status"`, `.inline-warning`: `nothingToRunReason` (900-904),
   `temporalUnavailableReason`, `SDR output selected. This model cannot preserve HDR.`, `Choose
   a HAT model for HDR preservation.`, the one-fix engine note, `editBlockReason`, a plan error.
3. **Plan line** (not in Edit).
4. **Output line** (not in Edit).
5. **Cost line**: when the plan has downloads or a time is known.

With no task chosen the foot is not rendered, and the body holds the Task row alone, as today
(`{#if settings.task}`, 4712).

### Plan line

`lib/planLine.ts`, pure: `planLine(stages, settings, media): string[]`. It keeps the order of
the host's stages and only shortens their names:

| Stage | Name |
| --- | --- |
| `role: 'fix'`, or `'primary'` with a `fix` | the row label of that fix: `Blur`, `JPEG` (`Compression` on video), `Noise`, `Colour` |
| `kind: 'upscale'` or `'video'`, primary | `Upscale 4×` from `output_scale`; with `video_target_resolution`: `Upscale to 1080 px` |
| `kind: 'restore'`, primary, no fix (own file) | `Fix` |
| `kind: 'edit'` | `Edit` |
| `kind: 'deflicker'` | `De-flicker` |
| `kind: 'interpolate'` | `2× frames`, `4× frames`, `60 fps`, `120 fps` |
| `kind: 'audio_denoise'` | `Clean audio` |
| `kind: 'face_restore'` | `Faces` |
| a kind it does not know | the host's `label` |

The line wraps to two lines at most (`-webkit-line-clamp: 2`, as `.status-detail` does,
`styles.css:2287-2295`); the full text is its `title`.

### Locked, Edit, batch

- **Locked** (`settingsLocked`, 662-666): the body's `fieldset` is disabled as today; the head
  shows `Locked while running`; Recipes is not offered.
- **Edit**: head, Task row, the prompt, the model line (`Models…` opens the edit models),
  `AdvancedSettings` as today, then the foot with needs-you, notes and the cost line.
- **Several inputs**: the panel shows the selected file's facts and one set of settings. The
  status bar offers `Only this one` and `Upscale all N` as today.

## The plan comes from the host

Today `App.svelte:1341-1347` computes `plan = resolvePlan(snapshot, settings, …)` and ten places
read it (916-919, 959-960, 1348-1350 and the Model card). `plan_job` exists
(`commands.rs:1152-1158`, registered `lib.rs:266`) with its wrapper `api.planJob`
(`lib/api.ts:193-224`), and nothing calls it. `jobPlanParity.test.ts` proves on 359 cases that
the two planners agree.

### Client

New `lib/jobPlanClient.ts`:

```ts
export type PlanState =
  | { status: 'loading'; stages: PlanStage[]; first: boolean }  // stages: the last good plan
  | { status: 'ready'; stages: PlanStage[] }
  | { status: 'error'; stages: []; message: string };

export function toHostStage(stage: PlanStage): HostPlanStage;                 // from jobPlanParity.test.ts:81-102
export function hydrateStage(stage: HostPlanStage, snapshot: AppSnapshot): PlanStage;
export function planLocally(snapshot: AppSnapshot, settings: UiSettings, options: PlanJobOptions): HostPlanStage[];
export function planKey(snapshot: AppSnapshot, settings: UiSettings, options: PlanJobOptions): string;
export function fetchPlan(snapshot: AppSnapshot, settings: UiSettings, options: PlanJobOptions): Promise<PlanStage[]>;
export function createPlanClient(fetch = fetchPlan): {
  state: Readable<PlanState>;
  request(snapshot: AppSnapshot, settings: UiSettings, options: PlanJobOptions): void;
  settled(): Promise<PlanState>;
  retry(): void;
};
```

- `fetchPlan` inside Tauri: `api.planJob(settings, options)`, then `hydrateStage` on each
  stage. Outside Tauri (`!api.isTauri()`, `lib/api.ts:28`): `resolvePlan`, wrapped in a
  resolved promise so both paths have the same timing.
- `hydrateStage` looks each id up in the snapshot (`catalog.models`, `video_models`,
  `interpolation_models`, `audio_models`, `edit.models`, `edit.custom_model` for
  `__custom__`) and takes `installed`, `can_download`, `needs_file` and `size_bytes` from the
  host. An id the snapshot does not have throws; the client reports it as an error.
- `planLocally` is `resolvePlan` followed by `toHostStage`. The e2e fake host and the parity
  test use it.
- `planKey` is a string of the settings, the options, the installed model ids of the four
  catalogs and of the edit models, and the device ids. Two snapshots that differ only in
  progress give the same key.

### When it calls

No timer. `App.svelte` calls `planClient.request(snapshot, settings, options)` from a reactive
statement in place of `resolvePlan`.

1. `request` computes the key. The same key as the plan shown or the one in flight: nothing
   happens.
2. Otherwise the request is noted and sent in a microtask, so several `updateSettings` calls of
   one click (`setTask` saves twice, `App.svelte:2697` and `2720`) make one host call.
3. One call is in flight at a time. A newer request waits for it and is sent when it returns;
   an answer whose key is no longer the newest is dropped.
4. An answer is given up after 5 s and treated as a failure.

`plan_job` reads two locks and no file (`commands.rs:1165-1166`), except in Edit, where it
lists the edit models from disk (`:1169-1174`). Sliders write on release (`on:change`), and the
Edit prompt is not a setting, so a click is the fastest thing that can ask for a plan.

Options sent: `{ face_enabled: false, has_audio: audioHasSource }`. The host derives the rest
from the settings with the same rules the UI uses (`commands.rs:1181-1206`,
`job_plan.rs:112-116` against `App.svelte:762-765`).

### What shows

| State | Plan line | Row values, model line, cost | Start |
| --- | --- | --- | --- |
| First plan not there yet | `Working out the plan…` in `--secondary` | names from the settings, no sizes, no cost | off |
| Loading, a plan is shown | the last plan, `aria-busy="true"`; after 400 ms at 55 % opacity | the last plan's | as the last plan allows; a click waits (below) |
| Ready | the plan | the plan's | as today |
| Error | `Could not work out the plan.` `Try again`, `role="status"`, the message as `title` | names from the settings | off, `title` `Could not work out the plan. Try again.` |

- `plan` in `App.svelte` becomes `$planState.stages`; its readers do not change.
- `start` (3409), `prepareAndStart` (2444) and `startScope` (2461) begin with `await
  planClient.settled()` and return when the result is an error. A click during the few
  milliseconds a plan is in flight therefore runs on the new plan and the button never flashes
  off.
- `queueReady` (993-1003) and `prepareReady` (1352-1370) gain `planState.status !== 'error'`.
- A failure is recorded once with `api.recordFrontendError`.
- Nothing inside Tauri falls back to `resolvePlan`.

### What stays in TypeScript

- `resolvePlan` (`plan.ts:180-363`): the browser demo's planner (`demoSnapshot`,
  `lib/demo.ts:24`; every host call is skipped there, `lib/api.ts:30-37`), `planLocally` for
  the fake host, and one side of the parity test.
- Everything else in `plan.ts` (ranking, presets, `pendingDownloads`, library lists) is not a
  plan and stays. `presetDownload` (520) still feeds the Quality tooltips; the host has no
  equivalent.
- `buildStartInput` (`startInput.ts:60`) still builds the start request. The host builds the
  same (`build_start_input`, `job_plan.rs:550`) and the parity fixture compares them, but
  `plan_job` returns the stages only. Out of scope here.

### De-flicker in the plan

Both planners append one stage whenever the start request would carry `deflicker: true`
(`job_plan.rs:605-609`): `task` is `video`, `deflicker` is on and the model is frame by frame.
(Keeping HDR on an HDR video already turns the setting off, `App.svelte:2496-2500`.)

```json
{ "kind": "deflicker", "role": "step", "label": "De-flicker",
  "installed": true, "can_download": false, "needs_file": false, "size_bytes": 0 }
```

It goes after the primary stage (after the last fix when there is none) and before an
interpolation that runs after upscaling, the registry's order (`deflicker` temporal 10,
`interpolate` temporal 20, `steps.json:430`, `:482`). `PlanStage['kind']` and `['role']`
(`plan.ts:139-171`) gain the two literals; `pendingDownloads` ignores the stage because it
cannot be downloaded. The fixture is rebuilt with `UPDATE_JOB_PLAN_FIXTURE=1`
(`jobPlanParity.test.ts:667`). The stage is for display: `build_start_input` is not touched.

## Colour

### Accent tokens

`--accent` marks what will run: the Start button, ticked Fix boxes, progress. Selection and
focus stay neutral (`styles.css:41-43`). New tokens: `--accent-press`, `--accent-text` (links
in the panel) and `--accent-edge` (an outline for accents that are too close to the panel;
`transparent` for the fixed palettes).

| Block (`styles.css`) | `--accent` | `--accent-hi` | `--accent-press` | `--on-accent` | `--accent-text` |
| --- | --- | --- | --- | --- | --- |
| `:root`, dark (37-40, 54, 230) | `#3a6ea5` | `#3f75ad` | `#33639a` | `#fff` | `#8fb8ea` |
| light (234) | `#3a6ea5` | `#33639a` | `#2f5c8f` | `#fff` | `#2f5c8f` |
| colour-blind, dark and light (412-413) | as the two rows above | | | | |
| high contrast dark (510, 559-560) | `#9cc2f0` | `#b3d0f4` | `#b3d0f4` | `#000` | `#9cc2f0` |
| high contrast light (631, 680-681) | `#244a75` | `#1f4672` | `#1f4672` | `#fff` | `#244a75` |
| high contrast dark, colour-blind (714-715) | `#6fb1ee` | `#8cc2f3` | `#8cc2f3` | `#000` | `#6fb1ee` |
| high contrast light, colour-blind (748-749) | `#1c50a7` | `#17458f` | `#17458f` | `#fff` | `#1c50a7` |

Computed with the WCAG formula: white on `#3a6ea5` 5.31:1, on `#3f75ad` 4.82:1, on `#33639a`
6.19:1; `#3a6ea5` against the panel `#1b1b1b` 3.24:1 and against `#fafafa` 5.09:1; `#8fb8ea`
on `#1b1b1b` 8.38:1, on `#2a2a2a` 6.98:1; `#2f5c8f` on `#fafafa` 6.60:1; black on `#9cc2f0`
11.39:1; white on `#244a75` 9.09:1.

Rules:

- `.button.primary` (2207-2214) gets `:active { background: var(--accent-press) }` and
  `box-shadow: inset 0 0 0 1px var(--accent-edge)`.
- `.enhance-pane .link-button` and the links inside popovers use `--accent-text`. Links
  elsewhere stay grey (`styles.css:3309-3322`).
- The comment at 37-38 is rewritten: the accent is no longer the logo's colour. The logo keeps
  its terracotta pixel (`BrandMark.svelte:2`).

### The macOS accent

The host supplies it, because the WebView hides it (see Decisions).

Host, new `system_accent.rs`:

- `pub fn accent_pair(value: Option<i64>) -> Option<(&'static str, &'static str)>`, a table
  from the `AppleAccentColor` number to the light and dark colour. Measured on macOS 27.2 as
  `NSColor.controlAccentColor` in sRGB, with the setting forced per process:

  | `AppleAccentColor` | Name | Light | Dark |
  | --- | --- | --- | --- |
  | key absent | Multicolour | none | none |
  | `-1` | Graphite | `#989898` | `#8c8c8c` |
  | `0` | Red | `#e0383e` | `#ff5257` |
  | `1` | Orange | `#f7821b` | `#f7821b` |
  | `2` | Yellow | `#ffc726` | `#ffc600` |
  | `3` | Green | `#62ba46` | `#62ba46` |
  | `4` | Blue | `#007aff` | `#007aff` |
  | `5` | Purple | `#953d96` | `#a550a7` |
  | `6` | Pink | `#f74f9e` | `#f74f9e` |

- `#[tauri::command] pub async fn system_accent() -> Option<SystemAccent>` with `{ light,
  dark }`. On macOS it runs `/usr/bin/defaults read -g AppleAccentColor`, parses the number
  and looks it up; a missing key, an unknown number or a failed read gives `None`. On other
  systems it returns `None` without running anything.

UI, new `lib/accent.ts`, pure functions and one controller:

- `onAccent(accent)`: `#ffffff` when white on the accent is 4.5:1 or more, else `#000000`.
- `shade(accent, on, amount)`: mixes away from the text colour (towards black under white
  text, towards white under black text), 8 % for hover and 16 % for pressed, so the contrast of
  the text only grows.
- `edge(accent, panel, theme)`: `transparent` when the accent is 3:1 or more against the
  panel; otherwise the accent mixed towards black (light theme) or white (dark) in 5 % steps
  until it is.
- `accentText(accent, surface, theme)`: the accent mixed towards white (dark theme) or black
  (light) in 5 % steps until it is 4.5:1 on the surface; the surface is `--raised` in the dark
  theme and `--hover` in the light one.
- `createAccentController(read = api.systemAccent)`: `apply(settings)` and `dispose()`. It
  sets the six tokens as inline custom properties on `<html>` when **all** hold: the host
  returned a pair, `accent` is `system`, the palette is `default`, the contrast is `standard`.
  Otherwise it removes them and the stylesheet's values apply. It reads the host at start and
  whenever the window gets focus, and applies again when `data-theme` or `data-contrast`
  changes.

What the rules give (computed; the test table is built from these rows):

| Accent | Theme | Text | Ratio | Against the panel | Edge | Link |
| --- | --- | --- | --- | --- | --- | --- |
| Blue `#007aff` | dark | black | 5.23 | 4.29 | none | `#3395ff` |
| Blue `#007aff` | light | black | 5.23 | 3.85 | none | `#0062cc` |
| Purple `#a550a7` | dark | white | 4.88 | 3.53 | none | `#bc7cbd` |
| Purple `#953d96` | light | white | 6.20 | 5.94 | none | `#953d96` |
| Pink `#f74f9e` | dark | black | 6.57 | 5.39 | none | `#f758a3` |
| Pink `#f74f9e` | light | black | 6.57 | 3.06 | none | `#ad376f` |
| Red `#ff5257` | dark | black | 6.60 | 5.41 | none | `#ff5257` |
| Red `#e0383e` | light | black | 4.80 | 4.19 | none | `#be3035` |
| Orange `#f7821b` | dark | black | 8.16 | 6.70 | none | `#f7821b` |
| Orange `#f7821b` | light | black | 8.16 | 2.46 | `#de7518` | `#a15412` |
| Yellow `#ffc600` | dark | black | 13.33 | 10.93 | none | `#ffc600` |
| Yellow `#ffc726` | light | black | 13.45 | 1.50 | `#b28b1b` | `#806413` |
| Green `#62ba46` | dark | black | 8.63 | 7.08 | none | `#62ba46` |
| Green `#62ba46` | light | black | 8.63 | 2.33 | `#539e3c` | `#3b702a` |
| Graphite `#8c8c8c` | dark | black | 6.25 | 5.12 | none | `#929292` |
| Graphite `#989898` | light | black | 7.28 | 2.76 | `#909090` | `#636363` |

Only Purple keeps white text; system Blue gets black text (white on it is 4.02:1).

### Setting

| Field | Values | Default | Rust (`types.rs`) | TypeScript (`lib/types.ts:323`) |
| --- | --- | --- | --- | --- |
| `accent` | `"system"`, `"localsr"` | `"system"` | `pub accent: String` | `accent?: 'system' \| 'localsr'` |

A host-owned preference like `contrast`: saved by `set_preference`, kept by a full settings
save, allowed while a job runs, and an unknown value loads the default. No schema bump
(`settings.rs:91`); an older build ignores the field. It is shown as a switch in Settings ›
Appearance on macOS only, under High contrast. The browser demo sets `accent: 'localsr'`
(`lib/demo.ts`), which also gives the e2e fixtures one fixed colour.

### Colour-blind mode

The switch and its label stay (`SettingsSheet.svelte:434-452`). What changes is that the four
meanings (will run, danger, warning, done) are measurably apart, and that the mode owns its
colours: the macOS accent is not used while it is on.

**Rule.** For each of the four colour-blind blocks, take `--accent`, `--danger`, `--warning`
and `--success-strong`; simulate each for normal vision, protanopia, deuteranopia and
tritanopia; in every one of the four views every pair must be at least the block's distance
apart. Simulation: Machado, Oliveira and Fernandes (2009), severity 1.0, applied to linear
RGB. Distance: Euclidean in OKLab, times 100.

```
protan   0.152286  1.052583 -0.204868 |  0.114503  0.786281  0.099216 | -0.003882 -0.048116  1.051998
deutan   0.367322  0.860646 -0.227968 |  0.280085  0.672501  0.047413 | -0.011820  0.042940  0.968881
tritan   1.255528 -0.076749 -0.178779 | -0.078411  0.930809  0.147602 |  0.004733  0.691367  0.303900
```

| Block (`styles.css`) | Accent | Danger | Warning | Success | Smallest distance: today → new | Test asks |
| --- | --- | --- | --- | --- | --- | --- |
| dark (406-437) | `#3a6ea5` | `#f37513` | `#eded6c` | `#3ed0c8` | 7.3 → 18.2 | ≥ 17 |
| light (439-468) | `#3a6ea5` | `#780374` | `#936812` | `#17502a` | 1.4 → 11.7 | ≥ 11 |
| high contrast dark (712-744) | `#6fb1ee` | `#ff8a3d` | `#f5f07a` | `#5fe0d6` | 2.6 → 10.4 | ≥ 10 |
| high contrast light (746-778) | `#1c50a7` | `#970d63` | `#404000` | `#032617` | 1.2 → 11.9 | ≥ 11 |

In the light blocks every colour must also carry text on a near-white panel, which leaves
little room in lightness; 11 to 12 is what a search over hue, chroma and lightness found there.
Danger is a red-purple in the light blocks for that reason.

Derived tokens, same names as each block has today:

| Token | dark | light |
| --- | --- | --- |
| `--danger`, `--danger-border`, `--danger-card-border`, `--danger-overlay-border`, `--danger-pill-border`, `--memory-error-border` | `#f37513` | `#780374` |
| `--danger-text`, `-soft`, `-warm`, `-muted`, `--memory-error-text` | `#ffb27a` | `#780374` |
| `--warning`, `--warning-strong`, `--warning-border`, `--warning-pill-border` | `#eded6c` | `#936812` |
| `--warning-text`, `--warning-text-muted` | `#f9f08f` | `#6b4c0a` |
| `--success-strong` | `#3ed0c8` | `#17502a` |
| fills, pills, hover and glow | the base colour at the alpha the token has today | the same |
| `--danger-surface`, `--danger-banner-bg`, `--danger-overlay-bg` | unchanged | unchanged |

The two high-contrast blocks take their base colours from the table above for every token of
that family, text included, as they do today.

Not by colour alone: every use of these four colours in the panel also has a word or a shape
(`Labs`, the tick in a box, the note's text, `Needs your file`). The worker checks the new
components against that; nothing new may be told by colour only.

## Components and files

New:

| File | Holds |
| --- | --- |
| `desktop/src/EnhancePanel.svelte` | The `<aside>`: head, body, foot. Props `view` (what it reads) and `actions` (what it calls), both typed in `lib/enhancePanel.ts`; `bind:` props for `editPrompt`, `recipeName`, `recipeEditorOpen`, `deletingRecipeId`, `termsAccepted`, `inspectorScroll`, `failureNote`. |
| `desktop/src/Popover.svelte` | Row popover and foot sheet. |
| `desktop/src/FixList.svelte` | Head, rows, popovers, More fixes. |
| `desktop/src/RecipesMenu.svelte` | The menu's content (list, delete question, save field). |
| `desktop/src/PlanSheet.svelte` | Steps with arrows and `Try others…`, then `<RunSettings>`. |
| `desktop/src/OutputSheet.svelte` | Export controls for photo and video. |
| `desktop/src/RunSettings.svelte` | `AdvancedSettings.svelte:468-613` moved as it is; also rendered by `AdvancedSettings` in Edit. |
| `desktop/src/lib/enhancePanel.ts` | `EnhancePanelView`, `EnhancePanelActions`; `mediaFacts`, `outputSummary`. |
| `desktop/src/lib/fixRows.ts`, `planLine.ts`, `jobPlanClient.ts`, `accent.ts` | As specified above. |
| `desktop/src/lib/cvd.ts` | The simulation and the distance, used by the palette test only. |
| `desktop/src-tauri/src/system_accent.rs` | Table and command. |

Changed:

`desktop/src/App.svelte`
- 4552-5454: replaced by `<EnhancePanel {view} {actions} bind:… />`. The handlers stay in
  `App.svelte` (they also serve the library, Start and the downloads); `actions` is one
  constant object of them.
- 1341-1347: `plan` from the client. 96: the `resolvePlan` import goes.
- 2444, 2461, 3409: wait for the plan. 993-1003, 1352-1370: the error gate.
- 1351 `fixChips`: goes.
- 1564-1572 (`onMount`): create the accent controller beside the theme controller; 2559
  `setPreference`: apply it for `accent`, `colour_blind_palette`, `contrast`, `appearance`.
- 5540-5630 (status bar): unchanged.

`desktop/src/AdvancedSettings.svelte`
- Only Edit renders it. The photo and video branches (239-448) and 449-613 move out;
  `RunSettings` and the Save to field are rendered from their new files. The `Format, tiles`
  summary (170) goes; `Size, steps, seed` stays.

`desktop/src/lib/plan.ts`: the `deflicker` stage and the two type literals; `resultFps`.
`desktop/src/lib/steps.ts`: `FIX_CHIPS` (62-65) goes with its test line (`steps.test.ts:29`).
`desktop/src/lib/api.ts`: `systemAccent()`; `'accent'` in `PreferenceName` (151-167) and in the
value union (170-179).
`desktop/src/lib/types.ts`: `accent?` in `UiSettings`. `desktop/src/lib/demo.ts`: `accent:
'localsr'`.
`desktop/src/SettingsSheet.svelte`: the switch row after High contrast (about 470), macOS only.
`desktop/src/styles.css`: token blocks as above; new rules for `.panel-row`, `.panel-key`,
`.panel-head`, `.fix-list`, `.fix-row`, `.fix-value`, `.fix-tag`, `.panel-foot`, `.foot-line`,
`.pop`, `.sheet`; remove `.recipe-grid` (1716-1744), `.chips` and `.chip` (3270-3298) and the
`.plan-row` family (3329-3430) once nothing uses them.

`desktop/src-tauri/src`
- `job_plan.rs:287-547` (`resolve_plan`): the `deflicker` stage.
- `types.rs`: `accent` after `viewer_background` (394) with `default_accent`,
  `deserialize_accent` (pattern: 556, 594) and the default at 620.
- `commands.rs`: `keep_update_switches` (621) copies it; `apply_preference` (796) gets an
  `"accent"` arm like `"contrast"` (832), error `unknown accent`.
- `lib.rs:210-266`: register `system_accent`; `mod system_accent`.

`desktop/e2e/fake-host.ts`
- `plan_job`: `(await import('/src/lib/jobPlanClient.ts')).planLocally(snapshot,
  args.settings, args.options ?? {})`, so the tests run the same hydration as the app. New
  scenario fields `plan?: 'error' | { delayMs: number }` and `systemAccent?: { light: string;
  dark: string }`; `system_accent` returns the latter or `null`. Today an unknown command
  returns `null` (997-998).

Docs: `CHANGELOG.md` under 0.7.0 (the panel, the accent, the colour-blind mode);
`docs/architecture/steps.md` gains a paragraph "A step's row in the panel" pointing at
`lib/fixRows.ts`.

## Tasks

In this order; each is about an hour for a worker and ends with `./local-ci.sh frontend` green
(and `rust` where the host changes). A task updates the vitest and e2e tests that its own
change breaks; the files are listed under Tests. Only the screenshot baselines wait: from T1
on the `@visual` tests differ, and T16 regenerates them once. T1 to T4 are independent of T5
to T7; T8 needs T6; T9 to T15 need T8 and follow each other.

| # | Task | Touches | Done when |
| --- | --- | --- | --- |
| T1 | Blue accent tokens, `--accent-press`, `--accent-text`, `--accent-edge`, the button rules | `styles.css`, `lib/contrast.test.ts` | Token table matches; contrast tests pass with the new pairs. |
| T2 | Colour-blind palette | `styles.css`, `lib/cvd.ts`, `lib/contrast.test.ts` | The four distance tests and the existing contrast tests pass. |
| T3 | Host: `accent` preference, `system_accent` | `types.rs`, `commands.rs`, `lib.rs`, `system_accent.rs` | Host tests below pass. By hand: `defaults read -g AppleAccentColor` with Multicolour set reports no such key. |
| T4 | UI: accent controller, Settings switch | `lib/accent.ts`, `lib/api.ts`, `lib/types.ts`, `lib/demo.ts`, `App.svelte`, `SettingsSheet.svelte`, `fake-host.ts` | vitest and the accent e2e cases pass. By hand in `npm run tauri dev`: change the accent in System Settings, return to the app, the Start button follows. |
| T5 | Plan client | `lib/jobPlanClient.ts` and its test, `jobPlanParity.test.ts` (imports `toHostStage`) | Client tests pass; the fixture is unchanged. |
| T6 | App uses the client | `App.svelte`, `App.test.ts` (transport mocked, see Tests), `fake-host.ts` | Whole suite green; `App.svelte` no longer imports `resolvePlan`. |
| T7 | De-flicker stage | `job_plan.rs`, `lib/plan.ts`, fixture, `plan.test.ts` | Parity passes on the rebuilt fixture in both languages. |
| T8 | Extract `EnhancePanel.svelte`, markup unchanged | `App.svelte`, `EnhancePanel.svelte`, `lib/enhancePanel.ts` | No test changes. In the component, `$: ({ settings, snapshot, … } = view)` and `const { setTask, … } = actions` keep the moved markup as it was. |
| T9 | `Popover.svelte`; head with the Recipes menu; Task row with its tip | `Popover.svelte`, `RecipesMenu.svelte`, `EnhancePanel.svelte`, `styles.css`, `recipes.e2e.ts` | Tests "Head", "Recipes", "Task" pass. |
| T10 | Foot: needs-you (the `.plan-extra` content leaves the Model card), notes, plan line without a sheet, cost line | `lib/planLine.ts`, `EnhancePanel.svelte` | Tests "Foot" pass. The Model card still stands in the body. |
| T11 | Plan sheet: the Model card's list moves into it as it is, with `Change…` still on its rows; `RunSettings` moves in from Advanced | `PlanSheet.svelte`, `RunSettings.svelte`, `AdvancedSettings.svelte`, `plan.e2e.ts`, `advanced.e2e.ts`, `library.e2e.ts`, `themes.e2e.ts` | Tests "Plan sheet" pass, except the line about `Change…`. |
| T12 | Output line and sheet; `AdvancedSettings` for Edit only | `OutputSheet.svelte`, `AdvancedSettings.svelte` and its two test files, `advanced.e2e.ts`, `video-engines.e2e.ts`, `layout.e2e.ts` | Tests "Output" and "Edit" pass. |
| T13 | Section head, Content row, Fix list without popovers | `lib/fixRows.ts`, `FixList.svelte`, `lib/enhancePanel.ts`, `plan.e2e.ts`, `size.e2e.ts`, `video-restore.e2e.ts` | Tests "Fix list" pass; the chips are gone. |
| T14 | Fix popovers; `Change…` leaves the sheet's fix rows | `FixList.svelte`, `PlanSheet.svelte` | Tests "Fix popover" pass. |
| T15 | Result block: head, Size, Quality, model line, Frames, the hand-picked video variant; `Change…` leaves the sheet's upscaler row | `EnhancePanel.svelte`, `PlanSheet.svelte`, `lib/plan.ts`, `size.e2e.ts`, `advanced.e2e.ts` | Tests "Result" and all of "Plan sheet" pass; the Quality cards are gone. |
| T16 | `enhance-panel.e2e.ts`; `screens.e2e.ts` shots; every baseline regenerated and looked at; dead CSS removed; CHANGELOG | `desktop/e2e`, `styles.css` | `npm run e2e` green on Chromium and WebKit at both sizes. |
| T17 | Optional: time on the cost line | `commands.rs` (new `estimate_start(media_ids, input)` on `estimate_request_seconds`), `lib/api.ts`, `EnhancePanel.svelte` | `about 40 s` shows after two measured runs with the same work profile; nothing shows before. |

## Tests

`./local-ci.sh frontend`, `./local-ci.sh rust` and the e2e suite pass at the end.

### Host (`cargo test`)

| Test | Expected |
| --- | --- |
| `settings.rs`, beside the contrast test | `{}` loads `accent == "system"`; `"localsr"` round-trips; `"pink"` loads the default. |
| `commands/tests.rs`, `set_preference_round_trips…` | `("accent", "localsr")` is saved; `"accent": "red"` and `"accent": true` are errors and leave the value. |
| `commands/tests.rs`, `a_full_settings_save_never_changes…` | `accent: "localsr"` survives a full save with defaults. |
| `system_accent.rs` | `accent_pair(None)` is `None`; `Some(4)` is `("#007aff", "#007aff")`; `Some(-1)` Graphite; `Some(5)` `("#953d96", "#a550a7")`; `Some(9)` is `None`. |
| `job_plan.rs`, parity | Passes on the rebuilt fixture. New cases: video with `deflicker` on gives a `deflicker` stage after the primary and before an after-upscale `interpolate`; with interpolation before upscaling it is last; with SeedVR2 and on a photo there is none. |

### vitest

`lib/jobPlanClient.test.ts` (new; `api` mocked)

| Case | Expected |
| --- | --- |
| `hydrateStage` on each kind of stage | the model object from the snapshot; `installed`, `canDownload`, `needsFile`, `sizeBytes` from the host stage even when the snapshot's model says otherwise |
| `hydrateStage`, id not in the snapshot | throws |
| `toHostStage(hydrateStage(x))` over every stage of the parity fixture | equals `x` |
| `planKey`: two snapshots that differ in `runtime.progress` | equal |
| `planKey`: a model becomes installed; a setting changes; `has_audio` changes | each differs |
| Inside Tauri, one `request` | one `planJob(settings, options)`; `resolvePlan` (spied) never runs; state `loading` with `first: true`, then `ready` |
| Three `request`s in one tick | one `planJob`, with the last settings |
| A `request` while one is in flight | the first answer is dropped, a second call goes out when it returns, the state ends on the second plan |
| `request` with the key already shown | no call |
| `planJob` rejects | state `error` with the message and no stages; `retry()` calls again |
| No answer within 5 s (fake timers) | state `error` |
| `settled()` while in flight | resolves with the final state, after it |
| Outside Tauri | `planJob` is never called; the state gets `resolvePlan`'s stages |

`lib/planLine.test.ts`: one case per row of the Plan line table; a moved order (`noise`, then
`blur`) gives `Noise`, `Blur`; an empty plan gives `[]`.

`lib/fixRows.test.ts`

| Case | Expected |
| --- | --- |
| photo, today's registry | ids `blur`, `jpeg`, `noise`, `colour`; labels `Blur`, `JPEG artifacts`, `Noise`, `Colour` |
| video | the same and `deflicker` last, tag `Labs`; `jpeg` reads `Compression` |
| no `faces`, `interpolate`, `audio_denoise` row | holds for both |
| a registry with an added fix step that has `required_feature: 'x'` | the row exists only when the features include `x` |
| two added steps that are not common | `More fixes (2)` case: both hidden until ticked |
| one added step that is not common | it shows; no link |

`lib/accent.test.ts`: every row of the system accent table (text colour, ratio to two decimals,
edge, link); `shade` raises the text's contrast for every row; the controller sets the six
properties when all four conditions hold and removes them when the palette is colour-blind,
the contrast is high, `accent` is `localsr`, or the host returns `null`; it reads the host
again on `focus`.

`lib/contrast.test.ts`

| Case | Expected |
| --- | --- |
| existing pairs, all eight palettes | pass with the new tokens |
| `--on-accent` on `--accent`, `--accent-hi`, `--accent-press` | ≥ 4.5 (≥ 7 in high contrast) |
| `--accent-text` on `--panel`, `--raised`, `--hover` | ≥ 4.5 (≥ 7 in high contrast) |
| `--accent` against `--panel` | ≥ 3 |
| four colour-blind blocks, the rule above | ≥ 17, ≥ 11, ≥ 10, ≥ 11 |
| the same rule on the values of `dc2bb271`, hard-coded | 7.3, 1.4, 2.6, 1.2 within 0.1 (guards the helper itself) |

`lib/plan.test.ts`: the `deflicker` stage cases of the host table; `resultFps('x2', 24)` is 48,
`('fps60', 59.94)` is 59.94, `('fps60', 0)` is null.

`App.test.ts`. First, the transport: `vi.mock('./lib/jobPlanClient', …)` keeps the module and
replaces `fetchPlan` with `async (snapshot, settings, options) => resolvePlan(…)`, so the app
plans against the snapshot each test gave it. Tests that click with `fireEvent` and then read
plan text need one `await tick()` or a `findBy`. Then, by block:

| Block | Case | Expected |
| --- | --- | --- |
| Head | photo selected | no text `Quick · Photo · 4×`; a button `Recipes` |
| | job running | `Locked while running`; no `Recipes` button |
| Recipes | open the menu with one saved recipe | its name and `upscale · 4× · FP16`; click applies it (as today's test) and closes the menu |
| | `Save current as recipe…` | the `Recipe name` field has focus; Enter saves; Escape closes the field |
| | delete | `Delete Phone photos?`, `Confirm delete Phone photos`, `Cancel deleting Phone photos` as today |
| Task | video selected | `Edit` disabled; no caption paragraph; the `About Task` tip holds the video sentence |
| Fix list | photo | group `Fix` with checkboxes `Blur`, `JPEG artifacts`, `Noise`, `Colour` in that order; none checked; no `Faces` |
| | tick Noise | `saveSettings` gets `fix_stages: [{ fix: 'noise', … }]`; the row's value button reads `RealPLKSR Denoise` |
| | tick Blur after Noise | `fix_stages` order `blur`, `noise`; the rows have not moved |
| | Noise's model missing | value `RealPLKSR Denoise · 28 MB` |
| | video | `Compression` and `De-flicker` with `Labs`; ticking De-flicker saves `deflicker: true`; with `video_hdr_mode: 'preserve'` it is disabled |
| | engine without `image_multi_fix`, two ticks | today's note, in the foot |
| Fix popover | click Noise's value | group `Noise options` with `Model`, `Change the noise fix model`, `2 more models`; Escape closes it and focus is on the value button |
| | `Change…` | the library opens on Noise (as today's test) |
| | open Noise's, then Blur's | only Blur's is open |
| | Colour | no `Change…` |
| Result | photo at 4× | head value `6400 × 4268`; radios `Same size`, `2×`, `3×`, `4×`; the model line names the upscaler and `Change the upscaler` opens the library |
| | Same size | Content still there, both radios disabled; no model line; head `1600 × 1067` |
| | Quick, Best | two buttons in group `Quality`; Quick's `title` is `Fast, clean result · 287 MB download` in the fixture of size-decides.md:387 |
| | hand-picked upscaler | neither pressed; the name's `title` is the Chosen-by-you sentence; no `Back to Best` |
| | video, Frames `2× · 48 fps · before upscaling` | saves `video_interpolation: 'x2'`, `video_interpolation_order: 'before_upscale'`; head value ends ` · 48 fps` |
| | SeedVR2 chosen | no Content, Fix or Frames; `Output resolution` options as today; the memory card shows |
| Foot | Noise and Blur at 4× | plan line `Blur → Noise → Upscale 4×`, name `Plan: Blur, Noise, Upscale 4×` |
| | Same size, nothing ticked | the note; plan line `Nothing to run`; Start disabled |
| | a model that needs terms | the tick and the button in the foot under `Upscale · <name>`; Start disabled until ticked (as today's test) |
| | downloads pending | `287 MB to download`; the tip holds the download sentence |
| | `fetchPlan` rejects | `Could not work out the plan.`, `Try again`; Start disabled; `Try again` plans again |
| | `fetchPlan` pending, then Start is clicked | `startJobs` is called once, after the plan resolved |
| Plan sheet | open with Noise and Blur | list `Stages` with three rows; `Move the blur fix later` reorders (as today's test) and the Fix list keeps its order; no `Change…` button inside the sheet |
| | `Try others…` | as today's tests, from inside the sheet |
| | This run | Hardware, Tile, Halo, Precision, Safe memory as `AdvancedSettings.test.ts` checks them today |
| | job running | the sheet opens; its selects are disabled; `Run at low priority` is not |
| Output | photo | line `PNG · Desktop`; the sheet has Format, the metadata tick, Save to; JPEG adds the quality slider and the line reads `JPEG 98 · Desktop` |
| | video | line `MP4 · AV1 · CRF 18 · Desktop`; with Clean up audio `MP4 · AV1 · CRF 18 · sound cleaned · Desktop`; the sheet has Container, Video format, External FFmpeg, CRF, Clean up audio, Audio model |
| | HDR video | `Colour output` in the sheet; choosing Preserve saves what today's test expects |
| Edit | Edit chosen | prompt, model line, Advanced `Size, steps, seed`; no plan line, no Output line |
| Settings | macOS user agent | the accent switch is there; off saves `set_preference('accent', 'localsr')` |
| | Windows user agent | no accent switch |

`AdvancedSettings.test.ts`, `AdvancedHelpTips.test.ts`: the photo and video cases move to
tests of `OutputSheet`, `RunSettings` and the Frames row with the same assertions; the Edit
cases stay.

### e2e (Playwright, `desktop/e2e`)

New `enhance-panel.e2e.ts`

| Case | Expected |
| --- | --- |
| 1024 × 700, photo, no banner | the Size row is fully inside the body's visible area without scrolling |
| 1024 × 700, video with five fix rows | the foot is fully visible; the body scrolls; no horizontal scrollbar in the pane |
| every label and segment at both sizes | `scrollWidth <= clientWidth` (nothing is cut) |
| tick Blur, JPEG artifacts, Noise, Colour one after another | the top of every row and of the Size row is where it was (± 0.5 px); the foot's top moves only when the cost line appears |
| click `Same` | the Size row has not moved |
| Noise's popover on the last visible row | it opens above the row and lies inside the body |
| popover and keyboard | Enter on the value opens it with focus inside; Escape returns focus; Tab leaves it and it closes |
| plan sheet | covers the body's lower part, the foot stays visible; Escape closes |
| scenario `plan: { delayMs: 600 }` | `Working out the plan…` first; after a tick with a plan shown the line gets `aria-busy` and, later, the dimmed style |
| scenario `plan: 'error'` | the error line; Start disabled; after the scenario is switched and `Try again` is clicked, the plan |
| scenario `systemAccent` yellow, `accent: 'system'`, light theme | Start's background `rgb(255, 199, 38)`, its text black, an inset edge; `contrastOnPage(start) >= 4.5` |
| the same with `colour_blind_palette: true`, then with `contrast: 'high'` | the stylesheet's accent |
| `accent: 'localsr'` (the fixtures' default) | `rgb(58, 110, 165)` with white text |
| a ticked Fix box | its fill is the Start button's fill |

Existing files that must change:

| File | What |
| --- | --- |
| `plan.e2e.ts` | All of it: the `chip` helper becomes a checkbox in group `Fix`; `rows` reads the Plan sheet; order and arrows are tested there; the layout test of the Model card rows (64-104) is rewritten for the sheet's rows; `Change…` opens from a fix's popover and from the model line, `Try others…` from the sheet. `Faces is not offered` stays. |
| `size.e2e.ts` | Chips to checkboxes; `Stages` to the plan line; Content at Same size is disabled, not absent (24, 58); the card texts `28 MB download` / `443 MB download` (88-89) become the segments' `title`s. `.field-value` and the radio names hold. |
| `recipes.e2e.ts` | Open `Recipes` first; the outside click (29) needs a new target. |
| `advanced.e2e.ts` | `openAdvanced` (6-10) opens the Plan sheet and waits for `#device`; `openFfmpegSettings` (13-21) opens the Output sheet, whose External FFmpeg row still opens Settings at Storage. The box-style test (53-96) finds `#low-priority` and Safe memory in the Plan sheet. Frame interpolation (144-179) is rewritten for the one select: its option list, `x2:after_upscale` as the value, no `#interpolation-order`. Audio (248-330) reads the Output sheet instead of the region `Audio`. The Settings tests (42, 98-143, 229) and the edit runtime test (205) stay. |
| `video-restore.e2e.ts` | Chips, `Stages`, recipes through the menu. |
| `video-engines.e2e.ts` | 99: no `Advanced` button; the Labs prose is in the Size tip. 23 and 84 hold (the accessible names are kept). |
| `library.e2e.ts` | 320-324: `About Try others` is in the Plan sheet. |
| `layout.e2e.ts` | `the Try others tip wraps inside its box` (314), the two `Advanced:` cases (374, 504), `help tips and the Model card links are at least 24 px targets` (645) are rewritten against the sheets; `pane headers` (26) with the Recipes button; `at 880 px the Enhance page is a centred column` (557) and `while a queue runs` (390) are checked against the new markup. `status bar start buttons` (200-266) must pass unchanged. |
| `themes.e2e.ts` | 128-129 and 140-154 measure `.plan-row` and `.chips .chip`: measure a fix row's label, a value button and `Needs your file` in the foot instead. |
| `keyboard.e2e.ts` | 96-116 must pass as it is: every new control shows a focus ring. |
| `screens.e2e.ts` | Shots renamed and retaken: `panel-photo`, `panel-fixes`, `panel-popover`, `panel-video`, `panel-plan-sheet-mac`, `panel-plan-sheet-windows`, `panel-output-sheet-video` replace `plan-quick`, `plan-fixes` and the three `advanced-*`. |

Screenshot baselines: the accent changes every picture with a Start button, a progress bar or a
banner, so every baseline of the four projects (40 each in WebKit, 41 in Chromium) is
regenerated in T16, not before. Look at each; outside the panel and the accent nothing may
differ.

## Later

Where the tool waves put their parts. None of this is built now.

| Part | Appears when | Where and how |
| --- | --- | --- |
| A new fix (Faces, Exposure, Stabilise, Scratches & dust, Text) | its step is in `steps.json` with `is_fix_stage` or is named in `SETTING_ROWS`, and the engine lists its feature | a Fix row at its phase and order. Add its `title` and, when it is not common, nothing else. |
| A value such as `Medium ▾` | the step declares a `choice` parameter | the row's value is that choice; the popover shows it as a segmented row above `Model`. Built for fix strength (`tools-wave.md`, section 1). |
| `Labs` | the step's model is `support_tier: 'labs'`, or the step is in `LABS_ROWS` | the tag. |
| Hints and `Auto` | a detector feature exists | hint text right of an unticked row in `--accent-text`; the switch right of the Fix head, off by default. Auto never ticks Creative, Text or Colour. |
| `Scan` | the engine lists `video_deinterlace` and the file says `tff`, `bff` or `unknown` (not Content: there is no Tape; see `docs/design/scan-and-background.md`) | a row with a native select under Content. |
| `More ▾` in Content | `content` has more than two values for the media | third segment; a chosen domain takes its place. |
| `Detail` | the catalog has a creative upscaler | a select right of the Quality segments, 120 px. |
| `Backgr.` | a background step exists | last row of Result, `Keep` / `Remove` (`Colour ▾` waits; see `docs/design/scan-and-background.md`). |
| `…` in Size | the host takes a long-edge target for photos | fifth segment; the label `Same` is already short for it. |
| Mixed batch | the host takes per-file plans | the checkbox's `indeterminate` state with `14 of 30` as the value, a click on it opens `For all 30: On / Off`; the plan line reads `3 different plans`. |

## What may not work as drawn

- **The fold.** With the board's order, Size and Quality are under the Fix list. By the row
  heights above, the photo panel needs about 560 px with its head and foot, and a 700 px window
  leaves the pane about 590; a video needs about 630 px and scrolls, and with the GPU-engine banner showing (about 100 px)
  so does a photo. The body scrolls and the foot stays, but Size can be below the edge. The
  board's Tweak "Size above Fix" is the cure and is a reordering of two blocks.
- **Popovers inside a scrolling column** are clipped by it. They flip above the row when
  needed and are 230 px wide, so they fit; a popover taller than the body cannot fit and gets
  its own scrolling.
- **The accent table** is a measurement of one macOS version. Apple can change the eight
  colours; then a button is a shade off until the table is updated. Reading the live colour
  needs an Objective-C call from the host, which this spec avoids.
- **`defaults` in a sandboxed build.** If LocalSR is ever sandboxed for the Mac App Store,
  running `/usr/bin/defaults` may be refused; the command then returns `None` and the app is
  blue.
- **The fake host imports a source module** (`/src/lib/jobPlanClient.ts`) from the Vite dev
  server. If WebKit refuses the dynamic import from an init script, the fallback is to build
  `planLocally` into the page as `window.__planLocally` from `main.ts` in dev mode only.
- **Black text on system Blue** is correct by contrast and unusual on a Mac, where the same
  blue carries white text.
- **Multicolour was not measured.** That the `AppleAccentColor` key is absent under
  Multicolour is Apple's convention; the Mac used here has Pink set. T3 checks it once by hand.

## Outside this change

- `plan_job` returning the start request too, and Start sending the host's own
  (`JobPlan.start_input`, `job_plan.rs:95-98`).
- The logo, the app icon and the site keep the terracotta pixel; a brand change is its own
  decision.
- The header of the Viewer pane and the other panes are not restyled; only links inside the
  Enhance panel take `--accent-text`.
- Edit's own layout.

## Open questions

Only where the board and the code disagree. The spec builds the first answer of each.

1. **Order of the Fix rows.** Engine order (Blur, JPEG artifacts, Noise), so the list matches
   the plan line, or as drawn (Noise, Blur, JPEG artifacts), today's chip order, with the plan
   line alone showing the run order?
2. **Start button.** In the bottom bar under the panel, where Cancel and the queue buttons
   share its place, or inside the panel's foot as drawn?
3. **Content at Same size.** Visible and greyed out, so no row moves, or hidden as you settled
   in 0.6.0?
4. **Multicolour on macOS.** LocalSR's blue with white text, or the system blue with black
   text?
5. **A ticked row's value.** Answered in 0.7.0 (`tools-wave.md`, section 1): the strength on Blur, JPEG artifacts and Noise, else the model name. Was: the model name until fixes have strengths, or nothing but the
   `▾`?
