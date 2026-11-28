# Edit: the Expert group

## Decisions on the open questions (2026-11-27, coordinator, Hermes can change them)

- GPU memory stays a select (`Automatic`, `Up to N GB`), as built here: it cannot hold an invalid value.
- `Allow any edit size` is not offered on Macs (unified memory). An oversized edit there pushes the whole Mac into swap instead of failing cleanly; the engine ignores the flag on unified memory. The tester who asked has a 12 GB NVIDIA card.
- The completion note names the size actually used when the engine edits at a smaller size than requested (point 6 of the summary).

Addendum to `enhance-panel.md` for LocalSR 0.7.0. UI line numbers refer to
`w/070-enhance-panel` at `c7e6aa6d`; engine and host line numbers to `w/070-edit-expert`
(base `b14cf043`, with the engine task's uncommitted work as read on 2026-11-27 16:20). Every
reference also names its function or text, because both branches are still moving.

A tester with a 12 GB NVIDIA card measured Edit (FLUX.2 klein 4B, 1024 px): 160 s with the text
encoder and the VAE on the CPU, 19 s with everything on the GPU. LocalSR gave him 8.66 GB of
his card and 768 px although 1024 px ran. He asked to set these himself and to get a "not
enough memory" message when he overdoes it.

Two things answer him. The engine task fixes the automatic case. This addendum adds four
controls for the rest, and it removes a second cap that sits in the UI and would keep him at
768 px even with the new engine (see "The recommended size").

## What the engine offers

Read from the engine task's code and tests; check the names again when it is merged.

| Field | Values | Engine behaviour |
| --- | --- | --- |
| `edit_text_encoder`, `edit_vae` | `auto`, `gpu`, `cpu` | Feature `edit_placement` (`server.py:215`). Automatic on a card with its own memory: on the GPU when it fits the budget (`_choose_discrete_placement`, `edit_memory.py`). Automatic on a Mac: on the CPU (`_choose_unified_placement`). Forced `gpu` that does not fit raises `Keeping the text encoder on the GPU needs about … GiB of VRAM, but only … GiB is budgeted.` Mage-Flow on a Mac refuses `gpu`. |
| `edit_vram_limit_mb` | `0` or 1024 up to the card's memory in MiB | Feature `edit_expert`. Replaces the automatic budget `min(90% of the card, free − max(1 GiB, 8% of the card))`. Ignored on a Mac, with a log line. |
| `edit_allow_any_size` | bool | Feature `edit_expert`. Off: a card with its own memory gets the largest of 1024, 768, 512 at or under the request for which `diffusion file + working memory ≤ budget` and the plan passes; a Mac keeps its caps (under 36 GB: 512, under 48 GB: 768). On: the requested size is used on both. |
| Failure | | `Not enough memory for this size. Choose a smaller size or let LocalSR place the models automatically.` (`EDIT_OUT_OF_MEMORY_MESSAGE`, `memory_errors.py`), as the job's `error_message`, when sd-cli runs out of memory or the edit no longer fits before loading. When the memory supervisor stops a running edit, the `error_message` is its own sentence, `Editing stopped to protect system memory: <reason>. <advice>` (`EditMemoryStop`). |
| Completion note | | `placement_note` (`image_edit.py:413`): `Text encoder and VAE ran on the GPU.`, `Text encoder ran on the GPU; VAE ran on the CPU.`, `VAE ran on the GPU; text encoder ran on the CPU.`, `Text encoder and VAE ran on the CPU.`, `Diffusion model, text encoder, and VAE ran on the CPU.` |

The host takes the two new fields on `start_edit` and falls back to the saved settings when
they are missing (`start_edit_checked`, `commands.rs:137-160`), so the UI always sends all four.

## Decisions

| Point | Decision | Reason |
| --- | --- | --- |
| Where | A second disclosure, `Expert`, closed, as the last item inside Edit's Advanced, after the memory line. | Edit keeps its Advanced and has no Plan sheet (`enhance-panel.md:53`, `:464`). Settings › Performance holds defaults for every task; these four belong to one card and one edit model and are tuned while trying edits. Closed, because wrong values make Edit slower or make it fail, and Size, Steps and Seed stay the everyday controls. Last, so opening it moves no row, and it sits under Hardware and the memory line it belongs to. |
| Names | `Text encoder`, `VAE`, `GPU memory`, `Allow any edit size`. Options `Automatic`, `GPU`, `CPU`. | The words the tester used and the words the engine's completion note uses, so the setting and the result read alike. Each tip explains its term in its first sentence. `Automatic` as in the Video format select (`OutputSheet.svelte:74`). |
| GPU memory control | One native select: `Automatic`, then `Up to 4 GB` to the card's size in whole GB. | Selects are what Advanced uses (Edit size, Hardware). No typing, no invalid value, the bounds are visible, and `Automatic` is an ordinary option to go back to. 1 GB steps are finer than the difference between two sizes (1.25 GB and more). |
| GPU memory on a Mac | Not shown. | The engine ignores it there. "Rows never move" is about one session on one computer; whether the card has its own memory cannot change while the app runs (`editingDevices`, `editing.ts:112-116`). A row that can never apply is absent, as the accent switch is on Windows. |
| Size select | Off: sizes above the recommendation are disabled and read `· needs more memory`, as today. On: every size can be chosen; those above read `· may not fit`, also in the closed select. | The mark is in the row itself, so no prose is added. |
| Recommendation | `editRecommendedSize`, the engine's own fit for this model, card and limit, replaces the fixed 12 and 16 GB steps of `editSizeLimit`. | Otherwise the UI still caps a 12 GB card at 768 px. |
| Reset | `Reset to automatic`, the group's last line, always there, disabled while nothing is changed. | The failure message says "let LocalSR place the models automatically"; this is where. A line that came and went would move the rows under the pointer. |
| What is set by hand shows outside | The Expert head reads `Automatic` or `Changed`. The Model line gains ` · Expert settings` while anything is changed. | A forgotten override that makes every edit eight times slower must be visible with Advanced closed. Same idea as `Your pick` on a hand-picked model. |
| Placement before a run | Not predicted. | It depends on the memory that is free when the edit starts, which only the engine reads. After the run the status bar shows the engine's note under `Complete`, as today (`workerEvents.ts:515`). |
| Start | Always sends the four values that apply (see "State rules"). | The host would otherwise use saved values the UI has ruled out, such as a limit saved for another card. |

## Layout

Inside `.advanced-controls`, 308 px wide, label column 92 px as the rows above.

Closed (the default), on any computer:

```
│ Advanced                         Size, steps, seed ⌃ │
│ Edit size ?     [ 1024 px · longest edge         ▾ ] │
│ … Steps, Seed, Save to, Hardware, low priority …     │
│ [ low memory pressure ?                    Refresh ] │
│ Expert                                   Automatic ⌄ │
```

Open, card with its own memory, two things changed:

```
│ Expert                                     Changed ⌃ │
│ Text encoder ?  [ Automatic                      ▾ ] │
│ VAE ?           [ Automatic                      ▾ ] │
│ GPU memory ?    [ Up to 11 GB                    ▾ ] │
│ [✓] Allow any edit size ?                            │
│ Reset to automatic                                   │
```

Open, on a Mac (no GPU memory row):

```
│ Expert                                   Automatic ⌃ │
│ Text encoder ?  [ Automatic                      ▾ ] │
│ VAE ?           [ Automatic                      ▾ ] │
│ [ ] Allow any edit size ?                            │
│ ░░ Reset to automatic ░░                             │
```

The Edit size list, Qwen Image Edit 2511 Compact on a 12 GB card:

```
 Allow any edit size off                 on
 512 px · longest edge                   512 px · longest edge
░768 px · longest edge · needs more memory░   768 px · may not fit
░1024 px · longest edge · needs more memory░  1024 px · may not fit
```

The Model line, and the status bar after the edit:

```
│ MODEL                                                │
│ Edit    FLUX.2 klein 4B                      Change… │
│         Balanced · Expert settings                   │
 status bar:  Complete · Text encoder and VAE ran on the GPU.
```

## Copy

| Where | String |
| --- | --- |
| Disclosure | `Expert`; summary `Automatic` or `Changed` |
| Row labels | `Text encoder` · `VAE` · `GPU memory` · `Allow any edit size` |
| Text encoder, VAE options | `Automatic` · `GPU` · `CPU` |
| GPU memory options | `Automatic` · `Up to 4 GB` … `Up to 12 GB` |
| Edit size options | `512 px · longest edge` · disabled: `1024 px · longest edge · needs more memory` (unchanged) · allowed above the recommendation: `1024 px · may not fit` |
| Link | `Reset to automatic` |
| Model line tag | ` · Expert settings`; the note's `title` lists what is set, after the variant: `Balanced · Text encoder on the CPU · VAE on the GPU · GPU memory up to 11 GB · Any edit size` |
| Selects disabled, `title` | `Mage-Flow runs on the CPU on a Mac.` |
| Edit size tip (replaces today's, `AdvancedSettings.svelte:46`) | `The photo is scaled down to this longest edge before editing, and the result has that size. Larger sizes keep more detail but need more memory. Sizes above what LocalSR recommends for this computer and model are disabled, unless Expert, further down, allows them.` |
| Text encoder tip, card with its own memory | `The text encoder is the part of the model that reads your description. Automatic runs it on the GPU when it fits there beside the rest of the model, which is much faster, and on the CPU when it does not. GPU always runs it there; if the GPU's memory is too small, the edit stops with a not-enough-memory message. CPU frees GPU memory and makes every edit much slower.` |
| Text encoder tip, Mac | `The text encoder is the part of the model that reads your description. Automatic runs it on the CPU, which needs the least memory. GPU can be faster but needs more memory at once; if the Mac has too little, the edit stops with a not-enough-memory message.` |
| VAE tip, card with its own memory | `The VAE turns your photo into the form the model works on and turns the result back into a picture. Automatic runs it on the GPU when it fits, which is faster, most of all at large sizes. GPU always runs it there and can stop the edit with a not-enough-memory message. CPU frees GPU memory and is slower.` |
| VAE tip, Mac | `The VAE turns your photo into the form the model works on and turns the result back into a picture. Automatic runs it on the CPU. GPU can be faster but needs more memory at once; if the Mac has too little, the edit stops with a not-enough-memory message.` |
| GPU memory tip | `How much of the graphics card's memory an edit may use. Automatic takes what is free and keeps back at least 1 GB for the screen and other apps. A higher limit can allow a larger Edit size or keep more of the model on the GPU; if that much is not really free, the edit gets much slower or stops with a not-enough-memory message. A lower limit leaves room for other apps.` |
| Allow any edit size tip | `Lets you choose any Edit size, also one above what LocalSR recommends for this computer and model. LocalSR then uses your size as it is and no longer picks a smaller one that fits. If the memory is not enough, the edit gets very slow or stops with a not-enough-memory message. Untick it, or choose a smaller size, to be safe again.` |

No tip says "diffusion". Failure and completion texts are the engine's and are shown as they
come: the failure in the `Last error` block, which sits directly above Advanced
(`EnhancePanel.svelte:713-730`), the note in the status bar.

## State rules

`editExpert(settings, model, device, features)` gives the four values that apply. The rows, the
summary, the Model tag and Start all read it.

| State | Group | Values that apply |
| --- | --- | --- |
| Engine without `edit_placement`, or no editing device | not rendered | `auto`, `auto`, `0`, `false` |
| Engine with `edit_placement`, without `edit_expert` | Text encoder, VAE, Reset | limit `0`, any size `false` |
| Card with its own memory (`device.id !== 'mps'`, as `editCapacity`, `editing.ts:133`) | all four rows, Reset | as saved |
| Mac | no GPU memory row | limit `0` |
| Mac with Mage-Flow | both selects disabled, showing `Automatic`, with the `title` | placements `auto` |
| Job running | everything disabled by the panel's `fieldset`, as the rest of Advanced | unchanged |

- **Summary and tag**: `Changed` and ` · Expert settings` when `editExpertChanges(expert)` is
  not empty, so a value that does not apply here (a limit on a Mac) does not count.
- **Reset** is enabled on the same condition and saves `{ edit_text_encoder: 'auto', edit_vae:
  'auto', edit_vram_limit_mb: 0, edit_allow_any_size: false }` in one call. Edit size is not
  touched.
- **GPU memory options**: `Automatic` (0), then for `n` from 4 to
  `floor((total_memory + 0.25 GiB) / GiB)`: `Up to n GB` with the value
  `min(n × 1024, floor(total_memory / MiB))`, the engine's upper bound. 4 GB is the engine's own
  floor for the automatic budget. A card under 4 GB has `Automatic` alone. A saved value that is
  not in the list is shown and sent as the largest option not above it, else the smallest; the
  saved value stays until the user picks. Another card in Hardware rebuilds the list.
- **Edit size**: shown and sent is `editSizeFor`: the saved size with any size on, else
  `min(saved, recommended)` as today (`AdvancedSettings.svelte:50`, `App.svelte:3484`, `:1309`).
- **Ticking `Allow any edit size`** saves `{ edit_allow_any_size: true, edit_max_dimension:
  <the size shown> }`, so the tick alone changes no size. Unticking saves the flag only; the
  select then shows the clamp.
- **A changed limit or placement** moves the recommendation at once, because it is computed
  from them. The free memory in it is the last hardware reading; `Refresh` in the memory line
  reads it again.
- All four are saved settings like Edit size, Steps and Seed, not session choices
  (`sessionKeys`, `App.svelte:688`).

## The recommended size

`editSizeLimit` (`editing.ts:218-227`) caps cards under 16 GB at 768 px, and Start sends
`min(saved, that)` (`App.svelte:3484`). With the new engine alone the tester still gets 768.
`editRecommendedSize(model, capabilities, device, expert)` replaces it at its three call sites
and repeats the engine's fit:

- No device, or a card with its own memory and no model: 512.
- Mac: 512 under 36 GB of `system_ram_total`, 768 under 48 GB, else 1024 (unchanged).
- Card with its own memory: `budget` is the limit in bytes when one applies, else
  `min(0.90 × total, free − max(1 GiB, 0.08 × total))` with `free = free_memory || total_memory`.
  Working memory is `1 GiB × (size / 512)²`. From 1024 down, the first size that fits, else 512.
  A size fits when `diffusion + working ≤ budget`, and with VAE on `gpu` also
  `diffusion + vae + working ≤ budget`, and with Text encoder on `gpu` also
  `diffusion + (vae when it is on the GPU) + text_encoder + vision + working ≤ budget`. The VAE
  is on the GPU when forced, or on Automatic when its line holds.

The engine also checks system RAM and reads free memory at the start, so it can still pick a
smaller size than the UI showed; see "Needed from the engine".

## Files to change

| File | Change |
| --- | --- |
| `desktop/src/lib/types.ts` | `EditPlacement`; `StartEditInput` (110-122) gains `edit_vram_limit_mb?: number`, `edit_allow_any_size?: boolean`; `UiSettings` (387-391) gains the four fields, optional. |
| `desktop/src/lib/editing.ts` | `editRecommendedSize` in place of `editSizeLimit` (218-227); new `editExpert`, `editExpertChanges`, `editVramOptions`, `editSizeFor`. |
| `desktop/src/AdvancedSettings.svelte` | Prop `features: string[]`. 21: the recommendation. 44-58: tip text, value from `editSizeFor`, options marked as in Copy. After `<RunSettings>` (106-115): the Expert disclosure (the `.disclosure` button of 25-39 with `aria-expanded`) and `<div class="expert-controls">` with three `.field-row`s as 42-59, one `.check-line` as `RunSettings.svelte:191-203`, one `.link-button`. Ids `edit-expert`, `edit-text-encoder`, `edit-vae`, `edit-vram-limit`, `edit-any-size`, `edit-expert-reset`. |
| `desktop/src/EnhancePanel.svelte` | 676-682 and 698-709: the tag and the `title`. 750-759: pass `features={snapshot.engine?.features ?? []}`. |
| `desktop/src/App.svelte` | 699 and 1309: recommendation and `editSizeFor`. `startEdit` (3474-3497): `max_dimension` from `editSizeFor`, plus the four values of `editExpert`. |
| `desktop/src/ModelLibrary.svelte` | 185, 1580-1581: `Largest edit here` from `editRecommendedSize` for the bundle shown. |
| `desktop/src/styles.css` | 2237: `.expert-controls` joins the `.advanced-controls` rule. Nothing else. |
| `desktop/src/lib/demo.ts` (80-83), `desktop/e2e/fake-host.ts` (11, 290-312) | Defaults for the four fields; a device kind `'cuda'`: `cuda:0`, `NVIDIA GeForce RTX 3060 (CUDA)`, not integrated, 12 GiB total, 10.66 GiB free, `device_id: 'cuda:0'`. |
| `desktop/src-tauri/src/types.rs` (479-490, 684-688), `commands.rs` (the edit check in settings validation, `unsupported edit size, step count, seed or model`) | **Missing today:** `edit_text_encoder` and `edit_vae` exist only on the start request (`editing.rs:138-141`; `types.ts:120-121` is `StartEditInput`). `UiSettings` needs both, default `"auto"`, validated as `auto`, `gpu` or `cpu`, or the two selects cannot be saved. The engine task has added the other two. |

## Tests

### vitest

`lib/editing.test.ts` (36-38 are rewritten for the new name)

| Case | Expected |
| --- | --- |
| 12 GiB card, 10.66 GiB free, FLUX.2 klein 4B Q4_0 | 1024 |
| the rows of `test_8gb_card_picks_largest_size_that_fits` (`tests/test_image_edit.py`), as merged | the same sizes |
| the same 12 GiB card, limit 6144 | 768 |
| Qwen Q3_K_S on it | 512; with limit 11264: 768 |
| FLUX.2 klein 4B Q8_0 on it | 1024; with Text encoder `gpu`: 512 (add the same case to pytest if it is missing) |
| `mps` with 24, 36, 48 GB; no device | 512, 768, 1024; 512 |
| `editVramOptions`, 12 GiB − 32 MiB card | `Automatic`, `Up to 4 GB` … `Up to 12 GB`; last value 12256; a 3 GiB card: `Automatic` alone |
| `editExpert` | saved limit 16384 on that card: 12256; no `edit_placement`: all automatic; no `edit_expert`: limit 0, any size false; `mps`: limit 0; Mage-Flow on `mps`: placements `auto` |
| `editExpertChanges` | empty at the defaults; the four phrases of the Copy table |

`AdvancedSettings.test.ts` (the four existing cases stay)

| Case | Expected |
| --- | --- |
| open Advanced | button `Expert`, `aria-expanded="false"`, text `Automatic`; no `Text encoder` |
| `edit_vae: 'cpu'` saved | the head reads `Changed` |
| open Expert, CUDA fixture | `Text encoder`, `VAE`, `GPU memory`, `Allow any edit size`, `Reset to automatic` (disabled) |
| open Expert, Mac | no `GPU memory` |
| Text encoder to `CPU`; GPU memory to `Up to 8 GB` | `updateSettings({ edit_text_encoder: 'cpu' })`; `({ edit_vram_limit_mb: 8192 })` |
| CUDA fixture, Qwen Q3_K_S, saved size 1024, tick the box | one call `{ edit_allow_any_size: true, edit_max_dimension: 512 }` |
| the same with the flag saved | no option disabled; the 1024 option reads `1024 px · may not fit`; value `1024` |
| Reset with two changes | one call with the four defaults |
| features without `edit_placement` | no `Expert` button |
| Mage-Flow on a Mac, `edit_vae: 'gpu'` saved | both selects disabled, value `auto`, the `title` |

`AdvancedHelpTips.test.ts`: both variants open Expert too; `expected` gains `Text encoder`,
`VAE`, `Allow any edit size`, and `GPU memory` for a new CUDA variant.

`App.test.ts`: the exact start object at 680-691 gains `edit_text_encoder: 'auto'`, `edit_vae:
'auto'`, `edit_vram_limit_mb: 0`, `edit_allow_any_size: false`. New: with `edit_vae: 'cpu'`
saved, the Model list holds `Expert settings`. New: a `job_failed` with the engine's
not-enough-memory text shows it in `Last error` (as 2702-2706).

### Playwright, new `desktop/e2e/edit-expert.e2e.ts`

Scenario: `devices: 'cuda'`, Windows platform, features `image_edit`, `edit_placement`,
`edit_expert`, FLUX.2 klein 4B Q4_0 installed and chosen.

| Case | Expected |
| --- | --- |
| open Advanced, saved size 1024 | `#edit-size` is `1024`, its option enabled (the tester's case) |
| open Expert | the tops of `#edit-size`, `#device` and the memory line are where they were (± 0.5 px) |
| column | the left edges of `#edit-text-encoder`, `#edit-vae`, `#edit-vram-limit` equal `#device`'s (± 0.5 px) |
| labels at 880 × 640 and at `interface_scale: 125` | no label or tip overlaps its select; `scrollWidth <= clientWidth` for the three labels |
| the four tips | each wraps inside the column and the window (the loop of `layout.e2e.ts:338-381`, with Expert open) |
| Text encoder to `CPU` | the saved settings hold `cpu`; head `Changed`; the Model note holds `Expert settings`; `Reset to automatic` restores all four, head `Automatic`, tag gone |
| Qwen Q3_K_S | 768 and 1024 disabled; GPU memory `Up to 11 GB` enables 768 |
| the same, tick the box, choose 1024, Start | `start_edit` carries `max_dimension: 1024`, `edit_allow_any_size: true`, `edit_vram_limit_mb: 11264` |
| Mac scenario | no `#edit-vram-limit`; the other rows are there |
| keyboard | the disclosure, the selects, the box and the link show a focus ring (`keyboard.e2e.ts:96-116` passes with Expert open) |

`screens.e2e.ts`: one new shot, `edit-expert-windows`, Expert open.

## Tasks

Each under an hour for a worker; T2 needs T1.

| # | Task | Done when |
| --- | --- | --- |
| T1 | Rules and wiring, no markup: `types.ts`, the five functions in `lib/editing.ts`, the three call sites, `startEdit`, `demo.ts`; the two placement fields in the host's `UiSettings` with validation and a round-trip test. | `lib/editing.test.ts` and the changed `App.test.ts` case pass; `./local-ci.sh typecheck`, `frontend` and `rust` pass. Edit on a 12 GB CUDA fixture offers 1024 px. |
| T2 | The group and what shows outside it: `AdvancedSettings.svelte`, the Model tag in `EnhancePanel.svelte`, the one CSS selector, the `'cuda'` device kind, the component tests, `edit-expert.e2e.ts`, the screenshot, one line in `CHANGELOG.md`. | The vitest and Playwright tables above pass; `layout.e2e.ts` and `keyboard.e2e.ts` pass unchanged. |

## Needed from the engine

Not part of the UI tasks; each is a small engine or host change.

1. **The size that was used.** With any size off, the engine may edit at a smaller size than
   requested (less memory free than at the last reading, or little system RAM), and nothing
   says so: the note names only the placement, and the Outputs row computes its size from the
   request (`OutputsList.svelte:59-63`). The completion note should begin with it, for example
   `Edited at 768 px: 1024 px did not fit the free GPU memory.`, and the job should carry the
   size used.
2. **The forced-placement message** says `VRAM`, `GiB` and `budgeted`. Suggested: `The text
   encoder does not fit on the GPU beside the model (needs about 9.2 GB, limit 8.0 GB). Set it
   to Automatic or raise GPU memory.`

## Open questions

1. **GPU memory as a list or a number.** A select in whole GB (built here), or a number field
   in GB with one decimal and an empty field for Automatic? The field is finer; the select
   cannot hold a wrong value and shows its bounds.
2. **Any size on a Mac.** The engine lifts the Mac caps too, so the box is shown there (built
   here). The memory check before the start still refuses what clearly does not fit, but a Mac
   near its limit swaps heavily instead of failing. Offer it on Macs, or only on cards with
   their own memory until someone has measured it?

Not measured: GPU placement of the text encoder or the VAE on a Mac. The two Mac tips say
"can be faster" for that reason.
