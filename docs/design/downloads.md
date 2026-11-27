# Downloads

## Amendments (2026-11-27, after the second design by Sol and the coordinator's review)

This spec is the base. These points change or settle it; where they disagree with the text below, they win.

1. **Open questions settled with defaults** (Hermes can still change them): speed only in the tooltip; the foot's `Download model` button is removed; Cancel keeps the `.part` and the next download continues, as today; a dropped connection is retried by the host for up to about 10 minutes with growing waits (5 s, 10 s, 20 s, 40 s, then every 60 s), keeping the `.part`, while the line reads `Waiting for the connection…`. This adds task **D4b** (host retry policy, `downloads.rs`, with host tests: retries continue after a short outage and resume from the `.part`; gives up after the limit with reason `connection`).
2. **Units:** `formatBytes` (`src/lib/format.ts`) divides by 1024 but writes `MB`/`GB`. Sizes become decimal (1 MB = 1,000,000 bytes), as macOS Finder and the catalog sizes count them. This is task **D0** (before D1): change the function and update the tests and screenshots that show sizes.
3. **Several downloads:** keep the single status-bar line, but its tooltip lists every model with its own state and size, and the library tray stays the per-model place with one Cancel each (Sol proposed a separate drawer; the tray already is that list).
4. **Engines and app updates** use the same size/time function (D10) and the same failure wording by cause where the cause is the same; their banners stay where they are.

Build spec for LocalSR 0.7.0. It follows `enhance-panel.md` and changes how a download is
shown: model downloads first, engine and app-update downloads with the same wording.

Line numbers refer to `8dcbb2b6` on `w/070-enhance-panel`: the panel through T16 (`3baa4274`)
with `rc/0.7.0` merged in. Every reference also names its function, selector or text. Paths
are relative to `desktop/` unless they start with `docs/`.

Hermes, 2026-11-27, on a screenshot of one Edit-model download: "the download UI here is
terrible it shows it in two places and the checked against checksum is like noone care duh".

Four things change:

1. **A running download shows in one place: the status bar.** The panel and the library stop
   repeating the percentage.
2. **The line says what people ask**: which model (its real name), how much (`454 MB of
   4.9 GB`), how long (`about 6 min left`). The checksum sentence goes.
3. **Verification is a word, not a paragraph**: `Verifying…` while it runs, and one sentence
   behind the `?` on the cost line.
4. **A failed download is a line in the status bar, not a dialog**, and the Start button is
   the retry.

The worker protocol does not change. The host gains two optional fields on a download's status
and progress event, three on the failure event, and publishes progress once a second. No
setting changes.

## Today

The screenshot shows the panel before its rebuild (the Model card). The same download on this
branch shows in these places:

| # | Place | Shows | Code |
| --- | --- | --- | --- |
| 1 | Foot of the panel, "needs you" | `Edit · FLUX.2 klein 4B`, a full-width button `Downloading 9% · Cancel`, a thin bar. The block appears only because a download runs. | `stageNeedsUser`, `Boolean(stageDownload)`, `src/EnhancePanel.svelte:215-221`; button `:814-828`; bar `:830-835` |
| 2 | Foot, cost line | `4.9 GB to download ?`, unchanged while 9 % of it is already there | `src/EnhancePanel.svelte:984-995`; `pendingDownloads`, `src/lib/plan.ts:422-437` |
| 3 | A ticked Fix row | `RealPLKSR Denoise · 42%` or `· Waiting` | `fixRowValue`, `src/lib/fixRows.ts:158-163` |
| 4 | Plan sheet row | `42%` or `Waiting` | `src/PlanSheet.svelte:147-151` |
| 5 | Status bar, title | `Downloading flux2_klein_4b_q4_0 · 9%` | `src/App.svelte:4738-4739` |
| 6 | Status bar, second line | `Checked against its pinned checksum before use.` | `src/App.svelte:4755-4758` |
| 7 | Status bar, strip | the bar's own 3 px progress line | `src/App.svelte:4800-4809` |
| 8 | Status bar, button | `Cancel download`, only when Start began the download | `src/App.svelte:4858-4861` |
| 9 | Library, when open | the same download three times: the tray (`42%`, bar, `Cancel`), the list row (`42%`), the detail pane (`Downloading 42% · Cancel`, bar) | `src/ModelLibrary.svelte:623-669`, `:765`, `:497-501` with `:1780-1789` |

So one download has two percentages, two bars and two Cancel buttons in the main window, and
three, two and two in the library. In the screenshot the Model row also carried `9%`; on this
branch the Edit card's state shows only `Needs your file` (`src/EnhancePanel.svelte:687-689`).

**Why the title shows `flux2_klein_4b_q4_0`.** That is the edit model's `model_id`
(`src/localsr/core/edit_catalog.json`; its name there is `FLUX.2 klein 4B · Q4_0`). The status
bar looks a download's name up in `catalog.models`, `catalog.video_models` and
`catalog.audio_models` only (`src/App.svelte:819-827`). An edit model is in `snapshot.edit.models`
and an interpolation model in `catalog.interpolation_models`, so both miss, and
`downloadSummary` falls back to the id (`?? item.model_id`, `src/lib/downloadSummary.ts:32`).
The library has its own lookup that does include them (`modelLabel`,
`src/ModelLibrary.svelte:443-453`).

**What nothing shows.** Size, speed and time left. The host already sends the bytes
(`downloaded_bytes`, `total_bytes`, `src-tauri/src/types.rs:716-724`; event `download-progress`,
`src-tauri/src/downloads.rs:1066-1075`), and the summary uses them for the percentage only
(`src/lib/downloadSummary.ts:19-25`).

**What the host has and has not** (`src-tauri/src/downloads.rs`):

| | Today |
| --- | --- |
| States | `queued` and `downloading` (`DownloadPhase`, `:41-44`). Up to 3 run at once, the rest wait (`MAX_CONCURRENT_DOWNLOADS`, `:31`). |
| Bytes | Done and total per model (`:46-55`). Published every 4 MiB and at the end of a file (`:907-918`), without a `state-changed` (test `:1441`). |
| Speed, time left | None. |
| Verifying | Each file is hashed after it arrived (`:815-818`); files and partial files already on disk are hashed before (`:713`, `:759`, `:767`). No state says so: the bar stands still. |
| Retry | Four attempts per file, 1, 2 and 3 s apart (`:777-806`). A silent server ends an attempt after 45 s (`STALL_LIMIT`, `:1094`). No state says so. |
| Resume | A cancel or a lost connection keeps the `.part` file, and the next download continues it with a range request (`:844-896`). |
| Cancel | Reported as `Download paused` / `The verified download can resume later.` (`:432-435`). |
| Success | `Model ready` / `Checksum verification succeeded.` (`:427-430`). |
| Failure | `Download failed` and the raw error text (`:437-440`); event `download-failed` with `error_message` only (`:237-242`). |

Start downloads its models one after another (`downloadModels`, `src/App.svelte:2424-2445`), so
with two missing models the bar runs to 100 % twice and never shows the total the button
promised (`Download 96 MB, then upscale`).

## Decisions

| Point | Decision | Reason |
| --- | --- | --- |
| The one place | The status bar: its title, its second line, its strip and one Cancel button. | It is where the user pressed `Download 4.9 GB, then edit`, and where the job that follows reports. A download that Start began is the first step of that run. The bar is in view on every page of the narrow layout, where the Enhance pane is hidden (`class:compact-hidden`, `src/EnhancePanel.svelte:294`), and it does not change when another file or model is selected. Nothing new is built: the bar has the title, the detail line and the strip already. |
| Not the panel | The panel shows the plan, not activity. | `enhance-panel.md`: rows never move, no extra visible prose. A bar or a button that appears in the foot for the length of a download breaks both. |
| Not the button | Start does not turn into a progress button. | Its place is needed for Cancel, and its label is already the longest in the bar (`e2e/layout.e2e.ts:201-202`). |
| Panel while downloading | Nothing counts or moves. The "needs you" block no longer appears for a running download. A Fix row and a Plan sheet row show the model's name without `42%` or `Waiting`. The cost line counts only what is not downloading yet and hides at zero. | One place. The size on a row (`· 28 MB`) is a fact about a missing model; once the download runs, the status bar has the fact. |
| Download button in the foot | Removed. The block keeps the licence tick and `Choose externally downloaded checkpoint…`. | Once the tick is set, Start reads `Download 28 MB, then upscale` (`prepareReady`, `src/App.svelte:1385-1387`), 30 px below a second button that does half of it. Downloading without running stays in the library. See Open questions. |
| Library | The tray at the top is its one place: name, size and time, bar, Cancel. A list row shows the word `Downloading` or `Waiting`. The detail pane's button turns into a disabled `Downloading…`. | The tray exists for several downloads at once and lists models from every group (`src/ModelLibrary.svelte:623-690`). The library is modal, so the status bar behind it is dimmed and out of reach; it is the same line, not a second one. |
| Name | The name the panel and the library use, never an id. Edit models: the family, `FLUX.2 klein 4B`; in lists with the variant, `FLUX.2 klein 4B · Balanced`. One function for every surface. | The bug above. Unknown id: `a model`. |
| Size | `454 MB of 4.9 GB`; with the same unit `1.2 of 4.9 GB`; before the first bytes `4.9 GB`. `formatBytes` as everywhere (`src/lib/format.ts:1-9`). | The Start button said `4.9 GB`; the line continues that number. |
| Percentage | In the status bar's title, as a job has it (`· 9%`). Nowhere else for a model. | The title is read at a glance; the strip is 3 px. Lists show size and time, which say more than a second percentage. |
| Time left | `about 6 min left`, in words and whole minutes. Shown once the speed has been measured over 3 s, held so it does not flip between two values, hidden while nothing arrives. | It is the question behind a 5 GB download. Minutes are what a network speed can honestly claim. A job's `ETA ≈ 0:45` counts down in seconds because the engine measures tiles. |
| Speed | Not in the line. In the tooltip of the line (`12 MB/s`). | Time left already contains it, and it is a second number that changes every second. See Open questions. |
| Several at once | One line: `Downloading 3 models · 41%`, the sum of the sizes, one time. The names with each one's state are the tooltip. Per model: the library's tray. Start asks for all its models at once. | One bar and one number that match the button's total. The host runs three at a time and queues the rest. |
| Verification | The word `Verifying…` in place of the time, only when it lasts a second or more. The explanation is one sentence in the cost line's `?` tip and the row `Integrity` in the library (`src/ModelLibrary.svelte:1280-1281`), which exists. | "No one cares" while it works. A stage name explains a bar that stands at 100 %. |
| Cancel | One button in the status bar whenever the bar shows a download: `Cancel download` (`Cancel downloads` for several). In the library, one `Cancel` per tray item. | One per window. |
| Pause | No separate control. Cancel keeps what was downloaded and the next download continues; the button's tooltip and the line after a cancel say so. | The host already works this way (`src-tauri/src/downloads.rs:844-896`). A Pause next to a Cancel that does the same would be two buttons for one action. |
| Failure | A title and one sentence in the status bar, in plain words by cause. No dialog. The Start button comes back with its label and is the retry. In the library the failed item gets `Try again`. | The dialog shows the raw error (`showModal('Download failed', String(error))`, `src/App.svelte:2439`, `:3426`) and has to be closed before the retry. |
| Connection lost | While nothing arrives for 8 s: `Waiting for the connection…` in place of the time. When the host gives up: the failure line, which says how much is kept. | The host waits up to 45 s and retries by itself; the user should see that it is waiting, not stuck. |
| Start is waiting | The second line ends with what follows, in the button's own words: `· then edit`, `· then upscale all 52`. Cancel takes the start slot, as today. | The user pressed `Download 4.9 GB, then edit`; the line keeps the second half of the promise in view. |
| A job is running | The bar stays the job's. A download gets one extra line, `Also downloading RealPLKSR Denoise`. Cancel for it is in the library. | Rare (a download started from the library during a run). Today it is invisible outside the library (`src/App.svelte:817-818`). |
| Finished | `Model ready` / `Downloaded and checked.` | Says the same without "checksum". |
| Engine and app update | Their second line uses the same function: `1.2 of 2.4 GB · about 3 min left`. Their places, buttons and stage texts stay. | Same words for the same thing. They are different downloads with their own banners; moving them is not needed. |
| Where speed is computed | In the window, from the bytes, in one small module that all three kinds of download use. | Engine and update status already arrive several times a second with bytes (`src-tauri/src/engine_manager.rs:887-898`, `src-tauri/src/updates.rs:1218-1221`). Computing it in three places in the host is three times the work. |

## Layout

The status bar, full width. `▓░` is its strip (`.progress`, `styles.css:2698-2712`), `●` the
status dot, `[ ]` a button.

### Before: a model is missing

```
│ (Edit: prompt, Model card, Advanced)                 │
╞══════════════════════════════════════════════════════╡
│                                 4.9 GB to download ? │
└──────────────────────────────────────────────────────┘
────────────────────────────────────────────────────────────────────────────────────────────────
 ● Ready                                                          [ Download 4.9 GB, then edit ]
   2000 × 1560 → 1024 × 800 · Apple GPU
```

### One model downloading, Start not pressed

Started in the library, which is closed again. The cost line is gone because nothing is left to
start; the foot shows no box when it is empty.

```
▓▓▓▓▓▓▓▓▓░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
 ● Downloading FLUX.2 klein 4B · 9%                      [ Cancel download ] [░ Edit selected ░]
   454 MB of 4.9 GB · about 6 min left
```

Start is disabled with the tooltip `Wait for the download to finish.`

### While a job waits for it

After `Download 4.9 GB, then edit`. Cancel sits where Start was.

```
▓▓▓▓▓▓▓▓▓░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
 ● Downloading FLUX.2 klein 4B · 9%                                          [ Cancel download ]
   454 MB of 4.9 GB · about 6 min left · then edit
```

In the first three seconds the line reads `454 MB of 4.9 GB · then edit`; before the first
bytes `4.9 GB · then edit`.

### Several at once

A photo with Blur and Noise ticked and no model installed, after `Download 322 MB, then
upscale`.

```
▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
 ● Downloading 3 models · 41%                                               [ Cancel downloads ]
   132 of 322 MB · less than a minute left · then upscale

   tooltip of the second line:   NAFNet GoPro Deblur · 80%
                                 RealPLKSR Denoise · 12%
                                 LocalSR Photo S · 33%
                                 4.2 MB/s
```

The panel above it, before and during. No row moves; the two sizes and the cost line go.

```
 before                                                  during
│ [✓] Blur               NAFNet GoPro Deblur · 68 MB ▾ │ │ [✓] Blur                       NAFNet GoPro Deblur ▾ │
│ [✓] Noise                RealPLKSR Denoise · 28 MB ▾ │ │ [✓] Noise                        RealPLKSR Denoise ▾ │
╞══════════════════════════════════════════════════════╡ ╞══════════════════════════════════════════════════════╡
│ Blur → Noise → Upscale 4×                          ▸ │ │ Blur → Noise → Upscale 4×                          ▸ │
│ Output     PNG · Desktop                           ▸ │ │ Output     PNG · Desktop                           ▸ │
│                                 322 MB to download ? │ └──────────────────────────────────────────────────────┘
└──────────────────────────────────────────────────────┘
```

### Verifying

```
▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
 ● Downloading FLUX.2 klein 4B · 100%                                        [ Cancel download ]
   Verifying… · then edit
```

Between two files of a model with several files the bytes stay: `3.0 of 4.9 GB · Verifying…`.
The strip keeps its moving shine (`.progress i::after`, `styles.css:2720`), so the bar is not
still.

### Nothing arrives

```
▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
 ● Downloading FLUX.2 klein 4B · 24%                                         [ Cancel download ]
   1.2 of 4.9 GB · Waiting for the connection… · then edit
```

### Failed

No strip. The Start button is back and is the retry; the download continues at 1.2 GB.

```
────────────────────────────────────────────────────────────────────────────────────────────────
 ● Download interrupted                                           [ Download 4.9 GB, then edit ]
   No connection to the model server. FLUX.2 klein 4B is kept at 1.2 of 4.9 GB and
   continues from there.
```

### A job runs and a model downloads

```
▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
 ● (the job's title) · 31%                                  [ Add selected to queue ] [ Cancel ]
   (the job's detail line, unchanged)
   Also downloading RealPLKSR Denoise
```

### Library

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ Model library                                                                │
├──────────────────────────────────────────────────────────────────────────────┤
│ Downloads · 2 active · 1 waiting                                             │
│ FLUX.2 klein 4B · Balanced   454 MB of 4.9 GB · about 6 min left  [ Cancel ] │
│ ▓▓▓▓▓▓▓░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░ │
│ RealPLKSR Denoise                                    12 of 28 MB  [ Cancel ] │
│ ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░ │
│ HAT-L ×4                                        Waiting · 159 MB  [ Cancel ] │
│ SwinIR ×4 Real                                     [ Try again ] [ Dismiss ] │
│   No connection to the model server. Kept at 20 of 64 MB.                    │
├──────────────────────────────────────────────────────────────────────────────┤
│ (list)   FLUX.2 klein 4B · Balanced                      4.9 GB  Downloading │
│          FLUX.2 klein 4B · Full detail                8.3 GB  Not downloaded │
│ (detail) …  Integrity  SHA-256 checked before use                            │
│          [░░░░░░░░░ Downloading… ░░░░░░░░░]                                  │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Engine and app update

Their banners and dialogs keep their layout. Only the text of the running download changes:

```
 engine banner     Downloading NVIDIA engine · 50%                              [ Cancel ]
                   1.2 of 2.4 GB · about 3 min left
                   ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░░░░░░░░░░░░░
 engine dialog     NVIDIA CUDA   ▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░                           [ Cancel ]
                   1.2 of 2.4 GB · about 3 min left
 update banner     Downloading LocalSR 0.7.1…                                          36%
                   112 of 310 MB · about 1 min left
                   ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░░░░░░░░░░░░░░░░░░░
 update dialog     ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░░░░░░░░░░░░░░░░░░░
                   112 of 310 MB · about 1 min left                   [ Cancel download ]
```

## Copy

Every string that is new or changes. Strings not listed stay.

| Where | String |
| --- | --- |
| Status bar title, one model | `Downloading FLUX.2 klein 4B · 9%` |
| Status bar title, several | `Downloading 3 models · 41%` (running and waiting ones are counted) |
| Second line, size | `454 MB of 4.9 GB` · same unit `1.2 of 4.9 GB` · nothing received yet, or all of it `4.9 GB` · size unknown: no size |
| Second line, time | `less than a minute left` · `about 6 min left` · `about 1 h 20 min left` · `about 4 h left`; absent until measured |
| Second line, nothing arrives | `Waiting for the connection…` in place of the time |
| Second line, verifying | `Verifying…` in place of the time; alone when every byte is there |
| Second line, Start is waiting | ` · then edit` · ` · then upscale` · ` · then restore` · ` · then start video` · ` · then upscale all 52`: the words after `then` on the button that was pressed (`src/App.svelte:4875`, `:4883`) |
| Second line, tooltip | one model: `12 MB/s`; several: a line per model, `NAFNet GoPro Deblur · 80%`, `RealPLKSR Denoise · verifying`, `HAT-L ×4 · waiting`, then the speed |
| Strip, `aria-valuetext` | `9%, 454 MB of 4.9 GB, about 6 min left` |
| Cancel | `Cancel download` · `Cancel downloads` when more than one still runs or waits · after the click `Cancelling…` (as today, `:4860`) |
| Cancel, `title` | `Stops the download. What is already downloaded is kept, and the next download continues from there.` |
| Start while a model of its plan downloads, `title` | `Wait for the download to finish.` |
| Extra line while a job runs | `Also downloading RealPLKSR Denoise` · `Also downloading 2 models`; its `title` is the second line as above |
| After a cancel (host) | `Download stopped` / `What was downloaded is kept. The next download continues from there.` (was `Download paused` / `The verified download can resume later.`) |
| After success (host) | `Model ready` / `Downloaded and checked.` (was `Checksum verification succeeded.`) |
| Failed, connection, part kept | `Download interrupted` / `No connection to the model server. FLUX.2 klein 4B is kept at 1.2 of 4.9 GB and continues from there.` |
| Failed, connection, nothing kept | `Download interrupted` / `No connection to the model server. Check the internet connection and start again.` |
| Failed, server refuses | `Download failed` / `The model server did not deliver FLUX.2 klein 4B. Try again later.` |
| Failed, file damaged | `Download failed` / `FLUX.2 klein 4B arrived damaged and was removed. Start again to download it once more.` |
| Failed, disk full, folder missing, anything else | `Download failed` / the host's sentence, first letter in capitals, with a full stop (`not enough disk space: the download needs about 5630 MB free, but only 2100 MB is available`, `src-tauri/src/downloads.rs:374-378`) |
| Failed, more than one | the first failure's two lines, and ` 2 more failed.` at the end of the second |
| Failed and not part of the plan | the link `Open library` after the sentence |
| Failure line, `title` | the host's error text |
| Cost line | `322 MB to download`, counting only models that are not downloading yet; no line at zero |
| Cost tip | `Models download once when you start and stay on this computer. Each file is checked before it is used. Your photos never leave it.` (`videos` on a video) |
| Fix row value | missing `RealPLKSR Denoise · 28 MB` · downloading or waiting `RealPLKSR Denoise` (removed: `· 42%`, `· Waiting`) |
| Plan sheet row, state | empty while downloading (removed: `42%`, `Waiting`); `Needs your file` stays |
| Needs-you block | removed: `Download model · 28 MB`, `Downloading 9% · Cancel`, `Waiting in queue · Cancel` and the bar |
| Library tray item | `454 MB of 4.9 GB · about 6 min left` (was `9%`); `Waiting · 159 MB` stays |
| Library tray, failed | the failure sentence without the model's name: `No connection to the model server. Kept at 20 of 64 MB.`; buttons `Try again` (accessible name `Try SwinIR ×4 Real again`), `Dismiss` |
| Library row state | `Downloading` · `Waiting` (was `42%`, `Waiting`); `Failed`, `Installed`, `Not downloaded` stay |
| Library detail button | `Download 4.9 GB` · while it runs, disabled: `Downloading…` · `Waiting…` (removed: `Downloading 42% · Cancel`, `Waiting in queue · Cancel`, the bar) |
| Engine banner | `Downloading NVIDIA engine · 50%` and below `1.2 of 2.4 GB · about 3 min left` (was `Downloading NVIDIA engine… 1.2 GB of 2.4 GB (50%)`) |
| Engine dialog | `1.2 of 2.4 GB · about 3 min left` (was `Downloading… 1.2 GB of 2.4 GB (50%)`) |
| Update banner | under `Downloading LocalSR 0.7.1…`: `112 of 310 MB · about 1 min left` |
| Update dialog | `112 of 310 MB · about 1 min left` (was `112.0 MB downloaded`) |

Not shown anywhere any more: `Checked against its pinned checksum before use.`

## Behaviour

### Who owns the status bar

`src/App.svelte:4731-4760` (title and second line), `:4791-4809` (strip), `:4810-4900` (buttons).
First match wins:

| State | Title | Second line | Strip | Buttons |
| --- | --- | --- | --- | --- |
| Starting, stopping, saving | as today | as today | as today | as today |
| A job runs | the job's | the job's; one more `.status-note` line when a model downloads | the job's | the job's |
| No job, a model downloads or waits | the download line | size, time, `then …` | the download's | `Cancel download`; the start buttons too unless Start is waiting |
| No job, no download, a failure is remembered | the failure's | the failure's sentence, `role="alert"` | none | Start, as the plan allows |
| Otherwise | the host's `status_title` | as today | none | as today |

- The download line is computed always, also while a job runs (today it is `null` then,
  `:817-818`); the job only decides where it is drawn.
- The second line of a running download carries no live-region role: it changes every second.
  The strip is the `progressbar` and gets `aria-valuetext`.
- `Cancel download` when Start is waiting (`preparing`): in the start slot, `button danger
  start`, as today (`:4858-4861`). It cancels every download Start asked for. Otherwise: a plain
  `button` left of the start buttons; it cancels every download that runs or waits. A download
  that has just finished answers a cancel with an error (`that model is not being downloaded`,
  `src-tauri/src/downloads.rs:499`); that error is ignored and no dialog opens.
- While a model of the current plan downloads without Start waiting, the start button is
  disabled as today (`modelReady`, `src/App.svelte:980`) and its `title` is `Wait for the
  download to finish.` (`singleHint`, `:1518`; the same sentence first in `startBlockReason`,
  `:1561`, for File › Start).
- A remembered failure is cleared when any download is queued or started and when a job starts.

### The download line

`lib/downloadSummary.ts` keeps one **run**: it begins with the first download and ends when no
download is left.

- Each model counts with `total_bytes`, or its catalog size while that is 0, and
  `downloaded_bytes`. A waiting model counts with 0 done.
- A model that finishes inside a run stays in the sums as complete. So the percentage never
  goes back when one of three finishes (today it does: `downloadSummary` sums the current list
  only, `:18-31`). A cancelled or failed model leaves the sums.
- Percentage: done over total, rounded as today. When a size is unknown: the mean of the
  models' percentages, and no size in the second line (today's fallback, `:27-31`).
- Title: one model in the run: `Downloading <name>`; more: `Downloading <n> models`.
- Verifying: the run is verifying when no model is receiving and at least one is being hashed.

### Size, speed and time

`lib/downloadProgress.ts`, pure; time is passed in, in seconds, as `eta-display.ts` does.

- **Size.** `progressBytes(done, total)`: `formatBytes` of both; the first unit is dropped when
  both are the same. `done <= 0` or `done >= total` gives the total alone; a total of 0 gives
  what is done alone.
- **Speed** is measured on `received_bytes`, the bytes that came over the network in this run.
  A resumed download starts with its old part already counted in `downloaded_bytes`, and files
  found on disk are added in one step (`src-tauri/src/downloads.rs:713-716`); neither is
  speed. Without the field (engine, update, an older host) `downloaded_bytes` is used.
  - Samples are kept for 10 s. Two samples less than 0.5 s apart: the later replaces the
    earlier.
  - Speed is the last sample minus the first over their distance, once they are 3 s apart.
    Before that there is none.
  - Bytes going down, or the phase leaving `downloading`, clears the samples.
- **Nothing arrives.** Phase `downloading`, at least one sample, none for 8 s: no speed, and the
  time is replaced by `Waiting for the connection…`. The host publishes at least once a second
  while data arrives, so 8 s of silence is a real pause. `App.svelte` already ticks once a
  second (`etaTimer`, `:1013-1021`); the same tick redraws the line.
- **Time left** is the bytes missing over the speed, shown through a label that holds:

  | Seconds | Text | Step |
  | --- | --- | --- |
  | under 45 | `less than a minute left` | |
  | under 59.5 min | `about N min left`, N rounded, at least 1 | 60 s |
  | under 3 h | `about 1 h 20 min left`, minutes to 5; `about 2 h left` at 0 | 300 s |
  | more | `about 4 h left` | 3600 s |

  A shown value changes only when the new one is more than 0.7 of its step away, the rule
  `EtaLabel` uses (`HOLD`, `src/lib/eta-display.ts:116-129`).
- **Verifying** is shown once the phase has been `verifying` for 1 s. A 28 MB model is hashed
  in a few milliseconds and never shows it.
- Several models: the run's sums feed one measurement. Each model also has its own, for the
  library's tray.

### Start with missing models

`downloadModels` (`src/App.svelte:2424-2445`) asks for all its models at once and waits for all
of them (`Promise.allSettled`). The host runs three and queues the rest.

- `prepareAndStart` (`:2465-2481`) and `startScope` (`:2485-2496`) note the words after `then`
  before the first request; the second line shows them while `preparing`.
- `cancelPreparation` (`:2447-2463`) cancels every model of the set, with today's retry for a
  download the host has not registered yet (`:2450-2462`).
- All succeeded and nothing was cancelled: `start()`, as today (`:2479-2480`).
- One or more failed: nothing starts, the failure is remembered, no dialog. Models that did
  arrive stay installed.
- Settings are not locked while a download runs (`settingsLocked`, `:643-647`). If the plan
  changed so that Start is no longer possible, the downloads finish and nothing starts; that is
  today's behaviour (`if (canStart)`, `:2480`).
- Start cannot begin downloads while any download runs (`prepareReady`, `:1373`), so the set
  never contains a model that is already downloading. A plan whose models are all installed
  can still be started then; that is the state "a job runs and a model downloads".

### Failure and retry

- The host names the cause in the `download-failed` event (`reason`, below). The window keeps
  per model: reason, the model's name, the bytes kept and the total, the host's text.
  `downloadErrors` (`src/App.svelte:149`) stays as it is for the library; the new record is
  beside it and filled from the same event (`:1952-1959`). A rejected `download_model` call
  with no event before it (`:3416-3424`) records reason `other`.
- Status bar: the failure line. The dialog `Download failed` is not opened any more
  (`:2439`, `:3425-3427`); `Import failed` for a file the user chose stays a dialog.
- Retry from the main window is the start button with its usual label. When the failed model is
  not among the plan's downloads, the sentence ends with the link `Open library` (a
  `link-button` in the status text, as the hardware hint has one, `:4765-4771`), which opens the
  library on that model (`openLibrary('browse', undefined, modelId)`, `:2319-2323`).
- Library: the failed item shows the sentence, `Try again` and `Dismiss`. `Try again` sends the
  same request again, with the licence answer the first request carried (kept per model when
  `runDownload` sends it, `:3413`).
- A retry continues the kept part. A damaged file was removed by the host and starts at 0.

### Panel

`src/EnhancePanel.svelte`, `src/lib/fixRows.ts`, `src/PlanSheet.svelte`.

- `stageNeedsUser` (`:207-230`): a stage whose model is downloading or waiting needs nothing
  from the user and returns `false`; `Boolean(stageDownload)` (`:220`) goes.
- The block for a missing model (`:803-835`): the licence tick stays (`:804-812`). The button
  stays only for a model LocalSR may not fetch (`!stage.canDownload`), where it reads `Choose
  externally downloaded checkpoint…` and opens the file dialog as today. The `<progress>`
  (`:830-835`) goes.
- Cost line (`:984-995`): bytes of `downloads.stages` whose model is not in `downloadMap`; no
  line at 0. `downloads` itself is unchanged, because Start and its label read it
  (`src/App.svelte:1381`, `:2470`, `:4875`).
- With the cost line gone in Edit the foot can be empty. It must then draw nothing
  (`.panel-foot:empty`, `styles.css:1085-1087`); the worker checks that no white space is left
  inside it.
- `fixRowValue` (`src/lib/fixRows.ts:158-163`): a model in `downloadMap` gives the name alone.
- `PlanSheet.svelte:147-154`: no text for a model in `downloadMap`.

### Library

`src/ModelLibrary.svelte`.

- Tray item (`:637-668`): the meta text is the model's own second line; `Waiting · 159 MB`
  stays. Names come from the shared function with the variant.
- Failed item (`:670-688`): the plain sentence, `Try again`, `Dismiss`.
- List rows (`:765`, `:793`, `:934`, `:1123`): `Downloading` in place of the percentage.
- Detail pane: `downloadButtonLabel` (`:497-501`) gives `Downloading…` or `Waiting…`, and the
  button is disabled while its model is in `downloadMap`. Its five callers (`:1381`, `:1455`,
  `:1528`, `:1677`, `:1780`) lose their `<progress>` (`:1383-1389`, `:1457-1463`, `:1530-1536`,
  `:1679-1685`, `:1782-1788`).

### Engine and app update

Each of the four places keeps one measurement of its own and shows its text:

- `src/EngineRecommendationCard.svelte:41-48`: the title with the percentage, then the line.
  `Unpacking and verifying … engine…` and `Checking … engine…` (`:49-53`) stay.
- `src/EngineManagerDialog.svelte:143-150`: the line in place of `Downloading… X of Y (N%)`.
- `src/UpdateNotice.svelte:74-76`: the line under the title; the `36%` on the right (`:128-130`)
  stays.
- `src/UpdatePanel.svelte:105-107`: the line in place of `112.0 MB downloaded`. Its button is
  already `Cancel download` (`:141`).

No `Waiting for the connection…` and no `Verifying…` here: these hosts send no phase, and
their own stage texts cover it.

### Look

No new colour, token, icon or component. The strip, the two text lines, `.button`,
`.link-button`, `.download-progress` (`styles.css:2166-2193`) and `HelpTip` are used as they
are. Numbers that change keep `font-variant-numeric: tabular-nums` where the element has it.

## Host

`src-tauri/src/downloads.rs`, `src-tauri/src/types.rs`. Fields are added, none changes or goes.

| Where | Field | Value |
| --- | --- | --- |
| `ModelDownloadStatus` (`types.rs:716-724`), and the `download-progress` event (`downloads.rs:1066-1075`) | `phase` | `"downloading"` or `"verifying"`; `#[serde(default)]`, absent means downloading |
| the same two | `received_bytes` | bytes read from the network for this model since this download started; `#[serde(default)]` |
| `download-failed` event (`downloads.rs:237-242`) | `reason` | `"offline"`, `"server"`, `"damaged"`, `"disk_full"`, `"folder"`, `"other"` |
| `download-failed` | `downloaded_bytes`, `total_bytes` | what is on disk for this model when it ended: for each planned file its full size when it is installed, else the size of its `.part` (the same reading as `:355-367`); and the sum of the planned sizes. More than 0 means the next download continues. |

Behaviour:

- **Once a second.** `download_attempt` publishes when 4 MiB arrived, at the end of the file,
  or when a second has passed since the last publication and something arrived (`:907-918`).
  Still no `state-changed` for progress (test `:1441-1467`).
- **`received_bytes`** grows by each chunk's length (`:900`) and by nothing else.
  `DownloadEntry` (`:46-55`) gains `received_bytes: u64` and `verifying: bool`; it is built at
  `:166`, in the tests at `:1291` and `:1923`, and in `src-tauri/src/model_storage.rs:1602`.
  `DownloadPhase` (`src-tauri/src/downloads.rs:41-44`) stays as it is: the disk space sum reads
  it (`:348`).
- **`verifying`** is set while a hash runs and cleared after: the final hash (`:815-818`), a
  complete partial (`reuse_complete_partial`, `:767`), and a file that is already there with
  the expected size (`:713`, `:759`). Each change publishes one `download-progress` with the
  current numbers. A file that is missing or has another size is not hashed
  (`verified_file`, `src-tauri/src/catalog.rs:77-87`) and sets nothing.
- **`reason`** comes from one function beside `is_cancelled` (`downloads.rs:268-270`), tested
  against the messages this file writes:

  | Message | Reason |
  | --- | --- |
  | `not enough disk space…` (`:374-378`) | `disk_full` |
  | `download checksum mismatch…` (`:821-823`), `download size mismatch…` (`:811-813`), `model server sent more data than expected` (`:902-904`) | `damaged` |
  | `the model folder is unavailable…` (`:141-144`), `wait for the model folder move…` (`:136-138`) | `folder` |
  | `download did not complete after four attempts: …` (`:800-802`) with `HTTP 4` or `HTTP 5` inside (`:869-871`) | `server` |
  | `download did not complete after four attempts: …`, anything else | `offline` |
  | everything else | `other` |

- **Texts** (`sync_runtime_downloads`): `:430` becomes `Downloaded and checked.`; `:434-435`
  become `Download stopped` and `What was downloaded is kept. The next download continues from
  there.` `src/App.svelte:1410` lists `Download paused` and takes the new title. The texts at
  `downloads.rs:445-463` are never drawn (the window draws its own line whenever the list is not empty,
  `src/App.svelte:4738`) and stay.

## Files

New:

| File | Holds |
| --- | --- |
| `src/lib/downloadProgress.ts` | `progressBytes`, `timeLeftText`, `TimeLeftLabel`, `DownloadDetail` (samples, speed, quiet, verifying delay, the line's text), `downloadFailureText` |
| `src/lib/modelNames.ts` | `modelDisplayName(snapshot, modelId, withVariant = false)` and `modelDownloadSize(snapshot, modelId)` |
| `e2e/downloads.e2e.ts` | the cases below |

```ts
// lib/downloadProgress.ts
export type DownloadPhase = 'waiting' | 'downloading' | 'verifying';
export function progressBytes(done: number, total: number): string;
export function timeLeftText(seconds: number): string;
export class TimeLeftLabel { text(seconds: number | null): string | null; }
export class DownloadDetail {
  observe(sample: { done: number; total: number; received: number; phase: DownloadPhase }, now: number): void;
  speed(now: number): number | null;   // bytes per second
  text(now: number): string;           // '454 MB of 4.9 GB · about 6 min left'
}
export interface DownloadFailure {
  modelId: string; name: string;
  reason: 'offline' | 'server' | 'damaged' | 'disk_full' | 'folder' | 'other';
  done: number; total: number; message: string;   // done > 0: a part is kept
}
export function downloadFailureText(failure: DownloadFailure, withName: boolean): { title: string; detail: string };

// lib/downloadSummary.ts
export interface DownloadLine { title: string; percent: number; detail: string; names: string; count: number; }
export class DownloadRun {
  observe(downloads: ModelDownloadStatus[], now: number): void;
  completed(modelId: string): void;
  dropped(modelId: string): void;
  line(now: number, name: (id: string) => string, size: (id: string) => number): DownloadLine | null;
  detail(modelId: string, now: number): string;   // one model's line, for the library
}
```

`modelDisplayName` looks in this order: `catalog.models` (`displayName`, `src/lib/plan.ts:27-34`),
`catalog.video_models` (`videoModelShortName`, `src/lib/videoEngines.ts:38-40`),
`catalog.interpolation_models` and `catalog.audio_models` (the name before ` — `, as
`src/EnhancePanel.svelte:176-177`), `edit.models` (`editFamilyLabel`, with ` · ` and
`editVariantName` when `withVariant`, `src/lib/editing.ts:87-101`), else `a model`.

Changed:

`src/lib/downloadSummary.ts`: `DownloadRun` replaces `downloadSummary`.

`src/lib/types.ts:437-443`: `phase?: 'downloading' | 'verifying'`, `received_bytes?: number`.

`src/lib/workerEvents.ts`
- `download-progress` (`:597-619`) copies `phase` and `received_bytes` into the entry; today it
  builds the entry from four fields and would drop them.
- `download-started` (`:576-596`) sets `received_bytes: 0`.

`src/App.svelte`
- `:3`, `:817-827`: one `DownloadRun`, fed from `activeDownloads` and the tick; names from
  `modelDisplayName`. `:1939-1960`: `completed`, `dropped`, and the failure record.
- `:4731-4760`, `:4791-4809`: title, second line, extra line, strip, as in "Who owns the status
  bar". The sentence at `:4758` goes.
- `:4810-4900`: the second `Cancel download`; the plural; the `title`.
- `:2424-2481`: all at once, cancel all, no dialog, the words after `then`.
- `:3391-3429` `runDownload`: remembers the licence answer; no dialog for a failed download.
  New `retryDownload(modelId)` and `cancelAllDownloads()`.
- `:1410`, `:1518`, `:1561`: the title's new name; the `Wait for the download…` sentence.
- `:4918-4921`: the library also gets the failures, each model's line (`downloadDetails`) and
  `retryDownload`.

`src/EnhancePanel.svelte`, `src/lib/fixRows.ts`, `src/PlanSheet.svelte`, `src/ModelLibrary.svelte`,
`src/EngineRecommendationCard.svelte`, `src/EngineManagerDialog.svelte`, `src/UpdateNotice.svelte`,
`src/UpdatePanel.svelte`: as under Behaviour. `modelLabel` (`src/ModelLibrary.svelte:443-453`)
calls `modelDisplayName` and keeps its own line for the user's edit model (`__custom__`).

`src/styles.css`: `.library-download-meta` (`:3895-3900`) gets `white-space: nowrap`, so the
name beside it gives way first; the two buttons of a failed item sit in an `.eyebrow-actions`
(`:3652`). Nothing else.

`src-tauri/src/downloads.rs`, `src-tauri/src/types.rs`, `src-tauri/src/model_storage.rs:1602`: as
under Host.

`e2e/fake-host.ts`
- `DownloadEntry` (`:178-185`) gains `phase` and `receivedBytes`; `syncDownloads` (`:187-200`)
  and `progressDownload` (`:247-262`) send them. `progressDownload(id, progress, extra?)` takes
  `{ phase?, receivedBytes? }`; without `receivedBytes` it sends the downloaded bytes.
- `failDownload(id, message, extra?)` (`:234-246`) takes `{ reason? }` and sends the entry's
  bytes, or 0 for `damaged`.
- `finishDownload` (`:215-233`) and `download_model` (`:1016-1020`) also look in
  `catalog.interpolation_models` and `catalog.audio_models`.

Docs: `CHANGELOG.md` under 0.7.0, one entry ("Downloads show in one place, with size and time
left"). This file as `docs/design/downloads.md`. `docs/design/enhance-panel.md`: the Copy rows
`Row value` (334) and `Cost tip` (357) and the Foot rule 1 (458-460) point here.

## Tasks

In this order. Each is under an hour for a worker and ends with `./local-ci.sh frontend` green
(D3 and D4: `./local-ci.sh rust`). A task updates the vitest and e2e tests its change breaks.
D1 to D4 are independent of each other. Screenshot baselines wait for D11.

Enhance panel tasks: D8 edits what T10 (the foot: needs-you and cost line, `d21a0670`), T14
(the Fix value and its popover, `3706b1be`) and T15 (the Plan sheet's final rows, `805ca9d8`)
built. D8 and D11 also need T16 (`3baa4274`: the panel's e2e file, its baselines, dead CSS
removed). All four are on this branch, so nothing waits. If the panel is changed again before
these tasks start, D8 is the task to re-read.

| # | Task | Touches | Needs | Done when |
| --- | --- | --- | --- | --- |
| D1 | Size, time and speed helpers | `lib/downloadProgress.ts` and its test | | The vitest tables below pass. |
| D2 | One name for a model; `DownloadRun` | `lib/modelNames.ts`, `lib/downloadSummary.ts`, their tests | D1 | Run and name tests pass; an edit model and RIFE give their names. |
| D3 | Host: `phase`, `received_bytes`, progress once a second | `downloads.rs`, `types.rs`, `model_storage.rs:1602` | | Host tests 1 to 4 pass; `progress_chunks_emit…` still passes. |
| D4 | Host: failure `reason` and bytes, the three texts | `downloads.rs` | D3 (same file) | Host tests 5 to 7 pass. |
| D5 | Window reads the new fields; fake host sends them | `lib/types.ts`, `lib/workerEvents.ts` and its test, `e2e/fake-host.ts` | | Reducer tests pass; the old e2e suite is still green. |
| D6 | Status bar: line, tooltip, strip text, extra line, second Cancel, `Download stopped` | `App.svelte`, `App.test.ts` | D1, D2, D5 | Tests "Status bar" pass. By hand in `npm run tauri dev`: download a model from the library, close it, watch size and time; cancel; download again and see it continue. |
| D7 | Start asks for all models at once; cancel all; failure line instead of the dialog; `then …` | `App.svelte`, `App.test.ts` | D6 | Tests "Start" and "Failure" pass. |
| D8 | Panel: no block for a running download, no download button, cost line, Fix value, Plan sheet state | `EnhancePanel.svelte`, `lib/fixRows.ts` and its test, `PlanSheet.svelte`, `App.test.ts`, `plan.e2e.ts` | T10, T14, T15, T16 | Tests "Panel" pass; the foot test of `enhance-panel.e2e.ts` still passes. |
| D9 | Library: tray text, `Try again`, row words, passive detail button | `ModelLibrary.svelte`, `App.svelte` (props), `App.test.ts`, `library.e2e.ts`, `styles.css` | D2, D5, D7 | Tests "Library" pass; the overflow test (`library.e2e.ts:438-468`) passes with the longer text. |
| D10 | Engine banner and dialog, update banner and dialog | the four components, `EngineManagerDialog.test.ts`, `UpdatePanel.test.ts`, `App.test.ts:8430` | D1 | Tests "Engine and update" pass. |
| D11 | `downloads.e2e.ts`; `layout.e2e.ts` case; new shots; affected baselines regenerated and looked at; CHANGELOG | `e2e`, `CHANGELOG.md` | D6 to D10, T16 | `npm run e2e` green on Chromium and WebKit at both sizes. |

## Tests

### Host (`cargo test`, `downloads.rs`)

| # | Test | Expected |
| --- | --- | --- |
| 1 | A server that sends 64 KiB every 300 ms | at least one `download-progress` per second; none carries a `state-changed` |
| 2 | One-file model | events in order: `download-started`, progress with `phase: "downloading"`, one with `phase: "verifying"`, `download-completed` |
| 3 | A bundle whose first file is already installed | a `verifying` progress before the first network byte; `received_bytes` is 0 in it and `downloaded_bytes` is that file's size after it |
| 4 | Resume from a 1 MiB part of a 3 MiB file | the last progress has `downloaded_bytes` 3 MiB and `received_bytes` 2 MiB |
| 5 | `failure_reason` | one case per row of the table, built from the real error values |
| 6 | Checksum mismatch (the test at `:2112`); a server that sends 1 MiB and then drops every connection | `download-failed` with `reason: "damaged"` and `downloaded_bytes` 0; with `reason: "offline"` and `downloaded_bytes` 1 MiB |
| 7 | Cancel (the test at `:1665`); success | `status_title` `Download stopped`; after success `status_detail` `Downloaded and checked.` |

### vitest

`lib/downloadProgress.test.ts`

| Case | Expected |
| --- | --- |
| `progressBytes(476_000_000, 5_293_871_164)` | `454 MB of 4.9 GB` |
| `progressBytes(1_288_490_189, 5_293_871_164)` | `1.2 of 4.9 GB` |
| `progressBytes(0, 5_293_871_164)`, `(5_293_871_164, 5_293_871_164)` | `4.9 GB` |
| `progressBytes(12 MiB, 0)` | `12 MB` |
| `timeLeftText`: 30, 44, 90, 330, 3540, 3600, 4800, 7200, 12600 | `less than a minute left` twice, `about 2 min left`, `about 6 min left`, `about 59 min left`, `about 1 h left`, `about 1 h 20 min left`, `about 2 h left`, `about 4 h left` |
| `TimeLeftLabel`: 330, then 318, then 280 | `about 6 min left` twice, then `about 5 min left` |
| `DownloadDetail`: samples at 0, 1, 2 s | no time yet; text is the size alone |
| the same, samples at 0 to 4 s, 12 MiB each second, 4.4 GiB missing | speed 12 MiB/s; text ends `about 6 min left` |
| first sample `received` 0 with `done` 1 GiB (a resume) | speed is not inflated: it is measured on `received` |
| last sample 9 s ago | text ends `Waiting for the connection…`; `speed` is null |
| phase `verifying` for 0.5 s, then 1.2 s | time, then `Verifying…`; with `done == total` the text is `Verifying…` alone |
| phase `waiting` | text is the total alone |
| `downloadFailureText` | one case per Copy row, with and without the name |

`lib/modelNames.test.ts`: an image model, a video model, RIFE, an audio model and
`flux2_klein_4b_q4_0` give `FLUX.2 klein 4B` and, with the variant, `FLUX.2 klein 4B ·
Balanced`; an unknown id gives `a model`.

`lib/downloadSummary.test.ts` (rewritten)

| Case | Expected |
| --- | --- |
| one model, half done | title `Downloading SPAN ×4 NomosUni`, percent 50, count 1 |
| one running, one waiting | `Downloading 2 models`; percent over both sizes (today's test, `downloadSummary.test.ts:49`) |
| three in a run, the first completes and leaves the list | the percentage does not go down; `Downloading 3 models` stays |
| one is dropped (cancelled) | it leaves the sums; `Downloading 2 models` |
| a size is unknown | mean of the percentages; no size in the detail |
| the list empties, then a new download | a new run: one model, its own percentage |
| `names` | one line per model with `· 80%`, `· waiting`, `· verifying` |

`lib/workerEvents.test.ts`: `download-progress` with `phase` and `received_bytes` keeps both in
the entry; without them the entry has neither; `download-started` sets `received_bytes` 0.

`lib/fixRows.test.ts`: a model in `downloadMap` gives the name alone, for `downloading` and for
`queued`; a missing one still gives `RealPLKSR Denoise · 28 MB`.

`App.test.ts`, by block. `mountWith` (`:427-441`) keeps matching `/^Downloading /`.

| Block | Case | Expected |
| --- | --- | --- |
| Status bar | an edit model downloads (`flux2_klein_4b_q4_0`, 9 %) | title `Downloading FLUX.2 klein 4B · 9%`; the text `flux2_klein_4b_q4_0` is nowhere in the document; no `Checked against` |
| | the same | second line `454 MB of 4.9 GB`; the strip's `aria-valuetext` starts `9%, 454 MB of 4.9 GB` |
| | two models (today's test `:1417-1442`) | title `Downloading 2 models · N%`; the second line's `title` holds both names |
| | progress events over 5 s of fake time | the second line ends `about … left` |
| | no event for 9 s | `Waiting for the connection…` |
| | `phase: 'verifying'` for 2 s | `Verifying…` |
| | a download and no Start waiting | a button `Cancel download` beside a disabled start button whose `title` is `Wait for the download to finish.`; the click calls `cancelDownload` for every model in the list; an error from it opens no dialog |
| | a job runs and a model downloads | the job's title; a line `Also downloading <name>`; no `Cancel download` |
| | `status_title: 'Download stopped'` with a selected photo | the second line is the idle summary |
| Start | two models missing, press `Download …, then upscale` | `downloadModel` is called for both before either resolves; second line ends `· then upscale` |
| | `Cancel downloads` | `cancelDownload` for both; `startJobs` never called (extends `:4436-4463`) |
| | both resolve | `startJobs` called once |
| | `Upscale all 7` with a download | the second line ends `· then upscale all 7` |
| Failure | one of two rejects after a `download-failed` with `reason: 'offline'` and 1.2 GB kept | no dialog; title `Download interrupted`; the sentence names the model and `kept at`; `role="alert"`; the start button is back; `startJobs` not called |
| | `reason: 'damaged'` | `arrived damaged and was removed` |
| | `reason: 'disk_full'` | the host's sentence with a capital and a full stop |
| | press Start again | the failure line is gone at `download-queued` |
| | the failed model is not in the plan | the link `Open library` opens the library on it |
| Panel | a model that needs the licence tick | the tick in the foot; no button `Download model`; after the tick the start button reads `Download …, then upscale` (rewrites `:9904-9917`) |
| | that model downloads | no needs-you block; no `progressbar` inside `.panel-foot` |
| | all models missing, one downloads | the cost line shows the sum of the others; all download: no cost line |
| | the cost tip | the three sentences (rewrites `:9928-9931`) |
| | Plan sheet with a downloading stage | no `%` and no `Waiting` in the list `Stages` |
| Library | four downloads (today's test `:7692-7727`) | the tray items show sizes, not `40%`; no button `Waiting in queue · Cancel`; the detail button is disabled and reads `Downloading…`; exactly one `Cancel` per model in the dialog |
| | a failed item | the plain sentence; `Try again` calls `downloadModel` again with the first call's licence answer; `Dismiss` as today |
| | list row of a downloading model | `Downloading` |
| Engine and update | engine downloading (`:8430`) | `Downloading NVIDIA engine · 50%` and a line with `of` |
| | update downloading | the banner and the dialog show `… of … MB`; no `MB downloaded` |

### e2e (Playwright)

New `e2e/downloads.e2e.ts`, scenario `concurrentDownloads: true`, time driven with `page.clock`
as `eta.e2e.ts:22-43` does.

| Case | Expected |
| --- | --- |
| Edit, model missing, press `Download 4.9 GB, then edit`, progress to 9 % | exactly one `progressbar` in the main window; exactly one button whose name contains `Cancel`; the text `9%` occurs once; no `flux2_`; no `checksum` |
| the same, at 1024 × 700 and 880 × 640 | the foot has no visible box; no row of the panel moved (± 0.5 px) from before the click |
| clock run 5 s with progress every second | the second line matches `/of 4\.9 GB · about \d+ min left · then edit$/` |
| clock run 9 s without progress | `Waiting for the connection…` |
| `phase: 'verifying'`, clock run 2 s | `Verifying… · then edit`; the strip is full |
| finish | the edit starts (`start_jobs` among the host calls) |
| photo with Blur and Noise, nothing installed, press Start | `Downloading 3 models`; one bar; finishing the first does not lower `aria-valuenow` |
| fail with `reason: 'offline'` at 24 % | no dialog; `Download interrupted`; the sentence says `kept at 1.2 of 4.9 GB`; the start button is enabled with its download label |
| download from the library, close the library | the status bar shows the line and `Cancel download`; the click empties it and the title is `Download stopped` |
| 880 × 640, a finished result selected, a download running, Start not waiting | the buttons stay apart and inside the bar (the checks of `layout.e2e.ts:228-243`) |
| forced colours and both themes | `contrastOnPage` of the second line and of `Cancel download` is at least 4.5 |

Existing files that change:

| File | What |
| --- | --- |
| `library.e2e.ts:403-430` | the tray shows sizes; `35%` and `80%` become the sizes at those points; the failed item has `Try again` |
| `library.e2e.ts:438-468` | must pass unchanged with the longer text |
| `plan.e2e.ts` | where a stage's state is read during a download: no percentage |
| `layout.e2e.ts:200-266` | must pass unchanged |
| `screens.e2e.ts` | new shots `download-waiting`, `download-several`, `download-failed`, `library-downloads` |

Baselines: the status bar and the foot change only in the new shots; the library shot changes
if it shows a download. Regenerate after T16, look at each.

## What may not work as drawn

- **Speed on a bursty line.** A 10 s window with one sample a second is smooth for a steady
  line and jumpy for one that sends in bursts. The label's hold covers a jump of up to 0.7 of a
  step; more shows as a changing minute count. The window length is one constant.
- **Two models that share a file** (two variants of one edit family) each count the shared
  file, so the sum is too high until it arrives, and the one that waits for the other's file
  (`lock_file_with_cancel`, `src-tauri/src/downloads.rs:758`) shows `Waiting for the
  connection…` although the connection is fine. A plan never contains both; the library can.
- **Hash time is not measured here.** `Verifying…` after 1 s is a guess that a multi-gigabyte
  file takes seconds and a small one does not. If it flashes on fast disks, raise the delay.
- **Tooltips with several lines** use the `title` attribute. WebKit and WebView2 show line
  breaks in it; if one does not, the names are joined with commas.
- **The computer can go to sleep during a long download.** Keep-awake is described as "while
  jobs run" (`src-tauri/src/stay_awake.rs:1-5`); whether it holds during a download was not
  traced. If it does not, a 20 GB download with Start waiting stops when the Mac sleeps.
- **The foot in Edit** relies on `:empty`. Svelte leaves comment nodes there, which `:empty`
  ignores, but a stray space would draw an empty box with a border.
- **After a restart** the window has forgotten a failure. The start button says the full size
  and the bar jumps to the kept part on the first progress.

## Outside this change

- **A kept part is never removed.** The host deletes a `.part` file only when its checksum is
  wrong or it is too long (`src-tauri/src/downloads.rs:820`, `:854`, `:993`). A cancelled 4 GB
  download stays in the model folder until the model is downloaded. No code was found that
  lists or removes these files. See Open questions.
- **More patience when the connection drops.** Four attempts a few seconds apart means a
  router restart ends a 20 GB download, and with Start waiting nothing has run when the user
  comes back. Retrying for some minutes, and counting attempts again after data arrived, is a
  change in `download_one` (`:777-806`) of its own.
- **Pressing Start while its model already downloads**, so that the run begins when the
  download ends. Today neither start is possible then (`modelReady`, `src/App.svelte:980`;
  `prepareReady`, `:1373`).
- **Remaining size on the start button** after a part is kept (`Download 3.7 GB more, then
  edit`). The plan would have to read the part's size from disk, and `plan_job` reads no file
  (`enhance-panel.md:557`).
- The engine's and the updater's own stage texts and buttons.

## Open questions

Only where there is a real choice. The spec builds the first answer of each.

1. **Speed.** Only in the tooltip, so the line has two facts (size, time), or in the line as
   `454 MB of 4.9 GB · 12 MB/s · about 6 min left`? It is one line in `DownloadDetail.text`.
2. **The download button in the foot** for a model with a licence tick. Removed, because Start
   downloads once the tick is set, or kept as `Download model · 28 MB` for downloading without
   running?
3. **Cancel keeps the part.** Named `Cancel download` as today, with the tooltip saying the part
   is kept, or should cancelling delete the part, and a second action (`Pause`) keep it? The
   first needs nothing from the host; the second needs a delete in the host and one more
   button.
4. **Connection drops.** Leave the host's four quick attempts for 0.7.0 and show the failure
   line, or make the host keep trying for a few minutes in this release (see Outside)?
