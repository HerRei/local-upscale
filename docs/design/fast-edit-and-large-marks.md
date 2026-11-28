# Quality in Edit, and a model for larger marks

Build spec for LocalSR 0.7.0, on `w/070-qwenfast`. Line numbers refer to `7c4165ce`; every
reference also names its function or its text. Paths are relative to `desktop/` unless they
start with `docs/`, `src/localsr/` or `tests/`. Layout notation as in `enhance-panel.md`:
340 px, 16 px padding.

Two parts, independent of each other:

1. **Edit gets a `Quality` row, `Quick` / `Best`,** for Qwen Image Edit 2511. Quick runs the
   official 4-step Lightning LoRA; Best is today's 40 steps.
2. **Dust & scratches fills wider marks with MI-GAN.** No control. The row downloads 28 MB the
   first time a painted photo is started.

Both wait for engine work that is not committed yet. The Edit engine task works in this
worktree; its uncommitted draft was read on 2026-11-28 and Part 1 uses its names
(`edit_lightning`, `speed`, `kind: "speed_addon"`, `with_addon`). Its files are moving, so
they are referred to by function and text, not by line. The names for Part 2 are proposals.
Check both against the engine tasks when they merge.

Measured on 2026-11-28 (given to this spec, not run again for it):

| | Quick (4 steps, cfg 1) | Best (40 steps, cfg 2.5) |
| --- | --- | --- |
| 16 GB graphics card | 24 s | 161 s |
| 16 GB M1 Pro Mac, 512 px, Qwen Q2_K | 2.3 min | 20.6 min |
| Peak memory on that Mac | 9.0 GiB | 7.3 GiB |
| Colour, Film look, Scratches prompts | equal | equal |
| Remove, New background prompts | worse in 3 of 15 results | |
| Another seed | changes the result less | |

Dust & scratches: MI-GAN (MIT, 28 MB, ONNX) against OpenCV Telea on painted marks wider than
about 10 px: LPIPS 0.076 against 0.148 (blotches) and 0.277 against 0.476 (patches), better in
26 of 30 and 28 of 30 photos, never worse. On hairlines it is worse. About 0.23 s per region:
a heavily marked 12 MP photo takes about 25 s where Telea takes 0.4 s.

---

## Part 1: Quality in Edit

### Decisions

| Point | Decision | Reason |
| --- | --- | --- |
| What the user chooses | `Quality`: `Quick` or `Best`. | The words Upscale & restore already uses for "fast" against "most careful, takes longer" (`EnhancePanel.svelte:766-783`). One vocabulary for both tasks; nobody has to learn "Lightning" or "LoRA" to edit faster. |
| Where | One segmented row in the Edit panel's Model block, under the model's note line. Not in Advanced, not in Expert, not a second set of models in the library. | It changes the time of every edit by a factor of 7 to 9 and sometimes the result, so it is an everyday control like the prompt and the model. Advanced is closed by default. Six sizes times two speeds as twelve library rows would hide that it is one choice. |
| When the row shows | The engine lists `edit_lightning`, and the catalog has a speed add-on for the chosen model's family. Today that is Qwen Image Edit 2511. | FLUX.2 klein 4B and Mage-Flow-Edit-Turbo already run 4 steps (`edit_catalog.json`). A row that can never apply is absent, as GPU memory is on a Mac (`edit-expert.md`, "GPU memory on a Mac"). |
| Your own edit model | No row. | The LoRA is only tested on the catalog's files. See "Later". |
| Default per computer | `Quick`, on every computer: 16 GB Macs, larger Macs, graphics cards. | On a Mac, Best is 20 minutes for one edit at 512 px. On a card it is 161 s against 24 s. People try a prompt several times before it is right; the fast run is the one to try with, and Best is one click away for the last one. One default for all computers is one sentence in the docs. |
| Default per prompt example | None. An example writes the prompt and nothing else. | The prompt is free text, so the app cannot know what kind of edit it is; the same words typed by hand would get another default. Four of the five examples are not offered today (`families: []`, `lib/promptExamples.ts:10-41`). A click on an example that makes the next edit nine times longer is a surprise. Trying Quick first costs 25 s to 2 min. The tip names the two kinds of edit where Best helps. |
| Saved as | `edit_speed`, `lightning` or `full` (the engine's two words), a saved setting like Edit size, Steps and Seed. Absent means `lightning`, which is Quick. Choosing another model does not reset it. | Not a session choice (`sessionKeys`, `App.svelte:769`). It describes how the user likes to edit, not one model. |
| The 850 MB file | Nothing downloads on the click. With Quick pressed and the file missing, the cost line counts it and Start reads `Download 850 MB, then edit`. With the model missing too it is one download: `Download 12.4 GB, then edit`. | The rule every fix and Backgr. follow (`scan-and-background.md`, "Backgr. download"). The plan gains a second stage that only the download code reads. |
| The file in the library | Its own row, last in the Qwen Image Edit 2511 list: `Lightning 4-step`. It can be downloaded ahead and removed. A model's `Download` button fetches the model only. | One button, one item, and the number on the button is the number of the row. Start fetches whatever is still missing. |
| Steps | In Quick the Steps field shows `4` and is disabled. The saved number is not touched and applies to Best. | The LoRA is made for 4 steps and the engine's choice is one of two. Deriving Quick from a typed `4` would be a hidden rule. The field stays where it is, so no row moves. |
| Time | The cost line shows `about 2:20` once this computer has finished two edits with the same model, quality, steps, size and hardware. Before that, no time. | The existing rule for every job (`typical_seconds_per_unit`, `src-tauri/src/queue_timing.rs:125`). Two machines were measured; a number for all others would be invented. The tip carries the two measurements as what they are. |
| Plan line | None in Edit, as today (`enhance-panel.md:464`). | Edit is one step. Its name and quality are the two rows of the Model block. |
| 16 GB Mac, memory | Qwen `Compact` (Q2_K) is listed as fitting. Its note reads `Needs most of this Mac's memory`. Whether enough is free is decided at the start, by the engine; its refusal shows in `Last error`, as today. | The engine reads free memory when the edit starts. A warning from the last hardware reading would be wrong as often as right (`edit-expert.md`, "Placement before a run"). |
| 16 GB Mac, default model | Unchanged: FLUX.2 klein 4B. Qwen Compact is an explicit choice. | `recommendAnyEditModel` leaves Q2_K out on purpose (`lib/editing.ts:188-217`); with Quick, Compact needs about 70 % of the Mac's memory free. |
| Results | An Edit result made with the row says `Quick` or `Best` first in its Outputs line: `Quick · 512 × 320 · 24 s`. | Two results of one prompt are otherwise told apart only by their seconds. The slot exists (`ui.quality`, `OutputsList.svelte:66`). |
| Names | The panel says `Quick`. Only the Quality tip and the library say `Lightning`. | The library names every file by its real name; the panel names what the user gets. |

### Layout

Edit on a 16 GB Mac, Qwen Compact chosen, nothing downloaded yet:

```
┌──────────────────────────────────────────────────────┐
│ Enhance                                    Recipes ▾ │
│ Task ?     [ Upscale & restore  ][●     Edit       ] │
├──────────────────────────────────────────────────────┤
│ WHAT TO CHANGE                            Examples ▾ │
│ [ Add natural colour to this black-and-white photo ] │
├──────────────────────────────────────────────────────┤
│ MODEL                                                │
│ Edit       Qwen Image Edit 2511              Change… │
│            Compact · Needs most of this Mac's memory │
│ Quality ?  [● Quick           ][ Best              ] │
├──────────────────────────────────────────────────────┤
│ Advanced                         Size, steps, seed ⌄ │
╞══════════════════════════════════════════════════════╡
│                                12.4 GB to download ? │
└──────────────────────────────────────────────────────┘
 status bar:             [ Download 12.4 GB, then edit ]
```

The model installed, the Lightning file not:

```
│ Quality ?  [● Quick           ][ Best              ] │
╞══════════════════════════════════════════════════════╡
│                                 850 MB to download ? │
 status bar:              [ Download 850 MB, then edit ]
```

Everything installed, after two finished Quick edits on this computer:

```
│ Quality ?  [● Quick           ][ Best              ] │
╞══════════════════════════════════════════════════════╡
│ about 2:20                                           │
 status bar:                           [ Edit selected ]
```

Advanced, open, with Quick pressed:

```
│ Advanced                         Size, steps, seed ⌃ │
│ Edit size ?     [ 512 px · longest edge          ▾ ] │
│ Steps ?               Seed ?                         │
│ [░ 4 ░░░░░░░░░░░░░░]  [ 42               ]           │
│ Quick runs 4 steps. The same seed with the same      │
│ prompt and image repeats a result.                   │
```

The Model library, group `Edit with a prompt`, on the same Mac:

```
 Qwen Image Edit 2511    Edits a photo from a written instruction
 ┌──────────────────────────────────────────────────┐
 │ Compact  Q2_K                            11.6 GB │
 │ Needs 16 GB of memory                            │
 ├──────────────────────────────────────────────────┤
 │ Lightning 4-step  LoRA                    850 MB │
 │ For Quick quality                      Installed │
 └──────────────────────────────────────────────────┘
 FLUX.2 klein 4B         Fast 4-step editor for 16 GB computers
 …
```

### States

`addon` is `editSpeedAddon(editModel, snapshot.edit.addons, features)`; `speed` is
`editSpeed(settings, addon)`: `lightning` is Quick, `full` is Best.

| State | Quality row | Steps field | Foot and Start |
| --- | --- | --- | --- |
| Not offered: no `edit_lightning` in the engine's features, no add-on for the family, or your own model | not rendered | as today | as today; the request carries `speed: 'full'` |
| Quick, file not downloaded | `Quick` checked; its `title` ends ` · 850 MB download` | `4`, disabled | cost line `850 MB to download`; Start `Download 850 MB, then edit` |
| Quick, file and model both missing | the same | the same | one sum: `12.4 GB to download`, `Download 12.4 GB, then edit` |
| Quick, file downloading (from Start or from the library) | `Quick` checked; the row stays usable. While Start waits for the download, the edit that follows uses what is checked when it begins, as a changed upscaler is used today (`e2e/downloads.e2e.ts:148`); with `Best` it no longer waits for this file | `4`, disabled | the download line of the status bar, as for the model (`e2e/downloads.e2e.ts:29`); a second Start is refused with `Wait for the download to finish.` (`App.svelte:1825`) |
| Quick, ready | `Quick` checked, `title` `4 steps` | `4`, disabled | no download; time when measured |
| Best | `Best` checked, `title` `40 steps` (the number that applies) | the saved number or the model's own, editable | no Lightning download, whether the file is there or not |
| Download failed or offline | `Quick` stays checked | `4`, disabled | the failure line of any download, with its retry (`e2e/downloads.e2e.ts:129`). Pressing `Best` starts without the file. |
| File removed in the library while Quick is saved | as "not downloaded" | | |
| `edit_speed: 'lightning'` saved, engine without `edit_lightning` | not rendered | as today | the edit runs the full steps; the saved value stays |
| The editing runtime cannot apply a LoRA (an older `sd-cli` chosen under `Choose runtime…`; `require_lora_support` in the engine task's draft) | rendered, `Quick` checked: the window cannot know | `4`, disabled | the edit is refused at once and `Last error` shows the engine's text; `Best` edits |
| Job running | disabled with the body's `fieldset`; the checked segment stays readable | disabled | as today |
| Too heavy, no runtime, no device | rendered; `editBlockReason` holds Start as today (`App.svelte:795-810`) | | |

The row is part of the Model block, so it comes and goes with the model: choosing FLUX.2 klein
in the library takes it away, choosing Qwen brings it back with the saved quality.

### Copy

| Where | String |
| --- | --- |
| Row label | `Quality` (tip label `Quality`; the group's accessible name is `Quality`) |
| Segments | `Quick`, `Best` (radios) |
| Segment `title` | Quick `4 steps`, and ` · 850 MB download` while the file is missing (`formatBytes`) · Best `40 steps` (the number Best will run) |
| Quality tip, common part | `Quick runs the edit in 4 steps with Lightning, a small add-on for this model. Best runs the model's own 40 steps, or the number you set under Advanced, and takes several times as long.` then the measured sentence, then `Colour and look changes come out the same either way. Removing an object or replacing a background follows the instruction more reliably with Best. With Quick, another seed changes the result less.` |
| Measured sentence, Mac | `Measured on a 16 GB M1 Pro Mac at 512 px: 2.3 minutes with Quick, 20.6 with Best.` |
| Measured sentence, card with its own memory | `Measured on a 16 GB graphics card: 24 seconds with Quick, 161 with Best.` |
| Tip, added while the file is missing | ` The first Quick edit downloads 850 MB.` |
| Steps field, disabled, `title` | `Quick always runs 4 steps.` |
| Steps tip, added when the row shows | ` Quick always runs 4 steps; this number is for Best.` |
| Line under Steps and Seed, Quick (replaces `… is tuned for 40 steps.`, `AdvancedSettings.svelte:122-126`) | `Quick runs 4 steps. The same seed with the same prompt and image repeats a result.` |
| The same line, Best | unchanged |
| Model note on a Mac where the model needs more than two thirds of memory | `Compact · Needs most of this Mac's memory` (`EDIT_FIT_LABELS.heavy`) |
| Cost line | `about 2:20` · `850 MB to download` (both existing formats, `EnhancePanel.svelte:1241-1255`) |
| Start | `Download 850 MB, then edit` · `Download 12.4 GB, then edit` · `Edit selected` (existing, `App.svelte:5812`) |
| Outputs line of an Edit result | `Quick · 512 × 320 · 24 s` · `Best · 512 × 320 · 2:41` |
| Library row | name `Lightning 4-step`, small `LoRA`; second line `For Quick quality`; right `850 MB` and `Installed`, `Waiting`, `Downloading` or `Failed` as any row |
| Library details | head `Lightning 4-step`; under it `Qwen Image Edit 2511 · add-on`; text `Lets Qwen Image Edit 2511 finish an edit in 4 steps in place of 40. Edit uses it when Quality is Quick. One file serves every size of the model.` |
| Library facts | `Download` `850 MB` · `Steps` `4` |
| Library details, list | License `Apache-2.0`, author `LightX2V`, Source; `Format` `LoRA · safetensors · 1 file`; integrity and rights as an edit model |
| Library buttons | `Download 850 MB` · `✓ Installed · integrity checked before use` · `Remove from this computer`. No `Use for this job`. |
| Library, under Remove while Quick applies to the chosen model | `Quick uses this file. Removing it means an 850 MB download again.` (as `ModelLibrary.svelte:1891-1894`) |
| Library, `Installed` group | name `Lightning 4-step`, role `Edit with a prompt · add-on for Quick` |

No badge, no icon, no note in the panel that says the row is new.

### Behaviour

New in `src/lib/editing.ts`, pure:

```ts
/** The engine's words (`EDIT_SPEEDS`): `lightning` is Quick, `full` is Best. */
export type EditSpeed = 'full' | 'lightning';

/** The speed add-on for this model: same family, `edit_lightning` listed, not your own model. */
export function editSpeedAddon(model: EditModel | undefined, addons: readonly EditModel[] | undefined,
  features: readonly string[]): EditModel | undefined;

/** The speed that applies: the saved one, else `lightning`; null when the row is not offered. */
export function editSpeed(settings: Pick<UiSettings, 'edit_speed'>,
  addon: EditModel | undefined): EditSpeed | null;

/** Steps the edit runs: the add-on's in Quick, else the saved number or the model's own. */
export function editStepsFor(settings: Pick<UiSettings, 'edit_steps'>, model: EditModel,
  speed: EditSpeed | null, addon: EditModel | undefined): number;
```

- **The row.** In `EnhancePanel.svelte`, inside the Model section (`:923-986`), after the
  `.plan` list: a `.panel-row` with `.panel-key` (`<span class="panel-label">Quality</span>`
  and `<HelpTip label="Quality">`) and `<div class="seg-inline" role="radiogroup"
  aria-label="Quality">` with two `role="radio"` buttons, `aria-checked`, `class:active`,
  `on:keydown={radioGroupKeydown}`, as Backgr. has them (`:862-895`). A click calls
  `updateSettings({ edit_speed })`.
- **Column.** `.plan-row` puts the model's name at 74 px (68 px and a 6 px gap,
  `styles.css:3972-3979`); `.panel-row` puts its control at 76 px (`:2098-2103`). One rule,
  `.model-section .panel-row { grid-template-columns: 74px minmax(0, 1fr); }`, puts the
  control's left edge under the name. No other CSS.
- **The plan.** With `speed === 'lightning'`, `resolvePlan` (`lib/plan.ts:283-299`) and the
  host's planner (`src-tauri/src/job_plan.rs:427-445`) add a second stage after the edit
  stage: `kind: 'edit_addon'`, `role: 'step'`, `label: 'Quick'`, `editModel: addon`, with
  `installed`, `canDownload` and `sizeBytes` from the add-on. Because it carries an
  `editModel`, `stageTarget` (`EnhancePanel.svelte:255-263`), `pendingDownloads`
  (`plan.ts:580-599`), the cost line, the `Download …, then edit` label, the download line and
  `stepStagesReady` work as they are. The Model list skips it
  (`{#each plan.filter((stage) => stage.kind !== 'edit_addon')}`), so it never shows as a row.
- **Start.** `startEdit` (`App.svelte:4234-4264`) sends `steps: editStepsFor(…)` and
  `speed` (`'full'` when the row is not offered). `editReady` (`:811-817`) also needs the add-on
  installed in Quick; until then Start is the download button.
- **Steps.** `AdvancedSettings.svelte:88-101`: with Quick, `value={4}` (the add-on's
  `default_steps`), `disabled`, the `title`. `edit_steps` is not written. `useEditModel` keeps
  resetting `edit_steps` to 0 and leaves `edit_speed` alone (`App.svelte:4273-4278`).
- **Edit size.** `editRecommendedSize` (`lib/editing.ts:313-357`) repeats the engine's fit.
  On a card the engine task's draft holds the LoRA with the transformer (`plan_edit`:
  `diffusion_bytes` plus `lora_footprint`, which is the file's size there), so in Quick the
  function adds the add-on's bytes to `diffusion`. A Mac keeps its size steps.
- **Fit.** `editFitFor` (`lib/editing.ts:152-159`) returns `heavy` on a Mac when
  `editPeakEstimate(model) + max(2 GiB, 10 % of memory)` is more than two thirds of
  `system_ram_total`. With today's catalog that is Qwen Q2_K on 16 GB alone (11.6 of 16 GiB);
  the largest bundle of every other Mac size stays under it. `EDIT_FIT_LABELS.heavy` becomes
  `Needs most of this Mac's memory`. The label does not depend on Quality, so it does not flip.
  `heavy` is listed among the fitting models, can be used and is not hidden
  (`ModelLibrary.svelte:214-221`, `:1676`); the panel's note and the library's fit line show
  the label through code that is there (`EnhancePanel.svelte:948-953`, `ModelLibrary.svelte:1607-1614`).
- **Time.** A second estimate beside `startEstimate` (`App.svelte:1563-1586`), asked when the
  task is Edit, the plan is ready, no job runs and the photo is probed. It calls a new host
  command `estimate_edit(input: StartEditInput)`. `work_profile`
  (`queue_timing.rs:15-19`) stops returning nothing for an edit and returns a key of the model
  id, `speed`, steps, device and the output's width and height
  (`edit_dimensions`), with 1 unit. The rule of two measured runs stays.
- **Results.** The host writes `quality: "quick"` for `lightning` and `"best"` for `full` into
  the job's `ui_json` when the model had the choice, and nothing otherwise, so results of FLUX.2 klein and of older
  versions read as today.
- **Prompt examples.** Not touched.

### The 16 GB Mac

What the engine task changes: Q2_K's `min_unified_memory_gb` goes from 24 to 16
(`src/localsr/core/edit_catalog.json`), and admission uses the measured peaks. In its draft
(`MEASURED_UNIFIED_PEAKS`, `MEASURED_MARGIN`, `reserve_for`) a 512 px edit needs about 11.2 GiB
free with Quick (9.00 GiB × 1.02 plus a 2 GiB reserve, 70 % of the Mac's memory) and about
9.5 GiB with Best (7.31 GiB × 1.02 plus 2 GiB, 59 %). The brief for this spec says about 73 %.
Either way a browser with many tabs is enough to be refused.

Quick needs about 1.7 GiB more than Best there, so Best can start where Quick is refused. The
UI does not suggest it: it would trade a minute of closing apps for twenty of waiting.

What the user sees, in order:

1. Library: `Compact` is in the list (today it is behind `Show 6 more that need more memory
   than this computer has`). Row `Needs 16 GB of memory`. The details' fit line, with the
   warning dot: `Needs most of this Mac's memory · this Mac has 16 GB of unified memory`.
2. Panel: `Compact · Needs most of this Mac's memory` under the model, `Quick` checked.
3. Start with enough free: the edit runs, 2 to 3 minutes.
4. Start with other apps holding memory: the job fails at once, before anything is loaded,
   and `Last error` shows the engine's text with focus on it, as any failure
   (`EnhancePanel.svelte:989-1003`). Nothing is lost; a download made for this start stays.
   Closing apps and pressing Start again is the whole recovery. No button is added.

Not built: a warning before the start, a memory number in the panel, a `Close apps` helper.

### Keyboard and accessibility

- The row is a radio group named `Quality`. Tab reaches it after `Change…`; Left, Right, Up,
  Down, Home and End move and choose (`radioGroupKeydown`, `App.svelte:3024`). Both segments
  show the focus ring of the other segmented rows.
- `About Quality` is the tip's button, next in the tab order, opened by click, Enter or Space
  (`HelpTip`).
- The download is never only in a `title`: the cost line and the Start button say it in text.
- A disabled Steps field cannot take focus, so its reason is also the visible line under it.
- The checked segment is told by `aria-checked` and by the raised background, and in high
  contrast by the 1 px inset ring (`styles.css:5492-5495`). No colour alone.
- The library row is a button like every row; its state (`Installed`, `Downloading`) is text.

### Engine and host contract

The engine half exists as the engine task's uncommitted draft (read on 2026-11-28 in
`edit_catalog.json`, `image_edit.py`, `edit_memory.py`, `worker/server.py`,
`worker/request_validation.py`); the host half is not written. Check both when they merge.

| Item | Engine draft | Host and window need |
| --- | --- | --- |
| Engine feature | `edit_lightning` in `_engine_features` (`src/localsr/worker/server.py`) | the row shows only with it |
| Catalog | an entry of `edit_catalog.json` with `kind: "speed_addon"`, `speed: "lightning"`: `model_id` `qwen_edit_2511_lightning_4step`, `name` `Qwen Image Edit 2511 · Lightning 4-step`, `family` `qwen-image-edit-2511`, one file with role `lora` (849,608,296 bytes), `default_steps` 4, `cfg_scale` 1, author `LightX2V`, Apache-2.0. The engine's `edit_catalog()` leaves entries with a `kind` out; `speed_addon(family, speed)` finds them. | The host does the same split: `EditSetup.models` holds bundles only, `EditSetup.addons` the entries with a `kind`. The add-on must never reach `edit.models`: it has no memory fields, and `recommendAnyEditModel` falls back to "the smallest bundle of all" (`lib/editing.ts:214-216`), which would be this 850 MB file. Every reader of the JSON needs the split: in the host `editing.rs`, `protocol.rs` and the tests of `job_plan.rs` (`:863`) and `commands/tests.rs`; in the window `lib/demo.ts:2-18`, which the e2e fake host builds on, and `lib/editing.test.ts`. It is downloaded, verified and removed by the commands of an edit bundle. |
| Request | `speed: "full" \| "lightning"` and `lightning_lora_path` on the edit job request | `speed` on `start_edit` (`src-tauri/src/editing.rs:127-146`, `protocol.rs:437-458`); the host resolves the path from the installed add-on and refuses `lightning` when the add-on is missing or of another family |
| Settings | none | `edit_speed: Option<String>` in `UiSettings` (`src-tauri/src/types.rs:521-532`), validated as `full` or `lightning` beside the other edit checks (`commands.rs:4725-4730`); absent in old settings files |
| Memory | Q2_K at 16 GB; the bundle is checked as it runs, with the LoRA in Quick (`with_addon`, `estimate_edit_memory`, `lora_footprint`) | the refusal names what the chosen quality needs |
| Runtime | `require_lora_support`: a runtime whose `--help` lacks the LoRA options is refused for `lightning` | nothing; the refusal is shown |
| Timing | none | `work_profile` for edits; `estimate_edit` |

Texts of the engine that reach `Last error`. They should use the row's words, `Quick` and
`Best`; the panel never says "full quality":

1. The admission refusal (`plan_edit` in `src/localsr/core/edit_memory.py`, the text `Editing
   needs about …`) makes the reader add two numbers in GiB: `Editing needs about 9.2 GiB
   available plus a 2.0 GiB reserve for other apps; only 6.1 GiB is currently available. Close
   apps or choose a smaller model or edit size, and retry.` Suggested: `This edit needs about
   11.2 GB of free memory, and 6.1 GB is free now. Close other apps and start again, or choose
   a smaller model.` One number, the sum. The part about the edit size only when the size is
   above 512 px.
2. `This editing runtime cannot apply the Lightning add-on. Reinstall LocalSR to restore it, or
   edit at full quality.` Suggested: `This editing runtime cannot run Quick. Choose Best, or
   reinstall LocalSR to get its own runtime back.`
3. `… has no Lightning add-on. Edit at full quality.` and `Download the Lightning add-on in the
   model library first.` cannot be reached from the window (the row is absent; Start downloads
   the file). For other clients: `Quick is not available for …. Choose Best.` and `Quick needs
   the Lightning file, which is not downloaded.`
4. `Memory pressure is high. Close other apps before editing.` (the first check of `plan_edit`)
   stays.

### Later

| Part | Appears when | How |
| --- | --- | --- |
| Quick for your own edit model | someone has tested the LoRA on other files of the family | `editSpeedAddon` accepts `__custom__` of that family |
| A note on the Remove and New background examples | the two examples are listed for Qwen again (`PROMPT_EXAMPLE_FAMILIES`) | the examples' existing `note` line: `More reliable with Best quality.` |
| An 8-step file | the catalog has one | a third segment is not added; Steps offers 4 and 8 in Quick |
| A speed add-on for another family | the catalog lists it | nothing: the row follows `addons` |

---

## Part 2: Dust & scratches with MI-GAN

### Decisions

| Point | Decision | Reason |
| --- | --- | --- |
| Control | None. No model picker, no `Hairlines / Larger marks` switch, no value on the row beyond `Paint…` and the mark count. | The engine splits by the width of each painted mark, and on wider marks the model was never worse. A switch would ask the user to make the choice the measurement already made. |
| The model | MI-GAN is the step's one model, chosen by the row. No `Change…`. | As BiRefNet for Backgr. (`scan-and-background.md`, "Backgr. model"). |
| Download | With the first start that includes a painted photo: the cost line says `28 MB to download` and Start `Download 28 MB, then upscale`. Nothing downloads on the tick or while painting. The size is also in the row's `title`. | The rule of every fix. A background download on the tick would make the first result depend on whether it had finished. |
| Needed or optional | Needed, when the engine and the catalog offer it: a start with a painted photo waits for the model like for any other. The engine's Telea-only path is for a catalog or engine without it. | One behaviour per version. An optional model means two results for the same paint, decided by the network. |
| Library | Not in `Fix noise, blur, JPEG, colour`: there is nothing to choose. Once on disk it is listed under `Installed`, where its licence and source can be read and it can be removed. BiRefNet gets the same. | Today a step's model is hidden everywhere (`ModelLibrary.svelte:177`), so 444 MB cannot be removed or looked up in the app. |
| Without the model | Catalog without it, or engine without `image_repair_model`: the row, its copy and its result are exactly today's. | Older engines stay installed beside new hosts. |
| Time | No number of its own. The repair's time is part of the measured time of the job. While it runs, the step's progress follows the regions. | 0.23 s per region was measured on one machine; the cost line only shows what this computer measured. |
| Preview patch | The patch runs the same split on its region. With the model missing, the chip is `↓ Dust & scratches`, as any step whose model is not downloaded. | The patch must show what the job will do, never a Telea fill that the job would not make. |
| Copy | `small` goes from the row's `title`; the paint bar's tip says what happens to thin and to wider marks and that the brush should fit the mark. | The row no longer fails on blotches, and the split follows the painted width. |
| Plan | Plan line `Dust & scratches`, unchanged. The Plan sheet's row keeps `3 marks` as its name and names `MI-GAN` on its note line. | The marks are what the user did; the model is a detail. |
| Finding marks automatically | Not offered. No `Find marks` button, no hint that one exists. | The detector was measured and rejected. |
| Recipes, workflows | Unchanged: Dust & scratches is not saved in a recipe (`tools-wave.md`, "Recipes"). | Paint belongs to one photo. |

### Layout

A painted photo, MI-GAN not downloaded:

```
│ Fix ?                                                │
│ [✓] Dust & scratches                         3 marks │
│ [ ] Blur                                             │
│ [ ] JPEG artifacts                                   │
│ [ ] Noise                                            │
│ [ ] Colour                                           │
├──────────────────────────────────────────────────────┤
│ RESULT                                   6400 × 4268 │
│ Size ?     [ Same ][ 2× ][ 3× ][● 4× ]               │
│ Quality ?  [● Quick           ][ Best              ] │
│            LocalSR Photo S                   Models… │
╞══════════════════════════════════════════════════════╡
│ Dust & scratches → Upscale 4×                      ▸ │
│ Output     PNG · Desktop                           ▸ │
│                                  28 MB to download ? │
└──────────────────────────────────────────────────────┘
 status bar:           [ Download 28 MB, then upscale ]
```

The Plan sheet's row:

```
│ Dust & scratches   3 marks                           │
│                    MI-GAN                            │
```

The preview patch before the download:

```
 [ Original ][ ↓ Dust & scratches ][ 4× ]
```

Once the model is installed, the panel is pixel for pixel what it is today.

### States

`model` is `repairModel(snapshot.catalog.models, features)`: the catalog model whose
`purposes` include `repair`, when `features` has `image_repair_model`.

| State | Row | Foot, Start, patch |
| --- | --- | --- |
| No `model` (older engine or catalog) | today's row and copy | today's |
| Row not ticked | as today | nothing about the model |
| Ticked, no photo of the start has marks | value `Paint…` | no download asked: the step is left out, as today (`masked_steps`, `App.svelte:996`) |
| Ticked, a photo of the start has marks, model not downloaded | value unchanged (`3 marks`); the row's `title` ends ` · 28 MB download` | cost line `28 MB to download`; Start `Download 28 MB, then upscale`; patch chip `↓ Dust & scratches` with `title` `MI-GAN is not downloaded.` |
| Downloading | unchanged | the status bar's download line; a second Start is refused with `Wait for the download to finish.` |
| Ready | unchanged | nothing; Plan sheet note `MI-GAN` |
| Download failed or offline | unchanged | the failure line of any download, with its retry. A painted photo cannot start until the model is there. |
| Model removed under `Installed` while ticked | as "not downloaded" | |
| Running | the step label `Step 1 of 2 · Dust & scratches`, its bar moving with the regions | Cancel stops between two regions |

"A photo of the start" matters with several photos: the open photo may have no marks while
another has. The download is asked when any photo that Start would send has marks
(`mediaFacts.marked > 0`, `lib/fixRows.ts:123-129`), although the plan line of the open,
unpainted photo does not list the step.

Keyboard and accessibility: nothing is added, so nothing new is reached by Tab. The download is
said in text on the cost line and on the Start button; the row's `title` and the chip's `↓`
repeat it and are never its only place.

### Copy

Each string has today's wording when there is no `model`.

| Where | With the model |
| --- | --- |
| Row `title` (`lib/fixRows.ts:196`) | `Repairs marks that you paint over: dust, scratches, spots, stains.` and, while the model is missing, ` · 28 MB download` |
| Paint bar tip (`PaintBar.svelte:23-24`) | `Paint over each mark: a speck of dust, a scratch, a spot, a stain. When you start, LocalSR fills the painted places before the other fixes and the upscale. Thin marks are filled from the picture right beside them. Wider ones are filled by a small model, MI-GAN, which continues the surroundings into the gap. Use a brush that just covers the mark: a hairline painted with a wide brush is treated as a wide mark. A mark across a face, lettering or other fine detail can still come out wrong. Zoom in with the wheel to see small marks; hold Space and drag to move the picture. B paints, E erases, [ and ] change the brush, and holding M hides the paint. Each photo keeps its own marks, and a photo without marks is left as it is.` and, while the model is missing, ` The first start downloads 28 MB.` |
| Fix tip, last sentence (`FixList.svelte:188`) | unchanged: ` Dust & scratches repairs only the places you paint over.` |
| Plan line, step label, step chip | unchanged: `Dust & scratches` |
| Plan sheet row | name `3 marks` (unchanged); note line `MI-GAN` |
| Patch chip, model missing | `↓ Dust & scratches`, `title` `MI-GAN is not downloaded.` (`patchChips`, `lib/previewPatch.ts:199-202`) |
| Library, `Installed` group | name `MI-GAN`, role `Fills wider marks in Dust & scratches` |
| Library details | text `Fills the wider marks you paint in Dust & scratches by continuing what is around them. Thin marks are filled without it.`; `Download` `28 MB`; License `MIT`, author and source from the catalog; `File`; integrity. No `Scale`, `Speed`, `Memory while running` or `Architecture` line, no `Use for this job`. |
| Library, under Remove while the row is ticked | `Dust & scratches uses this model. Removing it means a 28 MB download again.` |
| `CHANGELOG.md:27-36`, the 0.7.0 bullet | `(OpenCV inpainting, no model)` becomes `(hairlines and specks with OpenCV inpainting, wider marks with MI-GAN, 28 MB, downloaded with the first start)`; `It works for small marks on even areas; large areas or marks across fine detail come out smeared.` becomes `A mark across a face, lettering or other fine detail can still come out wrong.` |

### Behaviour

- **The step.** `repair` keeps `needs_model=False` and gains `model_task="repair"`
  (`src/localsr/core/steps.py:391-403`, the `repair` entry): a step that runs without a model and uses one when
  the catalog has it. The stored entry stays `{ step: 'repair' }`; the window writes no model
  id, so nothing in `toggleRow` changes (`FixList.svelte:120-142`).
- **The plan.** In `stepStageRow` (`lib/plan.ts:342-358`) and in the host's planner, a step
  with a `model_task` and without `needs_model` takes the catalog model that serves it
  (`servesStep`, `plan.ts:224`) when the engine lists `image_repair_model`: the stage then
  carries `model`, `installed`, `canDownload` and `sizeBytes`. Without such a model the stage
  is today's. The cost line, the Start label, `stepStagesReady` and the patch's `↓` chip follow
  from the stage; nothing is wired by hand for them.
- **Several photos.** `downloads` and `stepsReady` (`App.svelte:1031`, `:1592`) also count the
  model when the row is ticked, the open photo has no marks and another photo of the start has.
  The host refuses a painted photo's job when the model it expects is missing:
  `Choose or download the required models before starting.` (the existing text).
- **Names.** `PlanSheet.svelte:85-101`: the `repair` case comes before `stage.model`, so the
  name stays the mark count; the note line is `displayName(stage.model)`.
- **The row.** `FixList.svelte` `rowTitle` (`:101-112`) adds the size while the plan's repair
  stage is not installed. `PaintBar` takes `model: boolean` and `missing: boolean` for its tip.
- **The library.** One helper `isStepModel(model)` (purposes `background` or `repair`)
  replaces the five `!model.purposes.includes('background')` filters (`lib/plan.ts:627`,
  `:635`, `:655`, `lib/fixStages.ts:30`, `ModelLibrary.svelte:177`). `ModelLibrary` keeps step
  models out of every choosing group and out of the counts, and lists the installed ones under
  `Installed`. Their details use the generic pane (`:1724-1895`) without the facts and buttons
  named in Copy.
- **Not touched.** The brush, its sizes and its keys (`lib/maskPaint.ts`), the mask the host
  draws, `_repaired`, the marks' store.

### Engine and host contract (proposed)

| Item | Proposal |
| --- | --- |
| Engine feature | `image_repair_model`, beside `image_repair` |
| Catalog | `migan` in `src-tauri/resources/model-catalog.json`: `purposes: ["repair"]`, `stage: "repair"`, MIT, about 28 MB, ONNX, `automated_download_allowed` |
| Stage sent | today's `{"kind": "repair", "step": "repair", "mask_path": …}` plus the model's path. Without a path the handler fills everything with Telea (`_run_repair_stage`, `src/localsr/worker/image_job.py:553-586`). |
| The split | per painted region, by its width: hairlines Telea, wider regions MI-GAN |
| Progress | the stage reports regions done of regions; cancel is checked between regions (today: between channels, `:582`) |
| Patch | the same split, with enough of the picture around the region that a mark inside the patch is filled as the job fills it (`src/localsr/worker/patch_preview.py`) |
| Timing | `work_profile` drops every stage's `mask_path` before it builds the key (`queue_timing.rs:98-112`) |

Two things to check in the engine task; neither is a UI control:

1. **The starting brush and the 10 px line.** Painting starts with a brush of the longer edge
   divided by 250, between 4 and 64 px (`brushLimits`, `lib/maskPaint.ts:28-35`): 6 px on a
   1600 px photo, 16 px on 12 MP, 24 px on 24 MP. With a fixed 10 px line, a hairline painted
   with the untouched brush on a 12 MP photo is a "wide mark" and goes to MI-GAN, where it was
   measured worse. Measure hairlines painted with the starting brush at 12 and 24 MP. If MI-GAN
   loses there, the line follows the photo (for example 1.5 times the starting brush) in the
   engine. The tip's sentence about the brush stays either way.
2. **Time with paint.** Read, not run: a painted photo's job carries its own `mask_path`
   (`input_for_photo`, `src-tauri/src/commands.rs:1636-1661`), the estimate is built without
   one (`estimate_start_checked`, `:2410-2449`), and `work_profile` keeps the path in the key,
   so the two keys can never match and a start with Dust & scratches shows no time. Write the
   failing host test first. With the path dropped, the measured seconds of painted photos
   include their repairs, which is the only repair time the cost line shows.

---

## What may not work as drawn

Both parts.

- **A heavily marked photo.** The time on the cost line comes from earlier runs of the same
  settings, whose photos had other marks. A photo with a hundred wide marks takes up to about
  25 s longer than the line says.
- **The repair alone at Same size.** The job has no tiles to measure by; if `work_profile`
  gives it no key, the cost line shows no time, as today. The step's bar shows the progress.
- **Offline, first use.** A painted photo cannot be started until 28 MB have been fetched once.
- **Best on a 16 GB Mac** shows no time before two Best edits have finished there. The tip
  holds the 20.6 minutes.
- **The 2 px rule** for the Quality row's column is the one new CSS rule; if it disturbs
  another `.panel-row` inside `.model-section`, scope it with a class on the row.
- **`heavy` follows the window's estimate,** not the engine's own number. `editPeakEstimate`
  is the engine's phase model (11.6 GiB with the reserve for Q2_K); the engine task's draft now
  uses measured peaks for that bundle (11.2 GiB with Quick, 9.5 with Best). The label is right
  for both, and it stays one label for both qualities. If the merged engine admits Q2_K at
  16 GB by another rule, set the two-thirds line so that this pair is `heavy` and the largest
  bundle of each other Mac size is not; `lib/editing.test.ts` pins both.

---

## Implementation checklist

### Part 1

Prerequisite, not UI: the engine and host items of "Engine and host contract" (task Q0).

Files and components:

| File | Change |
| --- | --- |
| `src/lib/types.ts` | `EditSetup.addons?: EditModel[]` (`:103-107`); `StartEditInput.speed?: EditSpeed` (`:111-126`); `UiSettings.edit_speed?: EditSpeed` (`:417-425`) |
| `src/lib/editing.ts` | `EditSpeed`, `editSpeedAddon`, `editSpeed`, `editStepsFor`; `editFitFor` returns `heavy`; `EDIT_FIT_LABELS.heavy`; `editRecommendedSize` adds the add-on's bytes on a card in Quick |
| `src/lib/plan.ts` | `PlanStage['kind']` gains `'edit_addon'` (`:148-161`); `resolvePlan` adds the stage; its options gain `editAddon` |
| `src/lib/jobPlanClient.ts`, `src/lib/api.ts` | the plan request carries the speed; `estimateEdit(input)` |
| `src/EnhancePanel.svelte` | the Quality row in the Model section; the list skips `edit_addon`; `costTime` also from the edit estimate (`:336`) |
| `src/AdvancedSettings.svelte` | props `speed`, `addon`; Steps value, `disabled`, `title` (`:88-101`); the Steps tip (`:85-87`); the line at `:122-126` |
| `src/App.svelte` | `editAddon`, `editSpeedNow`; `editReady` (`:811-817`); `startEdit` (`:4234-4264`); the edit estimate beside `:1563-1586`; pass the two props down |
| `src/ModelLibrary.svelte` | the Lightning row after each family's rows (`:941-988`); its details in the `selectedEdit` branch (`:1571-1724`) without `Needs`, `Largest edit here` and `Use for this job`; the `Installed` group (`:1256-1266`); installed bytes (`:379`) |
| `src/styles.css` | the one `.model-section .panel-row` rule |
| `src/lib/demo.ts` | the catalog split by `kind` (`:2-18`): bundles to `models`, the rest to `addons` (`:156`) |
| `e2e/fake-host.ts`, `e2e/fixtures.ts` | a scenario field for the Mac's memory size; the add-on installed through the scenario's `installed` list; scenarios `qwenMac16` (16 GiB Mac, `image_edit`, `edit_placement`, `edit_lightning`, `qwen_edit_2511_q2_k` chosen, nothing installed) and `qwenCard` (the `cuda` device, Q4_K_M and the add-on installed); `start_edit` records `speed` |
| `src-tauri/src/types.rs`, `settings.rs`, `commands.rs`, `editing.rs`, `protocol.rs`, `job_plan.rs`, `queue_timing.rs`, `results.rs`, `lib.rs` | the catalog split into `models` and `addons`; the setting and its check; `speed` on the start input and `lightning_lora_path` on the wire; the `edit_addon` plan stage; `work_profile` for edits; `estimate_edit`; `quality` in `ui_json` |
| `src/localsr/core/edit_catalog.json`, `image_edit.py`, `edit_memory.py`, `worker/server.py`, `worker/request_validation.py`, `protocol/messages.py`, the protocol schema, `scripts/refresh_edit_catalog.py` | Q0, in progress in the engine task; the four texts above |
| Docs | `CHANGELOG.md` under 0.7.0; `KNOWN_LIMITATIONS.md:134`; `docs/image-editing.md`; `docs/model-licenses.md`; `docs/models/README.md`; `THIRD_PARTY_NOTICES.md` (LightX2V, Apache-2.0); `docs/design/edit-expert.md` points here for Steps |

Tasks, each under an hour for a worker except Q0:

| # | Task | Done when |
| --- | --- | --- |
| Q0 | Engine (in progress) and host: feature, catalog split, `speed`, Q2_K at 16 GB, the texts | pytest and `cargo test` cases below pass |
| Q1 | Rules and wiring, no markup: `types.ts`, `lib/editing.ts`, `lib/plan.ts`, `startEdit`, the setting in the host | `lib/editing.test.ts`, `lib/plan.test.ts`, parity and the changed `App.test.ts` start case pass |
| Q2 | The row, Steps, the Outputs label, the CSS rule | App.test block "Edit quality", `AdvancedSettings.test.ts` and `edit-quality.e2e.ts` pass |
| Q3 | The library row, its details, `Installed` | `ModelLibrary.test.ts` block "Lightning" passes |
| Q4 | Time: `work_profile`, `estimate_edit`, the second estimate | host cases "edit timing" pass; the cost line shows a time after two fake runs |
| Q5 | Screenshots, docs | the three new shots exist in all four projects; no old baseline differs |

Tests:

| Where | Case | Expected |
| --- | --- | --- |
| pytest `tests/test_edit_lightning.py` (the engine task's new file), `tests/test_image_edit.py` | `speed: lightning` with the file | 4 steps, cfg 1, the LoRA passed to the runtime; without the file a refusal |
| | Q2_K, 16 GiB unified, 12 GiB free | admitted; with 8 GiB free the refusal text of this spec |
| `cargo test` | settings round trip | `edit_speed` absent, `lightning`, `full`; any other word refused |
| | the embedded catalog | `models` has the nine bundles, `addons` the Lightning entry; a start with the add-on's id as `model_id` is refused |
| | `job_plan.rs::lightning_adds_the_addon_stage` | two stages, the second `edit_addon` with the file's size while missing; `full` and FLUX.2 klein give one |
| | parity fixture (`UPDATE_JOB_PLAN_FIXTURE=1`, `lib/jobPlanParity.test.ts`) | new cases: Qwen `lightning` with the file missing and installed, Qwen `full`, klein |
| | `queue_timing.rs` (rewrites `edit_steps_do_not_reuse_restoration_tile_estimates`, `:171`) | an edit has a key; `lightning` and `full` differ; another size differs; no restoration job shares it |
| | `commands/tests.rs::estimate_edit_needs_two_runs` | none after one run, the lower middle after three |
| | `results.rs` | an edit with `speed: lightning` reads `quick`, with `full` and an add-on for its family `best`; one without the field reads empty |
| vitest `lib/editing.test.ts` | `editSpeedAddon` | the add-on for a Qwen bundle with the feature; none without the feature, for klein, for `__custom__` |
| | `editSpeed` | absent setting is `lightning`; `full` kept; null without an add-on |
| | `editStepsFor` | 4 in Quick whatever `edit_steps` holds; saved or 40 in Best |
| | `editFitFor` | Q2_K at 16 GiB `heavy`; Q3_K_S at 24, Q4_K_M at 32, Q6_K at 36, Q8_0 at 48 `runs`; Q3_K_S at 16 `too_heavy` |
| | `recommendAnyEditModel` at 16 GiB | still FLUX.2 klein 4B Q4_0 |
| | the demo snapshot | `edit.models` has no entry with a `kind`; `edit.addons` has one |
| `lib/plan.test.ts` | Edit, Quick, file missing | stages `edit`, `edit_addon`; `pendingDownloads` sums both; Best has one stage |
| `AdvancedSettings.test.ts` | Quick | `#edit-steps` disabled, value `4`, the `title`; the line reads `Quick runs 4 steps.`; no `updateSettings` call |
| | Best with `edit_steps: 30` | enabled, value `30` |
| `AdvancedHelpTips.test.ts` | Steps tip with the row | ends with the added sentence |
| `App.test.ts`, block "Edit task" (`:680`) | the exact start object | gains `speed: 'full'` for FLUX.2 klein |
| `App.test.ts`, new block "Edit quality" | Qwen chosen, feature and file listed | radiogroup `Quality` after the model's note, `Quick` checked; `About Quality` holds the tip with the Mac sentence |
| | klein chosen; feature missing; own model | no such group |
| | press `Best` | `saveSettings` gets `edit_speed: 'full'`; the cost line loses the 850 MB |
| | Quick, file missing, model installed | cost line `850 MB to download`; Start `Download 850 MB, then edit`; `Quick`'s `title` ends ` · 850 MB download` |
| | both missing | `Download 12.4 GB, then edit` |
| | Start in Quick, all installed | `start_edit` carries `steps: 4`, `speed: 'lightning'`; in Best `steps: 40`, `speed: 'full'` |
| | model list | one row, `Edit`; no row named `Quick` |
| | 16 GiB Mac, Q2_K | the note holds `Needs most of this Mac's memory` |
| | a `job_failed` with the admission text | shown in `Last error` |
| | job running | both radios disabled |
| | an Edit result with `ui.quality: 'quick'` | its Outputs line starts `Quick · ` |
| `ModelLibrary.test.ts`, block "Lightning" | Edit group | row `Lightning 4-step` last under Qwen, not under klein; no `Chosen` pill |
| | selected | head, text, `Download 850 MB`; no `Use for this job`; installed: `Remove from this computer` and, with Quick applying, the warning line |
| | `Installed` | listed when installed |
| e2e, new `edit-quality.e2e.ts` | `qwenMac16`: the row sits under the model | the control's left edge equals the model name's (± 0.5 px); the tops of the model row and of Advanced are where they are without the row plus the row's height |
| | arrow keys | Right checks `Best`, Left `Quick`; both show a focus ring (`hasFocusRing`, `e2e/keyboard.e2e.ts:11-17`) |
| | nothing is cut at 1024 × 700, at 880 × 640 and at `interface_scale: 125` | `scrollWidth <= clientWidth` for the label, both segments and the note `Compact · Needs most of this Mac's memory` |
| | the tip | wraps inside the column and the window (the loop of `layout.e2e.ts`) |
| | Start with both missing | the button reads `Download 12.4 GB, then edit`; after the fake download `start_edit` has `speed: 'lightning'` |
| | `qwenCard`, press `Best`, Start | `steps: 40`, `speed: 'full'` |
| | open Advanced in Quick | `#edit-steps` is disabled and reads `4`; `#edit-size` has not moved |
| `e2e/downloads.e2e.ts` | "Edit download shows once…" with `qwenMac16` | one status line for the two files, one percentage |
| `e2e/library.e2e.ts` | the Lightning row at both sizes | nothing in the row or the details is cut |
| `e2e/overlaps.e2e.ts`, `e2e/edit-expert.e2e.ts` | unchanged | pass (FLUX.2 klein has no row) |

`@visual` baselines (`npm run e2e:update`, four projects: `chromium-1024`, `chromium-1440`,
`webkit-1024`, `webkit-1440`):

- No existing baseline changes. `edit-expert-windows` and `download-waiting` use FLUX.2 klein,
  which has no Quality row, and the shared fixtures do not list `edit_lightning`. A differing old
  baseline is a bug in "When the row shows".
- New in `screens.e2e.ts`: `edit-quality-mac` (`qwenMac16`, Quick checked, `12.4 GB to
  download`), `edit-quality-advanced` (the same with everything installed and Advanced open:
  the disabled Steps field), `library-edit-lightning` (the Edit group with the Lightning row
  selected). Twelve files.

### Part 2

Prerequisite, not UI: the engine items of "Engine and host contract" (task M0).

Files and components:

| File | Change |
| --- | --- |
| `src/localsr/core/steps.py`, `src-tauri/resources/steps.json` | `model_task="repair"` on `repair`; regenerated with `scripts/export_desktop_steps.py`, then `--check` |
| `src/localsr/worker/image_job.py`, `patch_preview.py`, `worker/server.py`, new ONNX fill module | M0: the split, the feature, the progress, the patch |
| `src-tauri/resources/model-catalog.json` | the `migan` entry |
| `src-tauri/src/job_plan.rs`, `commands.rs`, `steps.rs`, `queue_timing.rs` | the optional model on the `repair` stage; the model's path on a painted photo's job; the refusal when it is missing; `mask_path` out of the work key |
| `src/lib/stepRows.ts` | `repairModel(models, features)`; `isStepModel(model)` |
| `src/lib/plan.ts`, `src/lib/fixStages.ts` | `stepStageRow` for an optional model; the filters use `isStepModel` |
| `src/lib/fixRows.ts` | `copyForRow` takes whether a model serves the row (`:194-197`) |
| `src/FixList.svelte` | `rowTitle` adds the download size (`:101-112`) |
| `src/PaintBar.svelte` | props `model`, `missing`; the two tips (`:23-24`) |
| `src/PlanSheet.svelte` | name and note of the `repair` row (`:85-101`, the `meta` chain after it) |
| `src/App.svelte` | `downloads` and `stepsReady` count the model when any photo of the start has marks; pass the props to `PaintBar` |
| `src/ModelLibrary.svelte` | `isStepModel` in place of the `background` filter (`:177`); step models under `Installed`; their details and the warning line |
| `e2e/fake-host.ts`, `e2e/fixtures.ts` | scenario `scratchedModel`: `scratched` plus `image_repair_model`, MI-GAN not installed |
| Docs | `CHANGELOG.md:27-36`; `KNOWN_LIMITATIONS.md`; `docs/architecture/steps.md`; `docs/model-licenses.md`; `THIRD_PARTY_NOTICES.md` (MI-GAN, MIT); `docs/design/tools-wave.md`, section 3, points here; `docs/design/scan-and-background.md`, "Backgr. model", notes that BiRefNet is now under `Installed` |

Not touched: `lib/maskPaint.ts`, `MaskPaint.svelte`, `repair_mask.rs`, `lib/planLine.ts`,
`lib/jobSteps.ts`, `lib/recipes.ts`, `workflows.json`, `styles.css`.

Tasks:

| # | Task | Done when |
| --- | --- | --- |
| M0 | Engine: the split, `image_repair_model`, progress by region, the patch, the brush check of "Engine and host contract" | the pytest cases pass; the brush measurement is written down |
| M1 | Catalog, registry, both planners, the job's model path, the refusal | host cases and parity pass |
| M2 | Window: `repairModel`, the copy variants, the row `title`, the Plan sheet note, the download across several photos | App.test block "Dust & scratches, model" passes |
| M3 | Library: `isStepModel`, `Installed`, the details | `ModelLibrary.test.ts` block "Step models" passes |
| M4 | Timing: the failing test, then `mask_path` out of the key | the host case passes |
| M5 | e2e, the screenshot, docs | the new shot exists in all four projects; no old baseline differs. By hand on a scanned print: a coffee stain and a torn corner painted wide come out without smear, a hair painted with the starting brush is as clean as in 0.7.0 before this change. |

Tests:

| Where | Case | Expected |
| --- | --- | --- |
| pytest `tests/test_repair_step.py` | a wide blotch with the model | filled by MI-GAN (a stub model in the test); a hairline in the same mask by Telea; pixels outside the paint unchanged |
| | no model path | today's result, bit for bit |
| | progress and cancel | one progress event per region; cancel between regions stops the job |
| | 16-bit input | every pixel outside the paint keeps its 16-bit value |
| `tests/test_patch_preview.py` | a wide mark inside the region | the patch equals the job's picture at those pixels |
| `tests/test_steps_registry.py` | `repair` | `needs_model` false, `model_task` `repair`, still mask-required |
| `cargo test` `job_plan.rs` | painted photo, feature and catalog entry | the `repair` stage has `model_id` `migan` and its size while missing; without the feature the stage is today's |
| | parity fixture | new cases: repair with the model missing, installed, and without the feature |
| `commands/tests.rs` | a painted photo's request | carries the model's path; an unpainted photo's has no repair stage; a painted photo with the model missing is refused with the existing text |
| | `a_painted_photo_gets_an_estimate` (written first, fails on `7c4165ce`) | two finished painted runs give the next painted start a time |
| vitest `lib/stepRows.test.ts` | `repairModel` | `migan` with the feature; undefined without the feature or the entry |
| | `isStepModel` | true for `birefnet_general` and `migan`, false for a fix model |
| `lib/fixRows.test.ts` | row `title` | today's without a model; the new one with it |
| `lib/plan.test.ts` | `stepStageRow` | the stage carries the model; `pendingDownloads` counts 28 MB; `stepStagesReady` is false until installed |
| `lib/previewPatch.test.ts` | the repair entry | `modelName` `MI-GAN`; a `missing` step gives `↓ Dust & scratches` |
| `App.test.ts`, new block "Dust & scratches, model" | ticked, 3 marks, model missing | cost line `28 MB to download`; Start `Download 28 MB, then upscale`; the row's `title` ends ` · 28 MB download`; value still `3 marks` |
| | ticked, no marks anywhere | no download in the cost line or on Start |
| | two photos, the open one unpainted, the other painted | the download is asked |
| | model installed | no cost line entry; the Plan sheet's row reads `3 marks` with the note `MI-GAN` |
| | feature not listed | the row `title` and the paint bar tip are today's strings |
| | paint bar open, model listed | `About Dust & scratches` holds the new tip |
| `ModelLibrary.test.ts`, block "Step models" | MI-GAN and BiRefNet installed | both under `Installed`; neither in `Fix noise, blur, JPEG, colour` nor in any count; details without `Scale` and `Use for this job`; the warning line while the row is ticked |
| e2e `enhance-panel.e2e.ts` | `scratchedModel`: tick the row | the cost line reads `28 MB to download`; the tops of the Fix rows and the Result block are where they are in `scratched` (± 0.5 px) |
| `e2e/preview-patch.e2e.ts` | the model missing | chip `↓ Dust & scratches`; the other steps still show |
| `e2e/downloads.e2e.ts` | Start with the model missing | one status line `…, then upscale`; the job starts when the fake download ends |

`@visual` baselines:

- No existing baseline changes: `panel-dust` and `viewer-paint` use `scratched`, which does
  not list `image_repair_model`, and the library shots show other groups.
- New in `screens.e2e.ts`: `panel-dust-download` (`scratchedModel`, the row ticked, 3 marks,
  `28 MB to download`). Four files.

## Open questions

1. **People who already edit with Qwen get Quick with 0.7.0.** Built this way: `edit_speed`
   is absent in their settings, so the row shows `Quick`, the next Start reads `Download 850 MB,
   then edit`, and the same seed gives another picture than in 0.6. Every other addition of
   0.7.0 leaves existing behaviour alone until a control is touched. The other way, one line in
   `editSpeed`: absent means `full` when the model is installed and the Lightning file is
   not, so only new downloads of Qwen start with Quick.
