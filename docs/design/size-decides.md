# Size decides

Design for LocalSR 0.6.0, branch `w/060-flashvsr-ui`. It is a build spec. It is built **after**
`video-models-in-model-picker.md` ("the video spec") and assumes that one is in. Line numbers
refer to `8aa0dd68` (the code of `89f1cc6f`); the video spec moves them, so every reference
also names its function or its text. Only `desktop/src/` and `desktop/e2e/` change; the host,
the worker, the catalog and the settings file format stay as they are.

Two things change:

1. **No Restore task.** Whether the result gets bigger is a **Size** choice on the panel:
   `Same size`, `2×`, `3×`, `4×`. The task switch becomes `Upscale & restore` / `Edit`, and a
   video has no Task row. Output scale leaves Advanced.
2. **Quick and Best choose the fix models too.** This is already what the code does
   (`chooseFixModel`, lib/fixStages.ts:94). The spec states the rule, makes it visible in one
   tip sentence and closes the gaps in its tests. See "Quick and Best for the fixes".

## Decisions

Each is settled here with its reason; Hermes can still change them.

| Point | Decision | Reason |
| --- | --- | --- |
| Task name | `Upscale & restore` and `Edit` | It names what is inside with the two words the site, the changelog and old recipes use. `Enhance` would repeat the panel's title directly above it. |
| Words | **Fix** is one step (chips, rows, tip). **Restore** is the photo job at Same size (`Restore selected`). | One name per thing; no row and no field is called Restore any more. |
| Video start button | Always `Start selected video` | At Same size a video may run only Frame rate or audio cleanup, which is not restoring. |
| Label for 1× | `Same size` | Plain, and it is what the changelog already says ("to keep the size"). |
| Same size with nothing ticked | Nothing ticks itself. Photo: Start is off and one line says why. Video: De-flicker, Frame rate and Clean up audio run alone, as today. | A noise fix that appeared on a size click would stay on when the size goes back to 4×. |
| Field order | Fix, Size, Content | The order of the work, and nothing above Size moves when Content comes and goes. |
| Content at Same size | Hidden | It only chooses the upscaler; all six fix models are photo models shared by Photo and Illustration. |
| Size with SeedVR2 or FlashVSR | The Size row is hidden with "Your video" (video spec). **Output resolution** is the one size control there and lists `2×`, `3×`, `4×` before the pixel sizes. | These models work to a target resolution; one control, where the video spec already puts it. Same size needs a frame-by-frame model. |
| Quick, Best, Content | Never change Size, except down to the new model's largest scale | Today they reset the scale to the model's own (2× jumps to 4×). Hidden in Advanced that went unnoticed; on the panel it would look broken. |
| Hand-picked upscaler | Comes back when Size goes from Same size to 2×–4× again, within the session | Otherwise a look at Same size would silently replace it with Quick's. |
| Hand-picked fix model | Belongs to the quality it was picked under (Quick or Best) | Clicking Best asks for the better models; the button already reads `Use for Noise · Quick`. Same rule as the upscaler pins. |
| Stored settings | `task` and `video_task` keep their values; they follow Size | Recipes, presets, the queue and old settings need no migration. |
| Recipe subtitle | `same size` / `video · same size` instead of `restore` / `video restore` | It names the Size option the user sees. |

## Layout

Width as today (the 68 px stage column, `.seg-inline`, `.chip`, `.field-heading`).

### Photo, enlarging, two fixes

```
ENHANCE                                   Quick · Photo · 4×
TASK      [ Upscale & restore ] [ Edit ]
          Enlarges photos and artwork, fixes them, or both.
QUALITY                                    Save as recipe…
          [ Quick                ] [ Best                  ]
          [ Fast, clean result   ] [ Finest detail, slower ]
          [ 287 MB download      ] [ 730 MB download       ]
YOUR IMAGE
Fix (?)
[✓ Noise] [✓ Blur] [JPEG artifacts] [Colour]
Size (?)                                        6400 × 4268
[ Same size |  2×  |  3×  | (4×) ]
Content (?)
[ (Photo) | Illustration ]
MODEL
Fix blur     NAFNet GoPro Deblur                 ↓ Change…
             1 more model
Fix noise    RealPLKSR Denoise                   ↑ Change…
             3 more models
Upscale ×4   SPAN ×4 NomosUni                      Change…
             8 more models               Try others… (?)
ADVANCED                                    Format, tiles ⌄
                              [ Download 287 MB, then upscale ]
```

### Photo, Same size

```
ENHANCE                                   Quick · Same size
TASK      [ Upscale & restore ] [ Edit ]
QUALITY   [ Quick                ] [ Best                  ]
          [ Fast clean-up        ] [ Most careful, slower  ]
          [ 287 MB download      ] [ 702 MB download       ]
YOUR IMAGE
Fix (?)
[✓ Noise] [✓ Blur] [JPEG artifacts] [Colour]
Size (?)                                        1600 × 1067
[ (Same size) |  2×  |  3×  |  4×  ]
MODEL
Fix blur     NAFNet GoPro Deblur                 ↓ Change…
Fix noise    RealPLKSR Denoise                   ↑ Change…
             3 more models               Try others… (?)
ADVANCED                                    Format, tiles ⌄
                              [ Download 287 MB, then restore ]
```

No Content, no Upscale row. The last fix keeps its own name; it is still the job's model
(`selected_model_id`) and carries Try others….

### Photo, Same size, nothing ticked

```
[ Noise ] [ Blur ] [ JPEG artifacts ] [ Colour ]
[ (Same size) | 2× | 3× | 4× ]
MODEL
! Nothing to run. Pick what to fix, or choose a larger size.
                                      [ Restore selected ]  (off)
```

The Model section has no rows and no Change…; the Quality cards show no download line.

### Video, a frame-by-frame model

No Task row. The section is "Your video"; everything else as for a photo.

```
ENHANCE                                   Quick · Same size
AUDIO     [✓] Clean up audio (?)
QUALITY   [ Quick ] [ Best ]
YOUR VIDEO
Fix (?)
[ Noise ] [ Blur ] [ JPEG artifacts ] [ Colour ]
Size (?)                                        1920 × 1080
[ (Same size) |  2×  |  3×  |  4×  ]
MODEL
Interpolate 2×   RIFE 4.26                                ↑
                 24 → 48 fps · Labs
ADVANCED                                    Format, tiles ⌄
                                   [ Start selected video ]
```

### Video, SeedVR2 or FlashVSR chosen

As in the video spec, with three differences: no Task row, the note under the plan names
the new controls, and Output resolution carries the scale.

```
MODEL · CHOSEN BY YOU                            Back to Best
Upscale ×4   FlashVSR v1.1                       Change…
Chosen by you. Fix, Same size, De-flicker and Frame rate need a
frame-by-frame model: pick Quick or Best to go back.
(memory panel)
Output resolution   [ 4× · 7680 × 4320        v ]
                      2× · 3840 × 2160
                      3× · 5760 × 3240
                      4× · 7680 × 4320
                      256 px · shorter edge
                      … 512, 720, 1080, 1440, 2160
```

## Copy

Every string that is new or changes. Strings not listed stay.

| Where | String |
| --- | --- |
| Task switch (App.svelte:4413–4447) | `Upscale & restore`, `Edit` |
| Task caption (`taskCaption`, 1410) | photo: `Enlarges photos and artwork, fixes them, or both.` · Edit: unchanged · video: none (no Task row) |
| Header (4392) | enlarging: `Quick · Photo · 4×` (quality, content, `output_scale`) · Same size: `Quick · Same size` · video model: `Custom` (video spec) |
| Section head (4672–4690) | always `Your image` / `Your video`; `What to fix` is gone |
| Chips label and group `aria-label` (4715, 4726) | `Fix` |
| Fix tip, photo | `Each problem you pick gets its own fix at the original size, in the order listed under Model. Quick picks a fast model for each problem, Best the most careful one. ↑ and ↓ change the order; a fix's Change… picks its model, which LocalSR remembers for that problem. Colour adds colour to black-and-white photos.` |
| Fix tip, video | the same, ending `Colour adds colour to black-and-white videos and keeps it steady from frame to frame.` |
| Size label | `Size`, with the result beside it: `6400 × 4268` (source × scale; the source size at Same size; nothing without a selected file) |
| Size options | `Same size`, `2×`, `3×`, `4×` |
| Size option the model cannot reach, `title` | `This model enlarges up to 2×.` (its `native_scale`) |
| Size tip, photo | `Same size keeps the pixel size and runs only the fixes you picked. 2× to 4× enlarges the result after the fixes. The model always works at its own N× scale; a smaller choice resizes that result down afterwards, so it saves no time.` (`N` = the upscaler's `native_scale`, 4 when unknown) |
| Size tip, video | `Same size keeps the resolution and runs only the fixes you picked. With nothing picked, only De-flicker, Frame rate and Clean up audio run. 2× to 4× enlarges the result after the fixes. A 2× or 3× choice on a N× model resizes down afterwards.` |
| Plan row, own file at Same size (plan.ts:296) | `Fix` (was `Restore`) |
| Plan row, last fix at Same size (plan.ts:305–308, 322) | its fix name: `Fix noise`, `Fix blur`, `Fix JPEG`, `Colour` (was `Restore`) |
| Nothing to run, photo | `Nothing to run. Pick what to fix, or choose a larger size.` |
| Nothing to run, video (843) | unchanged |
| Start, photo | `Upscale selected` · Same size: `Restore selected` · `Download 287 MB, then upscale` / `…, then restore` (unchanged) |
| Start, video | `Start selected video` · `Download 28 MB, then start video` at every size (`Restore selected video` and `restore video` are gone) |
| Start, all (`allPhrase`, 1478) | `Upscale all 52`, `Restore all 52`, `Start all 5 videos` |
| Video model note (5119, video spec) | `Chosen by you. Fix, Same size, De-flicker and Frame rate need a frame-by-frame model: pick Quick or Best to go back.` |
| Output resolution options (5157) | `2× · 3840 × 2160`, `3× · …`, `4× · …` (only `2×` etc. without a selected file), then `256 px · shorter edge` … as today. `Match N× scale` is gone. |
| Advanced summary (AdvancedSettings:172–176) | `Format, tiles` (Edit: `Size, steps, seed`, unchanged) |
| Output scale in Advanced (245–262) | removed, with its three tips |
| Recipe subtitle (4625–4629) | `upscale · 4× · FP16` (unchanged) · `same size · FP16` · `video · same size · FP16` |
| Preset modal (3140) | `Choose a task before applying a preset.` |
| Library context (ModelLibrary:495–500) | `Choosing for Upscale · …`, `Choosing for Same size · Noise`, `Choosing for Video · …`, `Choosing for Video · Same size · Noise` |
| Status line (`outputDimensions`, 1238) | `1600 × 1067 · original size` (unchanged) |

## Behaviour

`restoring` (`isRestoreTask`, plan.ts:106) keeps its meaning and now reads "Same size".
Size shows 1 when `restoring`, else `output_scale`.

### Where Size shows

| State | Size row | Options enabled |
| --- | --- | --- |
| Photo, `upscale` or `denoise` | shown | Same size; 2× up to the upscaler's `native_scale` |
| Video, frame-by-frame model | shown | the same |
| Video, SeedVR2 or FlashVSR | hidden with "Your video"; Output resolution instead | 2×, 3×, 4× and the pixel sizes |
| Edit | hidden | |

The upscaler whose `native_scale` counts: the chosen one while enlarging (4 for an own
file); at Same size the one a size click would choose (the remembered hand pick, else
`choosePresetModel` for Upscale with the active quality).

### What each choice writes

| Action | Settings |
| --- | --- |
| Size → Same size | `setTask('denoise')`, for a video `setTask('video', 'denoise')`. As today: `task` / `video_task`, `output_scale: 1`, `selected_video_model_id: 'frame_by_frame'`, `fix_stages` kept, `selected_model_id` = the last fix's model. New: with no fix it is `''` (photos too); no Noise is added (`needsDefaultNoise`, 2517–2525, goes); a job set up by hand (`quality: 'custom'`) falls to `'best'`, not `'quick'`, because its fixes already use Best's models (`fixQuality`). |
| Size → N× from Same size | `setTask('upscale')` / `setTask('video', 'upscale')` with the scale: `output_scale: min(N, native_scale)`; the upscaler is the remembered hand pick (then `quality: 'custom'`), else the preset's; `fix_stages` kept. |
| Size → N× from M× | `output_scale: N`, nothing else |
| Fix chip | `toggleFix` (2156) as today. At Same size the last fix's model becomes `selected_model_id`; removing the last one sets `''`. |
| Quick, Best | `applyPreset` (3138): upscaler and every fix model chosen again for that quality, pins first. `keepScale: true`. Forgets the remembered hand-picked upscaler. |
| Content | `setContent` (2151) with `keepScale: true` |
| Change… on a fix row → `Use for Noise · Quick` | `rememberFixModel` (2267), unchanged |
| `Back to the default` | `forgetFixModel` (2289), unchanged |
| Use for this job, enlarging | `selectPrimaryModel` (3120), unchanged |
| Use for this job, Same size (reachable only from an own-file row) | the catalog model becomes its problem's chip: `fix_stages: [{ fix: fixesOf(model)[0], model_id }]`, `fixes`, `selected_model_id`, `halo`, `output_scale: 1`, `quality: 'custom'` |
| Own file, Same size | `selectCustomModel` (3078), unchanged: fixes cleared, row `Fix` |
| Task → Upscale & restore from Edit | `setTask(settings.output_scale === 1 ? 'denoise' : 'upscale')`. Edit never touches `output_scale`, so this survives a restart. |
| Output resolution → `3×` | `output_scale: 3`, `video_target_resolution: 0` |
| Output resolution → `720 px` | `video_target_resolution: 720` (unchanged) |

Every `setTask` that ends in Upscale keeps the size: target scale = the given one, else
`output_scale` when it is 2 or more, else 4; then `resolveRecipe(…, { keepScale: true })`,
which clamps to the model. (Today a round trip through Edit resets 2× to 4×.)

### Nothing to run

`noVideoRestoreModel` (App.svelte:742, plan.ts:249) becomes `noRestoreModel` and loses its
video condition: Same size, no fix, not an own file. Then: no primary row in the plan, no
header Change… (4769), `model_id: ''` in `makeStartInput` (3212), no stages in a saved
recipe (3532). `hasNothingToRun` (842) is `noRestoreModel && !hasVideoOnlyWork`, so a photo
in that state cannot start or download, and a video starts when De-flicker, Frame rate or
Clean up audio is on. The line shows where it does today (5094).

### Presets, recipes, old settings, queue

- **Preset slots** (`presetSlot`, plan.ts:96) and **fix pins** (`fixSlot`, fixStages.ts:20)
  are unchanged.
- **Recipes with `task: 'denoise'`** (or `video_task: 'denoise'`, or Upscale at 1×) apply as
  today (`isRestoreRecipe`, `applyRecipe` 3583): Size shows Same size. New recipes save the
  same fields (`addRecipe` 3488). A recipe saved in photo Restore without a chip names a
  model but no fix: `recipeFixStages` (recipes.ts:16) gives that stage the model's first
  fix, so it applies with its chip on instead of with nothing to run. `recipeMatches` (61)
  reads the same list, so the applied recipe still lights up.
- **Old settings**: `task: 'denoise'` loads as Same size. `normalizeLoadedSettings` (1893)
  gains one case, the photo twin of its video case (1977–2006): `task: 'denoise'`, no
  `fix_stages`, a catalog model selected → that model becomes its problem's chip
  (`fix_stages: [{ fix, model_id }]`, `fixes`); a selection that fits no fix and is not an
  own file becomes `''`. Until now such a job ran a noise model under a row called Restore
  with no chip on.
- **Photos and videos** keep their own size, as they keep their own task today
  (`lastImageTask`, `video_task`).
- **Queue**: a job carries the size it was started with (`makeStartInput`). The panel is
  locked during a run, Size included. `Add selected to queue` uses the Size shown.

## Quick and Best for the fixes

**The rule, as built.** For each ticked problem the model is, in this order:

1. the user's own pick for that problem and quality, `preset_pins['fix/<problem>/<quick|best>']`,
   when it still names a 1× model that fixes the problem and whose rights allow it
   (`rememberedFixModel`, fixStages.ts:82);
2. otherwise the first of the eligible models (`fitsFix` and `presetMayPick`) in the order
   of `rankForQuality` (plan.ts:76): **Quick** by `speed_tier`, then `speed_factor` (higher
   first), `memory_factor`, `size_bytes` (lower first); **Best** by `quality_tier`, then
   `size_bytes`, `speed_tier` (higher first).

Photo and video, Photo and Illustration, enlarging and Same size use the same rule and the
same pins. With a hand-picked upscaler (Custom) the fixes use Best's models.

**Today's catalog** (sizes as the cards show them):

| Problem | Quick | Best | Models |
| --- | --- | --- | --- |
| Noise | RealPLKSR Denoise, 28 MB | NAFNet SIDD Width64, 443 MB | 3 (SCUNet Color is never a default) |
| Blur | NAFNet GoPro Deblur, 259 MB | the same | 1 |
| JPEG artifacts | FBCNN Color, 274 MB | the same | 1 |
| Colour | DDColor, 870 MB | the same | 1 |

Only Noise differs between Quick and Best. For the other three Quick can only become
cheaper when the catalog gets a second model; that is a catalog decision, not part of this
change.

**Cards.** `presetDownload` (plan.ts:533) already adds each card's own fix models, each
counted once and only when not installed. Noise + Blur at 4×, with Quick's upscaler
installed: Quick `287 MB download` (259 + 28), Best `730 MB download` (Best's upscaler
28 + 259 + 443). Noise alone at Same size:
`28 MB` against `443 MB`. One change: at Same size no hidden default model is counted any
more (line 549 becomes `restoring || skipsUpscaleModel(ui) ? undefined : …`), so with
nothing ticked the cards show no download line.

**A pick by hand.** Change… on a fix row opens the library on that problem;
`Use for Noise · Quick` stores the pin and the row says `Your pick`. It stays through chip
clicks, Size, Content, Quick → Best → Quick, photo and video, and restarts. It is not used
while the other quality is active. It resets with `Back to the default` in the library, and
is passed over (not deleted) while its model is gone from the catalog or no longer fits.

**To build:** the tip sentence (Copy) and the tests below. No logic changes.

## Files to change

`desktop/src/App.svelte`
- 575: add `let upscalerBeforeSameSize = ''` (a model id or `'__custom__'`; not saved).
- New `setSize(scale: 1 | 2 | 3 | 4)`:
  - 1 and not `restoring`: note `activeQuality === 'custom' ? settings.selected_model_id : ''`,
    call `setTask` for Same size, then store the note in `upscalerBeforeSameSize`.
  - N and `restoring`: `setTask` for Upscale with `{ scale: N, modelId: upscalerBeforeSameSize }`.
  - N otherwise: `updateSettings({ output_scale: N })`.
- `setTask` (2504): third parameter `enlarge?: { scale: number; modelId: string }`. Clear
  `upscalerBeforeSameSize` first. `kept` (2529–2532) also accepts `enlarge.modelId`;
  `'__custom__'` with a `custom_model_path` counts as kept (`selected_model_id: '__custom__'`).
  When `enlarge.modelId` is kept, add `quality: 'custom'` and, for a catalog model, its
  `recommended_halo` (the last fix's was in use at Same size). Target scale and `keepScale` as
  in Behaviour. Remove `needsDefaultNoise`. The fallback quality (2558) is
  `fixQuality(activeQuality)` when the call changes `restoring`, else `'quick'` as today.
  `restoring` and `activeQuality` are reactive and stale after `updateSettings` in the same
  tick: read them before the first patch, as `setTask` does now.
- `resolveRecipe` (2037), restoring branch (2104–2127): without a last fix the model is kept
  only when it is the own file (`keepPrimary && selected_model_id === '__custom__'`);
  otherwise `selected_model_id: ''`. The `choosePresetModel` fallback there goes.
- `applyPreset` (3144), `setContent` (2153): `keepScale: true`; `applyPreset` and
  `applyRecipe` clear `upscalerBeforeSameSize`. 3140: the modal string.
- `selectPrimaryModel` (3120): the Same size case from the table.
- `normalizeLoadedSettings` (1893): the photo case.
- 742, 842, 848, 898, 3212, 3532, 4769: `noRestoreModel`. Add the photo sentence beside
  `nothingToRunReason` (843) and pick by task (5095, 1452, 1470, 1475, 1498).
- 741: delete `skipUpscale` and its uses (773, 1237, 1359, 5131); it is always false.
- 1314–1322 `startVerb`: video is always `'start video'`. 1478: drop the `'restore video'`
  case. 5410–5413: `Start selected video`.
- 1410 `taskCaption`, 4392 header, 4625–4629 recipe subtitle: Copy.
- 4411–4448: the Task section renders only when the task is not `'video'` and the selected
  file is not a video. Two buttons; the first is pressed for `upscale` and `denoise`.
- 4668–4750: one section. Order: Fix label and tip, chips, Size label with tip and result,
  Size switch (`role="radiogroup"`, buttons `role="radio"` as Content has, 4691–4712),
  then Content label, tip and switch inside `{#if !restoring}`.
- 5150–5163: the Output resolution options. Option values `x2`, `x3`, `x4` and the pixel
  numbers; the selected value is the pixel size when `video_target_resolution` is set, else
  `x` + `output_scale`.
- 5119: the video model note.

`desktop/src/lib/plan.ts`
- 249, 278, 328: `noRestoreModel` without the video condition.
- 296: `'Fix'`. 305–308: `chip ? FIX_ROW_LABELS[chip] : colorize ? FIX_ROW_LABELS.colour : 'Fix'`.
  322: `FIX_ROW_LABELS[chip]`.
- 549: the primary of `presetDownload`.

`desktop/src/lib/recipes.ts`
- 33–43: at the primary stage of a restoring recipe, fall back to the model's first fix.

`desktop/src/AdvancedSettings.svelte`
- 172–176: summary. 245–262: remove Output scale. Nothing else in Advanced moves.

`desktop/src/ModelLibrary.svelte`
- 495–500: the context text. Comments at 67 and 112 name removed things.

`desktop/src/styles.css`
- 1388: drop `.seg-inline.task-switch` (two columns are the default). Add
  `.seg-inline.size-switch { grid-template-columns: 1.5fr 1fr 1fr 1fr; }` and a right-aligned
  value for `.field-heading` (`margin-left: auto`, `color: var(--secondary)`, 12 px,
  `font-variant-numeric: tabular-nums`).

## Tests

`./local-ci.sh frontend` and the e2e suite must pass at the end.

### vitest

`lib/plan.test.ts`

| Case | Expected |
| --- | --- |
| 65, 73 (fix model by quality, pins) | unchanged, still pass |
| 100 "every fix chip has a model" | add `'colour'` to the loop |
| new: defaults in the bundled catalog | noise: quick `denoise_realplksr_1x`, best `nafnet_sidd_width64`; blur `nafnet_gopro_deblur`, jpeg `fbcnn_color`, colour `ddcolor_modelscope` for both |
| new: `presetDownload`, only `span_photo_x4` installed, Upscale 4×, Noise + Blur | quick `287 MB download`, best `730 MB download` (nothing installed: quick `292 MB download`) |
| new: same at Same size | quick `287 MB download`, best `702 MB download` |
| new: Same size, no fix | `''` for both |
| new: pin `fix/noise/quick` = `scunet_color_real_psnr`, Noise at Same size | quick `69 MB download`, best `443 MB download` |
| new: `resolvePlan`, `task: 'denoise'`, fixes blur + noise | rows `Fix blur` (role fix) and `Fix noise` (role primary, kind restore); no row labelled `Restore` |
| new: `task: 'denoise'`, own file, no fix | one primary row labelled `Fix` |
| new: `task: 'denoise'`, no fix, a catalog model selected | no primary row |
| new: Colour as last fix at Same size | row `Colour` |

`lib/recipes.test.ts` (new file, or beside the recipe tests in `fixStages.test.ts`)

| Case | Expected |
| --- | --- |
| recipe `task: 'denoise'`, `model_id: 'denoise_realplksr_1x'`, one restore stage, `fixes: []` | `recipeFixStages` → `[{ fix: 'noise', model_id: 'denoise_realplksr_1x' }]` |
| upscale recipe with fixes | unchanged |

`App.test.ts` (rewrite every use of the Restore button, `Restore first`, `What to fix`,
`Output scale` and `Restore selected video`: lines 2079–2090, 2768, 4038, 4570–4616,
6915–7092, 7165, 7394–7473)

| Case | Expected |
| --- | --- |
| Photo selected | Task group has exactly `Upscale & restore` and `Edit`; no `Restore` button; groups `Fix` and `Size`; no `Output scale` in Advanced; Advanced summary `Format, tiles` |
| Video selected | no Task group; `Fix` and `Size` under `Your video` |
| Size → Same size with Noise ticked (photo) | `saveSettings` gets `task: 'denoise'`, `output_scale: 1`, `selected_model_id: 'denoise_realplksr_1x'`; no Content; no `Upscale` row; last row `Fix noise`; header `Quick · Same size`; button `Restore selected` |
| Size → Same size, nothing ticked (photo) | `fix_stages: []`, `selected_model_id: ''`; no Noise chip pressed; the photo line shows; Start disabled; no download line on either card; no Change… in the Model head |
| then tick Blur | Start enabled, row `Fix blur`, `selected_model_id: 'nafnet_gopro_deblur'` |
| then Size → 2× | `task: 'upscale'`, `output_scale: 2`, Blur still ticked, row `Upscale ×2`, Content back, header `Quick · Photo · 2×` |
| At 2×: Best, then Illustration | `output_scale` stays 2 both times |
| At 4×, `realesrgan_x2plus` taken by hand | `output_scale: 2`; `3×` and `4×` disabled with the title |
| Hand-picked upscaler, Noise ticked, Size → Same size | `quality: 'best'`; the noise model stays `nafnet_sidd_width64` |
| then Size → 4× | the hand-picked upscaler again, `quality: 'custom'`, head `Model · chosen by you` |
| the same, but Quick clicked at Same size | 4× gives Quick's upscaler |
| Best with Noise ticked, then Quick | noise row `NAFNet SIDD Width64`, then `RealPLKSR Denoise`; at Same size with neither installed the cards read Quick `28 MB download`, Best `443 MB download` |
| `Use for Noise · Quick` on SCUNet Color, then Best, then Quick | SCUNet with `Your pick`, NAFNet SIDD, SCUNet again; `Back to the default` → RealPLKSR, pin removed |
| Video, Size → Same size, nothing ticked, Frame rate 2× | no Noise chip pressed (was: Noise on); Start enabled; `startJobs` gets `video_task: 'denoise'`, `model_id: ''`, `fix_stages: []`; button `Start selected video` |
| the same with every video option off | the video line (unchanged text); Start disabled |
| Edit, then Upscale & restore (was 2×; was Same size) | back at 2×; back at Same size |
| Recipe `task: 'denoise'` applied | Same size pressed; subtitle `same size · FP16` |
| Loaded settings `task: 'denoise'`, no `fix_stages`, `selected_model_id: 'denoise_realplksr_1x'` | Noise ticked, row `Fix noise`, Start enabled |
| Loaded settings `task: 'upscale'`, `output_scale: 1` (7053) | still migrates to Same size |
| SeedVR2 chosen | no Size row; Output resolution lists `2×`…`4×` before `256 px`; choosing `3×` saves `output_scale: 3`, `video_target_resolution: 0`; choosing `512 px` saves `video_target_resolution: 512`; the note names Fix and Same size |
| Batch of photos at Same size | `Restore all N`; videos: `Start all N videos` |

### e2e

`desktop/e2e/size.e2e.ts` (new)

| Case | Expected |
| --- | --- |
| Photo: tick Noise and Blur, click Same size | Upscale row gone, Content gone, result `1600 × 1067`, button `Restore selected` (or ending `then restore`); `start_jobs` gets `task: 'denoise'`, two fix stages |
| click 4× | Upscale row and Content back, result `6400 × 4268`, both chips still on |
| Same size, untick both | photo line, Start disabled |
| Best, then Quick, Noise on at Same size | card lines `443 MB download` / `28 MB download`; noise row changes model |
| Keyboard | Size is one radiogroup; arrow keys move, as in Content |

`video-restore.e2e.ts` (rewrite: Same size through `Size`; line 49 no longer gets Noise;
`Start selected video`; scale through `Size`, not `#scale`), `plan.e2e.ts:7` (group `Fix`),
`layout.e2e.ts:510` (`#scale` is gone; measure the Size switch or drop the case),
`video-engines.e2e.ts` (Output resolution options).

Screenshot baselines: every baseline that shows the Enhance panel changes (Task row, field
order, Advanced summary). Regenerate them and look at each.

## Outside this change

- Host message `commands.rs` 3799 names "Restore first" ("Nothing to run at 1×. Turn on
  Restore first, …"). The UI does not reach it; the wording should follow.
- The site and the guide describe Restore as a task; they need the new wording at release.
- Blur, JPEG artifacts and Colour have one model each, so Quick and Best are the same
  there. Colour is 870 MB even for Quick.
