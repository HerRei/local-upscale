# LocalSR tablet: UI design

Status (owner, 2026-11-19): the iOS v1 app is iPhone-only and iPads run the iPhone version; this iPad layout comes later as an update (see `tools/TABLET-MODELS.md`, "Release builds and licences"). Android tablets are unchanged.

Target: iPadOS 27, iPad Pro 13" M5 (1376 × 1032 pt landscape) first, A12Z iPad Pro as the low end; Android tablets through Skip Lite second. Sizes in pt (dp on Android). iOS-only API sits behind `#if !SKIP`. "Phone spec" means the phone UI as built (`DESIGN.md` and `Sources/LocalSR/UI`).

Scope for v1 (owner, 2026-11-15): photos only. **Video is out of scope for v1.** No transformer models, no Edit, no custom checkpoints. All models are bundled; downloading is a pending option (6.5).

| Model | Name in UI | Expected lane | Role |
|---|---|---|---|
| SPAN-28 | Fast | Neural Engine + GPU | preset |
| PULSE-48 | Best | Neural Engine + GPU | preset, default |
| RealPLKSR ×4 NomosWebPhoto | Finest | GPU | preset |
| SPAN ×4 NomosUni | same | Neural Engine | library only |
| Real-ESRGAN ×4plus | same | Neural Engine | illustration preset "General" |
| Real-ESRGAN ×4 Anime 6B | Real-ESRGAN ×4 Anime | Neural Engine | illustration preset "Anime" |
| FBCNN | JPEG fix | Neural Engine | fix stage with a strength |
| RealPLKSR Denoise 1× | Denoise | GPU | fix stage |

The device check decides the real lane per iPad; the UI always shows the checked result.

## 1. Concept

The tablet app is the desktop workstation with its three panes in place: Media on the left, the photo in the middle, Enhance on the right, and the status bar with the one rust Start button along the bottom. The panes stay flat and square-cut like the desktop; only what floats over the photo is Liquid Glass. Touch replaces the mouse: drop photos anywhere, pinch the photo, drag the divider, start from every tab. The phone's two parts, the brackets and the rust square on its white line, carry over unchanged, so the three apps read as one product.

The signature moment: you start a batch and two thumbnails in the Media list get their own small sweep line at the same time, one tagged "Neural Engine", one "GPU". In the viewer the brackets lock on the selected photo, its line crosses, and at the right edge it glides back to the middle and becomes the divider you drag.

Rust is spent on four things: the Start button, progress (status bar edge, sweep lines), the square, and the node of the plan stage that is running. Selection, focus, icons, links and lane meters are neutral.

## 2. Tokens

One `Theme` enum replaces the phone's `DesignTokens` and serves both tiers. Dark values are the desktop CSS tokens unchanged; light values mirror them in neutral greys (the icon's off-white stays in the icon). The app follows the system appearance; the viewer stage is dark in both.

| Token | Desktop CSS | Dark | Light | Use |
|---|---|---|---|---|
| `stage` | `--stage` | #0F0F0F | #0F0F0F | viewer canvas |
| `base` | `--base` | #161616 | #E4E4E4 | window |
| `panel` | `--panel` | #1B1B1B | #F4F4F4 | panes, sheets |
| `chrome` | `--chrome` | #222222 | #FAFAFA | status bar, tab-mode top bar |
| `raised` | `--raised` | #2A2A2A | #FFFFFF | buttons, active segment |
| `hover` | `--hover` | #313131 | #E9E9E9 | pointer hover, pressed |
| `recess` | `--recess` | #141414 | #E8E8E8 | segmented track, cards |
| `track` | new | white 10 % | black 10 % | unlit meter cells |
| `text` | `--text` | #ECECEC | #1A1A1A | text, icons, brackets, lit meter cells |
| `secondary` | `--secondary` | #A3A3A3 | #5C5C5C | second lines |
| `tertiary` | `--tertiary` | #8C8C8C | #6B6B6B | counts, idle nodes |
| `border` | `--border` | white 9 % | black 10 % | outlines, section rules |
| `divider` | pane borders | #09090B | #D0D0D0 | lines between panes |
| `accent` | `--accent` | #B3452A | #B3452A | see section 1 (white label 5.5:1) |
| `accentHi` | `--accent-hi` | #BF4C2F | #BF4C2F | Start pressed or hovered |
| `selectFill` | `--accent-fill` | white 6 % | black 5 % | selected card or row |
| `selectLine` | `--accent-line` | white 50 % | black 55 % | its 1 pt outline |
| `focus` | `--focus` | white 72 % | black 72 % | 2 pt keyboard focus ring |
| `danger` | `--danger` | #E5484D | #C62F35 | destructive buttons only |
| `warning` | `--warning` | #DE9935 | #9A5B00 | a slowed or paused lane |
| `ok` | status dot | #6DB07F | #3F8F58 | "Complete" dot |

Errors are monochrome as on the phone: `exclamationmark.triangle.fill` in `text`.

**Type.** IBM Plex Sans, the desktop's bundled variable face, for everything the app draws itself, on iPad and Android, through `Font.custom("IBM Plex Sans", size:relativeTo:)` so Dynamic Type scales. System-drawn surfaces (menus, alerts, pickers, share sheet) keep the system face. Semibold is the heaviest weight. Tabular figures. Always "×". Buttons in title case, the rest in sentence case.

| Style | Size, weight | Relative to | Use |
|---|---|---|---|
| `paneTitle` | 17 semibold | `.headline` | "Media", "Enhance", file name |
| `control` | 15 medium | `.subheadline` | buttons, segments, row and card titles |
| `body` | 15 regular | `.subheadline` | descriptions |
| `label` | 13 semibold | `.footnote` | section labels |
| `meta` | 13 regular | `.footnote` | second lines, chip names |
| `caption` | 12 regular | `.caption` | counts, status-bar lane labels |
| `emptyTitle` | 22 semibold | `.title2` | empty-state headline |

**Spacing.** 4-pt grid (4, 8, 12, 16, 24). Pane padding 16. Sections have 16 above and below and a 1 pt `border` rule between them. Controls are 36 high with a 44 hit area; list rows 64; status-bar buttons 44.

**Corners.** The desktop's crop-frame corners scaled to touch: 3 on controls, cards, thumbnails; 1 on meter cells and nodes; 0 on the photo. Glass elements are system capsules.

**Materials.** Flat token fills for the panes, headings, top bar, status bar, every in-pane control and the Model Library. Liquid Glass (`.glassEffect()` in one `GlassEffectContainer`) only for the viewer HUD: the zoom capsule, the "Original" / "Enhanced" chips and the "Waiting" chip. Menus, alerts and sheet toolbars get the system's glass by themselves. Reduce Transparency: the HUD becomes `chrome` at 92 %.

**Family.** Shared with the phone unchanged: `FrameMark`, `PhotoBrackets`, `SweepLine`, `Haptics`, the unit names. The phone keeps its rounded tiles and capsules. The tablet says "Enhanced" where the phone says "Upscaled", because Restore does not enlarge.

## 3. Layouts

### 3.1 Structure

`WorkspaceView` measures its width and picks a mode. It is a plain `HStack` of three panes with 1 pt dividers above the status bar: no `NavigationSplitView` and no system navigation bar in the workspace, so it is the same code on Android. Sheets keep their own `NavigationStack`.

| Width | Mode | Devices |
|---|---|---|
| ≥ 1100 | Three panes: Media 280, Viewer flexible, Enhance 360 | iPad 13" and 11" landscape, Android landscape |
| 700–1099 | Three tabs; Media grid; Enhance in two columns | iPad 13" and 11" portrait, half-screen windows |
| < 700 | Three tabs; one column; status bar in two rows | Split View thirds, small Stage Manager windows |

The fold is 1100, not the desktop's 920: touch-sized side panes need 640 and the viewer at least 460. Minimum window 375 × 560 (`UIWindowScene.sizeRestrictions`). A narrow window never swaps to the phone UI.

Selected photo, zoom, pan, divider position and the Enhance scroll offset live in the model, so crossing a breakpoint changes only the layout. In three-pane mode each pane starts with a 52-pt heading; the Media heading uses `.containerCornerOffset(.leading, sizeToFit: true)` to clear the window controls. In tab mode a 52-pt `chrome` top bar (Add button, pane switch, `ellipsis` menu) replaces the Media and Enhance headings. The pane switch is a segmented control, 36 high, segments 104 wide: "Media 5", "Viewer", "Enhance". A Start button pressed on the Media or Enhance tab switches to Viewer.

### 3.2 iPad 13" landscape, 1376 × 1032 (batch running; chain "JPEG fix, then Finest")

```
┌─ Media 280 ────────────┬─ Viewer 734 ──────────────────────────────────┬─ Enhance 360 ───────────────────┐
│ ◧  Media  5          + │ IMG_0412.jpg                                  │ Enhance      Finest · Photo   ⋯ │ 52
│                        │ 1512 × 2016 · 3.0 MP                          │                                 │
│ [ Queue 5 │ Results 12]│┌─────────────────────────────────────────────┐│ Settings Locked                 │
│                        ││ stage #0F0F0F       (− │ 100 % │ + │Fit│1:1)││ The queue uses this setup.      │
│ ▣ IMG_0410.jpg      ✓  ││    ┏━                               ━┓      ││ ─────────────────────────────── │
│   Done · 49 MP         ││      ███████████████│░░░░░░░░░░░░░░░        ││ Task                            │
│ ▣ IMG_0411.jpg         ││      ███████████████│░░░░░░░░░░░░░░░        ││ [   Upscale    │    Restore   ] │
│ ┃ Upscaling · GPU      ││      ███████████████■░░░░░░░░░░░░░░░        ││ ─────────────────────────────── │
│▐▣ IMG_0412.jpg         ││      ███████████████│░░░░░░░░░░░░░░░        ││ Quality        Save as Recipe…  │
│▐┃ Fixing JPEG ·        ││      ███████████████│░░░░░░░░░░░░░░░        ││ ┌────────┐ ┌────────┐ ┏━━━━━━━┓ │
│▐  Neural Engine        ││    ┗━                               ━┛      ││ │ Fast   │ │ Best   │ ┃Finest ┃ │
│ ▣ IMG_0413.jpg         ││                                             ││ │Quickest│ │ More   │ ┃ Most  ┃ │
│   Waiting              ││                                             ││ │        │ │ detail │ ┃detail ┃ │
│ ▣ IMG_0414.jpg         ││                                             ││ └────────┘ └────────┘ ┗━━━━━━━┛ │
│   Waiting              │└─────────────────────────────────────────────┘│ ┌ Old scans ──────────────────┐ │
│                        │                                               │ │ Upscale · Finest · JPEG fix │ │
│                        │                                               │ └─────────────────────────────┘ │
│                        │                                               │ ─────────────────────────────── │
│                        │                                               │ Your photo                      │
│                        │                                               │ [    Photo    │  Illustration ] │
│                        │                                               │ Fix first                       │
│                        │                                               │ [ Noise ] [✓ JPEG artifacts ]   │
│                        │                                               │ JPEG strength                   │
│                        │                                               │ [ Auto │ Light │ Medium│Strong] │
│                        │                                               │ ─────────────────────────────── │
│                        │                                               │ Model                  Change…  │
│                        │                                               │ ■ Fix JPEG        Neural Engine │
│                        │                                               │ │ FBCNN · strength Auto         │
│                        │                                               │ □ Upscale ×4                GPU │
│                        │                                               │   RealPLKSR ×4 NomosWebPhoto    │
│                        │                                               │ ─────────────────────────────── │
│                        │                                               │ Chips                    Both ⌄ │
│                        │                                               │ Neural Engine    ▮▮▮▮▮▮▮▮▯▯▯▯   │
│                        │                                               │ Fix JPEG · IMG_0412.jpg         │
│                        │                                               │ GPU              ▮▮▮▮▮▮▮▮▮▮▮▯   │
│                        │                                               │ Upscale ×4 · IMG_0411.jpg       │
│                        │                                               │ ─────────────────────────────── │
│                        │                                               │ Output       4× · HEIC · Ask  ⌄ │
│                        │                                               │ Queue    this photo ≈ 14 s …    │
├━━━━━━━━━━━━━━━━━━━━━━━━┷━━━━━━━━━━━━━━━━━━━┯───────────────────────────┴─────────────────────────────────┤ 2 rust
│ ● Upscaling 2 of 5 · 43 %         Neural Engine ▮▮▮▮▯▯  GPU ▮▮▮▮▮▯  [Add Selected to Queue][Cancel Queue]│ 64
│   About 50 s left · next IMG_0413.jpg                                                                    │
└──────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

`▐` selected row, `┃` a thumbnail's own sweep line, `■` the running stage's rust node.

### 3.3 iPad 13" portrait, 1032 × 1376 (tab mode, Enhance tab, idle)

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ +                  [ Media 5 │ Viewer │ Enhance ]                          ⋯ │ 52 chrome
├──────────────────────────────────────┬───────────────────────────────────────┤
│ Task                                 │ Model                        Change…  │
│ [    Upscale     │     Restore     ] │ □ Fix JPEG              Neural Engine │
│ Enlarges photos and artwork 4×.      │ │ FBCNN · strength Auto               │
│ ──────────────────────────────────── │ □ Upscale ×4                      GPU │
│ Quality             Save as Recipe…  │   RealPLKSR ×4 NomosWebPhoto          │
│ ┌─────────┐ ┌─────────┐ ┏━━━━━━━━━┓  │ ───────────────────────────────────── │
│ │ Fast    │ │ Best    │ ┃ Finest  ┃  │ Chips                          Both ⌄ │
│ └─────────┘ └─────────┘ ┗━━━━━━━━━┛  │ Neural Engine         ▯▯▯▯▯▯▯▯▯▯▯▯    │
│ ┌ Old scans ───────────────────────┐ │ Will run Fix JPEG                     │
│ └──────────────────────────────────┘ │ GPU                   ▯▯▯▯▯▯▯▯▯▯▯▯    │
│ ──────────────────────────────────── │ Will run Upscale ×4                   │
│ Your photo                           │ ───────────────────────────────────── │
│ [     Photo      │  Illustration   ] │ Output                                │
│ Fix first                            │ Size      4× · 6048 × 8064 · 49 MP  ⌄ │
│ [ Noise ] [✓ JPEG artifacts ]        │ Format                         HEIC ⌄ │
│ JPEG strength                        │ Full 4× for Large Photos        ( ○ ) │
│ [ Auto │ Light │ Medium │ Strong ]   │ Save Automatically              ( ○ ) │
│                                      │ ───────────────────────────────────── │
│                                      │ Queue                                 │
│                                      │ 5 photos · about 1 min with this setup│
├──────────────────────────────────────┴───────────────────────────────────────┤
│ Ready · 5 photos in the queue            [ Upscale Selected ] [ Start All 5 ]│ 64
└──────────────────────────────────────────────────────────────────────────────┘
```

Two columns of at most 520, 16 gutter, scrolling together. Left: Task, Quality, Your photo. Right: Model, Chips, Output (open in tab mode), Queue.

### 3.4 iPad 11" portrait, 834 × 1210 (Media tab while running; Viewer tab when done)

```
┌───────────────────────────────────────────────────┐  ┌───────────────────────────────────────────────────┐
│ +         [ Media 5 │ Viewer │ Enhance ]        ⋯ │  │ +         [ Media 5 │ Viewer │ Enhance ]        ⋯ │
│ [ Queue 5 │ Results 12 ]                          │  │ IMG_0412.jpg                          ⇧  Save   ⋯ │
│ ┌───────┐ ┌───────┐ ┌───────┐ ┌───────┐ ┌───────┐ │  │ 6048 × 8064 · 49 MP · Finest · GPU · 14.2 s       │
│ │     ✓ │ │   ┃   │ │ ┃     │ │       │ │       │ │  │┌─────────────────────────────────────────────────┐│
│ └───────┘ └───────┘ └───────┘ └───────┘ └───────┘ │  ││                          (− │100 %│ + │Fit│1:1) ││
│ IMG_0410  IMG_0411  IMG_0412  IMG_0413  IMG_0414  │  ││                       │                         ││
│ Done      GPU       Neural E. Waiting   Waiting   │  ││       original        ■        enhanced         ││
│                                                   │  ││                       │                         ││
│                                                   │  ││ (Original)                           (Enhanced) ││
│                                                   │  │└─────────────────────────────────────────────────┘│
├───────────────────────────────────────────────────┤  ├───────────────────────────────────────────────────┤
│ ● Upscaling 2 of 5 NE ▮▮▮▯ GPU ▮▮▮▮ [Cancel Queue]│  │ ● Complete · 5 photos       [ Save All to Photos ]│
└───────────────────────────────────────────────────┘  └───────────────────────────────────────────────────┘
```

Media grid: `GridItem(.adaptive(minimum: 148), spacing: 8)`, padding 16; square thumbnail, name (`control`, one line), state (`meta`). Enhance has the two columns of 3.3 at 393 each.

### 3.5 Split View and Stage Manager under 700 (507 and 375 wide)

```
┌───────────────────────────────┐   ┌─────────────────────────────┐
│ + [Media 5│Viewer│Enhance] ⋯  │   │ + [Media│Viewer│Enhance] ⋯  │  375: segments 96 wide,
│ Task                          │   │ ▣ IMG_0411.jpg              │  count hidden
│ [   Upscale   │   Restore   ] │   │ ┃ Upscaling · GPU           │
│ Quality       Save as Recipe… │   │ ▣ IMG_0412.jpg              │
│ ┌───────┐┌───────┐┏━━━━━━━┓   │   │ ┃ Fixing JPEG · Neural Eng… │
│ │ Fast  ││ Best  │┃Finest ┃   │   │ ▣ IMG_0413.jpg              │
│ └───────┘└───────┘┗━━━━━━━┛   │   │   Waiting                   │
│ …one column, same order…      │   │                             │
├───────────────────────────────┤   ├─────────────────────────────┤
│ ● Upscaling 2 of 5 · 43 %     │   │ ● Upscaling 2 of 5 · 43 %   │  status bar 108 high: copy
│   NE ▮▮▮▯▯▯   GPU ▮▮▮▮▮▯      │   │   NE ▮▮▮▯▯▯   GPU ▮▮▮▮▮▯    │  and lanes above, full-width
│ [       Cancel Queue        ] │   │ [      Cancel Queue       ] │  buttons below
└───────────────────────────────┘   └─────────────────────────────┘
```

Media shows rows below 600 and the grid from 600.

### 3.6 Android tablet, 1280 × 800 dp landscape

```
┌─ Media 280 ───────┬─ Viewer 638 ─────────────────────────┬─ Enhance 360 ────────────────┐
│ Media  5        + │ IMG_0412.jpg               ⇧ Save  ⋮ │ Enhance    Finest · Photo  ⋮ │ 52
│ [Queue 5│Results] │┌────────────────────────────────────┐│ Task  [ Upscale │ Restore ]  │
│ ▣ IMG_0411.jpg    ││              (− │100 %│ + │Fit│1:1)││ Quality  [Fast][Best][Finest]│
│ ┃ Upscaling · GPU ││   ┏━   ██████│░░░░░░   ━┓          ││ Model                        │
│ ▣ IMG_0412.jpg    ││        ██████■░░░░░░               ││ ■ Fix JPEG               GPU │
│ ┃ Fixing JPEG·GPU ││   ┗━   ██████│░░░░░░   ━┛          ││ □ Upscale ×4             GPU │
│ ▣ IMG_0413.jpg    │└────────────────────────────────────┘│ Chips                        │
│   Waiting         │                                      │ GPU          ▮▮▮▮▮▮▮▮▮▮▯▯    │
├───────────────────┴──────────────────────────────────────┴──────────────────────────────┤
│ ● Upscaling 2 of 5 · 43 %                     GPU ▮▮▮▮▮▯               [ Cancel Queue ] │ 64
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

### 3.7 Media pane

- **Heading:** `sidebar.leading` button (collapses the pane, `.smooth(duration: 0.25)`; while collapsed it sits at the leading edge of the viewer header), "Media" (`paneTitle`), count (`caption`, `tertiary`), Add button `plus`.
- **Add menu:** "Choose from Photos…" (`photo.on.rectangle`, `PhotosPicker`, images, multiple), "Choose Files…" (`doc`, `.fileImporter`), "Choose Folder…" (`folder`; every image in it joins the queue), "Paste" (`doc.on.clipboard`).
- **Segmented control** "Queue 5" / "Results 12", 12 inset. Queue: this session's inputs with their state. Results: finished photos kept across launches, newest first.
- **Row** (64 high): thumbnail 48; name (`control`, middle truncation); state (`meta`, `secondary`): "1512 × 2016 · 3.0 MP", "Preparing preview…", "Waiting", "Fixing JPEG · Neural Engine", "Denoising · GPU", "Upscaling · GPU", "Done · 49 MP" (trailing `checkmark`), "Couldn't upscale: <reason>" (two lines, leading triangle). A row being worked on carries a small `SweepLine` on its thumbnail (line 1.5, square 6) at that photo's progress.
- **Touch:** tap selects. Swipe left: "Remove" (queue) or "Delete" (result, with the phone's confirmation). Long press or secondary click: "Upscale Only This Photo", "Remove from Queue"; for results "Save to Photos", "Share…", "Delete…".
- **Pointer:** `hover` fill on the row; an `xmark` button (28, `tertiary`) appears at its trailing edge.
- **Drop:** the whole window accepts images and folders from Files and Photos (`.dropDestination`). During a drag the stage dims 35 % and `PhotoBrackets` hunt around a 320 × 240 area in its centre with "Drop to Add" (`emptyTitle`) inside; on drop they lock once. A result can be dragged out of Results or the viewer (`.draggable` with the output file).

### 3.8 Viewer

- **Header** (52, `panel`): file name (`paneTitle`); below it `meta`: "1512 × 2016 · 3.0 MP", and with a result "6048 × 8064 · 49 MP · JPEG fix + Finest · Neural Engine + GPU · 16.0 s". Trailing, with a result only: `ShareLink` (`square.and.arrow.up`), text button "Save" (`square.and.arrow.down`; then "Saved" with `checkmark`, disabled), `ellipsis` menu with "Enhance Again" and "Delete Result…" (`trash`).
- **Stage:** `stage` fill, photo aspect-fit with 24 inset.
- **Compare:** the phone's compare view as built (phone spec 5.3): `SweepLine` with the 36 square, pinch to 8×, pan, double tap, and the glass chips "Original" / "Enhanced" at the bottom corners, inset 16, which snap the line to one side. No result, no line and no chips.
- **Zoom HUD:** glass capsule, top-trailing, inset 12, 44 high: `minus`, zoom in percent of real pixels (`meta`, 64 wide), `plus`, "Fit", "1:1"; steps of 1.25×. It fades 2.5 s after the last touch, returns on any touch of the stage, and stays under a pointer.
- **While this photo is worked on:** the phone's stage at full size: 35 % dim right of the line, white `PhotoBrackets` (arm 28, thickness 6, 18 outside) lock, the line moves with `.linear(duration: 0.25)` across the whole plan (stages weighted by tile count). When it finishes and the photo is still selected, the phone's reveal runs (right edge to middle, `.smooth(duration: 0.9)`). Selection never changes by itself. A selected photo that is waiting shows a glass chip at the bottom centre: "Waiting · 3rd in the queue".
- **Trackpad:** pinch zooms, two-finger scroll pans, double click is double tap, secondary click opens the header menu. The divider square grows to 40 under the pointer.
- **Apple Pencil:** no special features; it acts as a finger.

### 3.9 Enhance pane

One scrolling column. Heading: "Enhance", a `meta` summary ("Finest · Photo", "Restore", a recipe's name, or "Custom"), and the `ellipsis` menu: "Model Library" (`square.stack.3d.up`), "Save All to Photos" (`square.and.arrow.down.on.square`), "Clear Finished", "Delete All Results…" (`trash`), "Settings" (`gearshape`, the phone's sheet), "About LocalSR" (`info.circle`). While the queue runs, controls are disabled under the notice "Settings Locked" / "The queue uses this setup."

1. **Task.** Segmented "Upscale" / "Restore". Caption: "Enlarges photos and artwork 4×." / "Removes noise and JPEG artifacts at the same size."
2. **Quality** (Upscale only). Cards in one row, equal width, 6 gap, 64 high, `recess`; selected: `selectFill` and `selectLine`. Title (`control`, semibold) and one `meta` line.
   - Photo: "Fast" / "Quickest", "Best" / "More detail", "Finest" / "Most detail, slower". After the first measured photo the second line is the measured time ("≈ 14 s per photo").
   - Illustration: "Anime" / "Line art, flat colour", "General" / "Painted or mixed artwork".
   - A preset whose model failed the device check is hidden.
   - **Recipes** are rows under the cards (44 high, `recess`, 6 gap): name (`control`) and a summary built from the setup (`meta`, "Upscale · Finest · JPEG fix"). Tap applies; the active one is outlined. Swipe or context menu: "Rename…", "Delete". The header link "Save as Recipe…" opens an alert with a text field ("Name this setup"; "Cancel", "Save"). It stores task, content, model, fixes, JPEG strength, size and format. In Restore the section is titled "Recipes" and has rows only.
3. **Your photo** (in Restore: "What to fix"). Segmented "Photo" / "Illustration" (Upscale only). Label "Fix first", then toggle chips (36 high, `recess`; on: `selectFill`, `selectLine`, leading `checkmark`): "Noise", "JPEG artifacts". With "JPEG artifacts" on, "JPEG strength" follows: segmented "Auto" / "Light" / "Medium" / "Strong". Restore needs at least one chip on.
4. **Model.** Header "Model" and the link "Change…" (opens the Model Library for the upscale stage; after a manual choice the link "Back to Best" appears). The plan lists the stages in run order (always Fix JPEG, Denoise, Upscale), each 52 high:
   - an 8 × 8 node joined to the next by a 1 pt `border` line: outlined `tertiary` when idle, filled `accent` while that stage runs for the selected photo, filled `text` when done;
   - line 1: stage name (`control`, semibold) and, right-aligned in `meta`, its chip: "Neural Engine", "GPU", "Both chips", "CPU", or "Checked on first use";
   - line 2 (`meta`): the model, e.g. "FBCNN · strength Auto", "RealPLKSR ×4 NomosWebPhoto".
5. **Chips.** Section 4.
6. **Output.** A disclosure row in three-pane mode, closed by default, with a summary value ("4× · HEIC · Ask"); open in tab mode. Rows: "Size" menu ("4×", "2×"; the value adds the result, "4× · 6048 × 8064 · 49 MP"), "Format" menu ("HEIC", "JPEG", "PNG"), toggle "Full 4× for Large Photos" (the phone's setting and footer), toggle "Save Automatically" with, when on, "Save to" ("Photos" / "Choose Folder…"). Off by default: results stay in LocalSR until saved, as on the phone.
7. **Queue** (when it has items). Idle: "5 photos · about 1 min with this setup" or "5 photos · time is measured during the first one". Running: "This photo ≈ 14 s", "Whole queue ≈ 50 s", "Next IMG_0413.jpg ≈ 9 s".

**Model Library:** sheet with `.presentationSizing(.page)`. A list grouped "Upscale photos" (5), "Illustration" (2), "Fix noise and JPEG" (2); Real-ESRGAN ×4plus is in two groups. Rows: name, one-line description, the desktop's four-dot Quality and Speed ratings, the chip it runs on here. The detail beside the list (below it under 700): Scale, Size, "Runs on", licence, author, source, and the button "Use for This Job" (`raised`). Models that fail every chip here are not listed. This sheet replaces the phone's "This Device" list.

### 3.10 Status bar

Flat `chrome`, 1 pt top `border`, 64 high plus the bottom safe area, visible in every mode and tab. Leading: an 8 pt dot (static), title (`control`, semibold), detail (`meta`). While working, a 2 pt `accent` line along its top edge shows whole-queue progress and the lane minis (section 4) sit before the buttons. Start is `accent` with a white label, min width 200; other buttons `raised` with `border`; "Cancel Queue" is `danger` text on `danger` 9 %. A disabled Start is `raised` with `secondary` text.

| State | Dot | Title / detail | Buttons |
|---|---|---|---|
| No photos | none | "Ready" / "Add photos to start." | "Upscale Selected" (disabled) |
| One waiting | none | "Ready" / "1 photo in the queue" | "Upscale Selected" (Start) |
| Several waiting | none | "Ready" / "5 photos in the queue" | "Upscale Selected", "Start All 5" (Start) |
| Device check | `accent` | "Getting Ready…" / "Checking which chip runs Best correctly." | "Cancel" |
| Running | `accent` | "Upscaling 2 of 5 · 43 %" / "About 50 s left · next IMG_0413.jpg" | "Add Selected to Queue", "Cancel Queue" |
| Done | `ok` | "Complete" / "5 photos in 1 min 12 s" | "Save All to Photos", then the Start buttons |
| Some failed | triangle | "3 of 5 Upscaled" / "2 photos failed. Select one to see why." | "Try Again", "Save All to Photos" |
| Paused by the system | `warning` | "Paused by iPadOS" / "3 of 5 done. Nothing is lost." | "Resume" (Start) |

In Restore the verbs are "Restore Selected" and "Restoring". Cancel acts at once; finished photos stay. Haptics as on the phone.

### 3.11 Keyboard and pointer

Registered with `.commands` so they also fill the iPad menu bar (File, View, Queue).

| Keys | Action |
|---|---|
| ⌘O / ⇧⌘O | Choose Files… / Choose from Photos… |
| ⇧⌘V | Paste photos into the queue (⌘V stays the system Paste for text fields; Ctrl+V on Android) |
| ⌘↩ / ⇧⌘↩ | Upscale Selected / Start All |
| ⌘. | Cancel Queue |
| ↑ ↓ / ⌫ | Previous or next photo / remove it from the queue (⌫ only while the Media pane was used last) |
| ⌘1 ⌘2 ⌘3 | Media, Viewer, Enhance in tab mode; in three-pane mode ⌘1 toggles the Media pane |
| ⌘0 / ⌥⌘0 / ⌘+ / ⌘− | Fit / 1:1 / zoom in / zoom out |
| ← → | Move the divider 5 %; Home and End jump to the ends (desktop) |
| M, held | Show the original (desktop) |
| ⌘S / ⇧⌘S | Save to Photos / Save All to Photos |
| ⇧⌘L / ⌘, | Model Library / Settings |

Every control is reachable with Tab and shows the `focus` ring. Under a pointer flat controls take the `hover` fill; nothing lifts or scales except the divider square.

## 4. The chip lanes

Three places, one question each. No milliseconds, frame rates or percentages.

**Which chip runs each step:** the plan rows (3.9, item 4). The name comes from the device check's verdict for that model on this iPad. "Both chips" means the model passed on both and a batch spreads photos over both lanes.

**How busy each lane is:** the Chips section, always two rows on iPad, each 56 high:

```
Neural Engine                  ▮▮▮▮▮▮▮▮▯▯▯▯      line 1: name (control, semibold), meter
Fix JPEG · IMG_0412.jpg                          line 2: meta, secondary, middle truncation
```

- **Meter:** 12 cells of 8 × 8, radius 1, 2 gap (118 wide), right-aligned; lit `text`, unlit `track`. Neutral, so rust keeps meaning progress.
- **Value:** the share of the last second the lane spent inside model calls, averaged over its runners; lit cells = share × 12, rounded. Sampled twice a second (once in Low Power Mode); changes animate with `.linear(duration: 0.2)`, not at all with Reduce Motion.

| State | Meter | Line 2 |
|---|---|---|
| Idle, used by the plan | unlit | "Will run Fix JPEG" / "Shares Upscale ×4 with the GPU" |
| Idle, not used | unlit, row at 40 % opacity | "Not needed for this setup" |
| Device check | one lit cell stepping left to right, 2 per second | "Checking…" |
| Working | live | "<Stage> · <file name>" |
| Waiting on the other lane | unlit | "Waiting for the GPU" |
| Slowed (thermal state serious or worse) | live, lit cells `warning` | "Slowed down, iPad is warm" |
| Paused in the background | unlit, name in `warning` | "Paused while LocalSR is in the background" |
| Switched off | hidden | "Off" |
| Failed the check for every model in the plan | hidden | "Not used: gave wrong results on this iPad" |

- **Header menu** "Both ⌄": "Both Chips" (default), "Neural Engine Only", "GPU Only"; disabled while the queue runs. A choice that leaves a stage without a chip disables Start, with the status detail "Finest needs the GPU."
- **After a batch** one `meta` line stays under the rows until the next start: "Last batch: 24 photos in 1 min 12 s. Neural Engine 14, GPU 10." For a chain: "Neural Engine fixed 24, GPU upscaled 24."
- VoiceOver: one element per row ("Neural Engine, busy, fixing JPEG on IMG_0412"), not a live region.

**Where each photo is now:** a working Media row names its chip ("Upscaling · GPU"), so two rows with moving lines and different chips are visible at once. The status bar repeats both lanes while the queue runs: label (`caption`, `secondary`; "NE" and "GPU" below 1100), then 6 cells of 6 × 6, same values. Tapping them scrolls Enhance to Chips, switching to that tab first in tab mode.

## 5. Background

Video (queue, scrubber, frame compare, audio) is out of scope for v1. Photo batches continue in the background:

- A Start button submits a `BGContinuedProcessingTaskRequest` with the title "Upscaling 5 Photos", the subtitle "2 of 5 · about 50 s left" and live progress. It asks for the GPU only when `BGTaskScheduler.supportedResources` offers it (M3 and newer) and the app has the `com.apple.developer.background-tasks.continued-processing.gpu` entitlement (`BatchBackgroundTask.isGPUEntitlementProvisioned`, false until Apple provisions it). The Chips texts follow what the batch was actually granted.
- On an iPad without background GPU (every iPad until the entitlement is provisioned) the Chips section shows, before the first start, one `meta` line under the GPU row: "In the background this iPad works on the Neural Engine only. Steps that need the GPU run on the slower CPU until you return." When the app leaves the foreground the GPU row goes to "Paused…" and a GPU step that moved to the CPU says "Upscaling on the CPU · IMG_0412.jpg".
- If the system ends the task, finished photos are kept and the status bar shows "Paused by iPadOS" with "Resume", also on the next launch. If the app was quit or crashed during a batch, the next launch shows "Batch Interrupted" / "3 of 5 done before LocalSR closed." with "Resume".

## 6. States

**6.1 First run.** No onboarding screens. The device check runs on first use of each model: the status bar shows "Getting Ready…", the brackets hunt on the selected photo, both meters step, and plan rows read "Checked on first use" until their verdict arrives. If no chip passes for any model: "Can't Upscale on This iPad" / "The device check found no chip that runs the models correctly.", Start disabled.

**6.2 Empty.**

- Viewer: `FrameMark(200)` with the white `plus` in its square (tap opens the Add menu; the brackets lock once on appear); 28 below, "Upscale Your Photos" (`emptyTitle`); then `body`, `secondary`, centred, max width 340: "Drop photos anywhere in this window, or choose them from Photos or Files. Everything stays on this iPad."
- Queue: "No photos yet" / "A folder becomes a batch.", buttons "Photos…" and "Files…".
- Results: "No results yet" / "Finished photos stay here until you save or delete them."
- Enhance shows its defaults (Upscale, Best, Photo), fully usable.

**6.3 Loading.** A row being read shows the `raised` placeholder and "Preparing preview…"; the viewer shows hunting brackets on an empty stage. While the picker hands photos over, the status detail is "Loading photos…" and Start is disabled.

**6.4 Errors.** No colour.

| Case | Where | Copy |
|---|---|---|
| A photo failed | its row; in the viewer a centred block with triangle, reason and "Try Again" (`arrow.clockwise`) | "Couldn't upscale this photo." + reason |
| Video or unreadable file dropped | status detail for 4 s | "Videos aren't supported yet." / "LocalSR can't open this file." |
| 4× does not fit in memory | `meta` note under the Size row | "4× needs more memory than this iPad has free. This photo is saved at 2×." |
| Storage full | alert | "Not Enough Storage" / "Free up 120 MB and try again." |
| Photos access denied | alert, as on the phone | "Allow LocalSR to add photos in Settings." |
| Result file missing | viewer | "Photo Unavailable" / "This enhanced photo is no longer on the device." |

**6.5 Model download: PENDING the owner's decision on network access.** v1 assumes every model is bundled, so none of this shows. A model outside the bundle (FBCNN is the likely one) gets these states:

- its fix chip or Quality card gets a `caption` line "144 MB download";
- its plan row shows "Download · 144 MB" in place of the chip name, with the footnote "Downloads once and stays on this iPad. Your photos never leave it.";
- Start reads "Download 144 MB, Then Upscale"; during the download the row shows a 2 pt `accent` track and "43 %", and Start becomes "Cancel Download";
- failure: "Download failed. Check the connection and try again." with the link "Try Again";
- the Model Library gains the group "On this iPad" and per model "Download 144 MB" or "Remove".

## 7. Android tablets

- Same panes, breakpoints (1100 and 700 dp), tokens and IBM Plex Sans (in `res/font`). The fold is our own width check, not Material's window size classes, whose "expanded" starts at 840 dp.
- Material 3 through SkipUI with a neutral tint. No glass: the HUD capsules are black at 60 %, as on the phone. Switches, menus, alerts and the picker are Material's own.
- Chips: unit names "NPU", "GPU", "CPU"; one row per lane that exists. With a single lane the header menu is hidden.
- Copy: "Photos" becomes "Gallery", "this iPad" becomes "this tablet", "Paused by iPadOS" becomes "Paused by Android".
- Back: in tab mode from Media or Enhance to Viewer, then out of the app; sheets close first.
- Native behind `#if SKIP`: drag and drop (`Modifier.dragAndDropTarget`), a long-press bottom sheet in place of context menus, hover fills. Shortcuts use Ctrl for ⌘.
- Long batches keep the phone's foreground-service notification with the status bar's title and progress.
- No device check yet: the engine takes the first accelerator that loads (NPU or GPU, then CPU) and has no PSNR, NaN or constant-output check and no GPU watchdog like the phone's `DeviceChecker.kt`. Until one exists the status bar says "Getting Ready…" / "Loading Best…", never "Checking…", and plan rows show the chip that ran.
- Picked, pasted and folder photos are copied into `cacheDir/LocalSRTabletInputs`, so Resume, Enhance Again and the compare view keep working after the content:// grant ends.

## 8. Acceptance checklist

Screenshots: iPad Pro 13" landscape and portrait, 11" portrait, 507- and 375-wide windows, light and dark; Android 1280 × 800 dp.

1. At 1376 wide: three flat panes (280, flexible, 360), 1 pt dividers, 52-pt headings, 64-pt status bar; no system navigation bar, no glass on a pane.
2. At 1032, 834, 507 and 375 wide: top bar with the pane switch, one pane at a time, status bar in every tab; Enhance in two columns from 700, Media as a grid from 600.
3. Rotating or resizing across 1100 keeps the selected photo, zoom, pan and divider position.
4. Rust appears only on Start, the status bar's progress edge, sweep lines, the square and the running stage's node.
5. App-drawn text is IBM Plex Sans, at most semibold, with tabular numbers and "×". Corners: 3 on controls, 0 on the photo.
6. The stage is #0F0F0F in light mode too; glass appears only on the viewer HUD.
7. Quality shows Fast, Best, Finest for Photo and Anime, General for Illustration; Best is selected on first launch.
8. With "JPEG artifacts" on and Finest selected, the plan lists "Fix JPEG" (Neural Engine) above "Upscale ×4" (GPU) joined by a line, and "JPEG strength" is visible.
9. A saved recipe appears under the Quality cards, restores the whole setup, and can be renamed and deleted.
10. Chips shows two rows with 12-cell meters and the second line of each state in section 4; no milliseconds, frame rates or percentages.
11. During a batch on a real iPad both meters move, two Media rows carry their own sweep line with different chip names, and the status bar shows both minis.
12. The selected photo shows brackets, dimming and the moving line while worked on; at the end the line glides from the right edge to the middle and can be dragged.
13. The status bar shows every state of 3.10 with its buttons; "Start All n" appears only with more than one waiting photo.
14. Dragging photos from Files and from Photos over the window shows hunting brackets and "Drop to Add"; a dropped video adds nothing and shows "Videos aren't supported yet."
15. Empty workspace: FrameMark with plus, "Upscale Your Photos", both empty Media texts; Enhance usable with defaults.
16. First start: "Getting Ready…", hunting brackets, stepping meters; plan rows change from "Checked on first use" to a chip name.
17. The Model Library lists nine rows in three groups with this device's chip names, and no download or remove buttons in the bundled build.
18. Every shortcut of 3.11 works and every control shows a focus ring; under a pointer rows take the hover fill and the zoom HUD stays.
19. Largest Dynamic Type: nothing clipped, Quality cards stack. Reduce Motion: no lock, hunt, sweep or meter animation. Reduce Transparency: HUD opaque. VoiceOver: every control labelled.
20. A12Z: failed models are hidden; after leaving the app during a batch the GPU row reads "Paused while LocalSR is in the background".
21. Android: same structure, opaque HUD, "NPU" and "Gallery" wording, one lane row when one lane exists, nothing behind the system bars.

## 9. Notes

**API names.** Checked against Apple's documentation on 2026-11-15: `glassEffect(_:in:)`, `GlassEffectContainer`, `containerCornerOffset(_:sizeToFit:)`, `BGContinuedProcessingTaskRequest(identifier:title:subtitle:)` with `requiredResources` and `strategy`, `BGTaskScheduler.supportedResources`, `presentationSizing(_:)`, `dropDestination(for:action:isTargeted:)`. `pointerStyle(_:)` is macOS only and is not used. Nothing here needs an API new in iPadOS 27. Not checked; verify before building on them:

- that `containerCornerOffset` leaves room for the iPad window controls in a layout without a navigation bar;
- the case names of the GPU resource and the submission strategy;
- that `.commands` fills the iPad menu bar;
- that `.monospacedDigit()` gives tabular figures with IBM Plex Sans (otherwise set the `tnum` feature);
- SkipUI support for `Font.custom(_:size:relativeTo:)`, `.keyboardShortcut`, `.draggable` and `.dropDestination`.

**Kept from the existing apps on purpose:** the "A · B · C" meta strings and the rust accent. The desktop's pulsing status dot is dropped.

**For the owner** (the design assumes the first option each time):

1. Appearance follows the system with a neutral light theme, or dark only like the desktop.
2. The fold is 1100, not 920, so iPad 13" portrait (1032 wide) is in tab mode.
3. Preset names "Fast", "Best", "Finest" (PULSE-48 stays "Best" as on the phone), and the illustration cards "Anime" and "General".
4. No Single / Batch switch: two buttons, "Upscale Selected" and "Start All n".
5. Results stay in LocalSR until saved; "Save Automatically" is off by default.
6. The lane menu "Both Chips / Neural Engine Only / GPU Only" is included.
7. Phone alignment, three small changes: IBM Plex Sans for the phone's own text, a neutral tint in place of rust toolbar icons, `brandFill` #B3452A in dark mode (now #C2502F).
8. FBCNN's size: the tablet plan lists 288 MB against a 50–60 MB bundle. If it stays outside the bundle, 6.5 and the network question apply.
