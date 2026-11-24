# View options: high contrast and viewer background

Design spec for two additions to Settings → Appearance in 0.6.0 (`w/060-view-options`).
Line numbers refer to commit `a25312b8`.

## Amendment: a switch, not a select (Hermes, 2026-11-23)

Hermes: "just have it be a switch where you can also do colour blind and dark or bright
mode, and i think you can go with the standart taste choices". This overrides the Contrast
select described below:

- High contrast is a **switch row exactly like "Colour-blind friendly colours"**, placed
  directly under it (same `.setting-row`, `input.switch role="switch"`, label + description).
- Label: `High contrast`. Description (`id="settings-contrast-desc"`): `Stronger text,
  borders and focus outlines. Also turns on when your computer asks for more contrast.`
  No `?` tip on this row.
- The stored field stays `contrast` with the same defaults and fallbacks, but the switch
  only writes two values: on → `"high"`, off → `"system"` (off still follows the system, as
  Match System did). `"standard"` remains a valid stored value (it loads and behaves as
  specified) but the UI never writes it. The switch shows on only when the stored value is
  `"high"`.
- Everything else in this spec stands, including the taste calls: the light terracotta
  Start button in dark high contrast, the zoom-bar background swatch, and Match System
  following the system after the update.

## Decisions

1. **Contrast** is a third, independent axis next to Theme (dark/light) and the
   colour-blind palette. It is chosen in the Theme block, directly under the tiles:
   `Contrast: Match System | Standard | High`. Default `Match System`.
   - Not extra tiles: high contrast exists in dark and in light, so tiles would need five
     entries and Match System could no longer follow light/dark and contrast separately.
   - The three tiles preview the contrast that is active, so the picker shows what you get.
   - First-run setup includes a high contrast switch with live preview; off follows the computer.
   - Not in the native menu: appearance items were moved out of it into Settings
     (`native_menu.rs:369-372` asserts they stay out).
2. **High contrast is a token theme.** `styles.css` has no colours outside its token
   blocks, so high contrast is four more token blocks plus a short list of rules for states
   that are only a faint fill today. Text reaches 7:1, control boundaries 3:1.
3. **Viewer background** is one app-wide preference: Checkerboard (default, today's look),
   Black, Grey, White. It is set in Settings → Appearance and by one swatch button beside
   the zoom controls, because the reason to change it is to look at the picture while it
   changes, and Settings covers the picture. It applies to the Preview, to Compare (slider
   and boxes) and to video. Themes and high contrast do not change the three fixed colours.
4. **Grey is sRGB 118/118/118 (`#767676`).** 18 % reflectance is linear 0.18; the sRGB
   encoding is `1.055 × 0.18^(1/2.4) − 0.055 = 0.4613`, × 255 = 117.6. Relative luminance
   of `#767676` is 0.181.

## What the WebViews report

| | `prefers-contrast: more` | `forced-colors: active` | Native chrome |
| --- | --- | --- | --- |
| macOS, WKWebView | Matches with System Settings → Accessibility → Display → Increase Contrast. `less` and `custom` never match. | Never. WebKit parses the query as a placeholder that is always `none`. | Traffic lights, menus and file dialogs are drawn by AppKit and follow the setting on their own. |
| Windows, WebView2 | Matches under a contrast theme whose text/background ratio is 7:1 or more; otherwise `less` (2.5:1 or less) or `custom`. | Matches under any contrast theme. Chromium then replaces text, background, border and outline colours with the theme's, removes `box-shadow` and non-`url()` `background-image`, and draws a backplate behind text. | The title bar and menus take the contrast theme's colours from Windows. |
| Linux, WebKitGTK (GTK 3 build, `webkit2gtk-4.1`) | Matches only when `gtk-theme-name` is exactly `HighContrast` or `HighContrastInverse`. | Never (same placeholder). | The GTK decoration follows the GTK theme. |

Consequences:

- "The system asks for high contrast" is `(prefers-contrast: more)` **or**
  `(forced-colors: active)`. The second covers Windows contrast themes that Chromium
  classes as `custom` or `less`.
- On Windows the contrast theme repaints the app whatever LocalSR's own setting says. With
  Contrast set to Standard the app still shows the theme's colours; only our tokens differ.
  The forced-colours rules below therefore apply independently of `data-contrast`.
- On Linux the system signal is uneven, so the manual High choice is the reliable path.
- Tauri has no contrast API and none is needed: `getCurrentWindow().setTheme()` stays as
  it is (`App.svelte:1499-1505`).

Sources:

- WebKit, `prefers-contrast: more` (fixed 2020-12-14; `low`/`forced` are no-ops on Apple
  platforms): <https://bugs.webkit.org/show_bug.cgi?id=215507>
- Increase Contrast triggers it in Safari:
  <https://tempertemper.net/blog/using-the-increased-contrast-mode-css-media-query>
- WebKit, `forced-colors` placeholder (`active` is false, `none` is true; "iOS/macOS has no
  such thing"; 2022-08-10): <https://bugs.webkit.org/show_bug.cgi?id=225281>
- WebKitGTK, GTK 3 only, by theme name (commit `7128c7a8144c`):
  <https://bugs.webkit.org/show_bug.cgi?id=256436>; the general Linux bug is still open:
  <https://bugs.webkit.org/show_bug.cgi?id=254135>
- Chromium, `prefers-contrast` values and thresholds on Windows, Mac and Linux:
  <https://groups.google.com/a/chromium.org/g/blink-dev/c/Hy6ngo_cSgU>
- Edge, what forced colours override, backplates, `forced-color-adjust`:
  <https://blogs.windows.com/msedgedev/2020/09/17/styling-for-windows-high-contrast-with-new-standards-for-forced-colors/>
- MDN, properties forced at paint time:
  <https://developer.mozilla.org/en-US/docs/Web/CSS/@media/forced-colors>

Not confirmed by a source (see Open points): that WebView2 behaves like Edge here, that
current GNOME sets the `HighContrast` GTK 3 theme when its High Contrast switch is on, and
the native-chrome column.

## Settings fields

| Field | Values | Default | Rust (`types.rs`) | TypeScript (`lib/types.ts:309`) |
| --- | --- | --- | --- | --- |
| `contrast` | `"system"`, `"standard"`, `"high"` | `"system"` | `pub contrast: String` | `contrast?: 'system' \| 'standard' \| 'high'` |
| `viewer_background` | `"checker"`, `"black"`, `"grey"`, `"white"` | `"checker"` | `pub viewer_background: String` | `viewer_background?: 'checker' \| 'black' \| 'grey' \| 'white'` |

Both are host-owned preferences like `appearance`: saved by `set_preference`, never changed
by a full settings save, allowed while a job runs. A settings file without them, or with an
unknown value, loads the default (same pattern as `deserialize_appearance`,
`types.rs:528-537`). `UiSettings` does not deny unknown fields, so an older build reads a
newer file. The first-run command `set_privacy_choice` also saves contrast and the other setup choices atomically.

## 1. High contrast

### Where it is chosen

Settings → Appearance (`SettingsSheet.svelte:416-476`). New parts marked `+`.

```
Appearance

Theme
Match System follows your computer's light or dark mode.
┌────────────┐ ┌────────────┐ ┌────────────┐
│ ▓▓▓▓▓▓▓▓▓▓ │ │ ░░░░░░░░░░ │ │ ▓▓▓▓▓░░░░░ │
│ ● Dark     │ │ ○ Light    │ │ ○ Match Sy…│
└────────────┘ └────────────┘ └────────────┘
Colour-blind friendly colours                      (●─)
Uses colours that stay distinct with colour vision deficiencies.

+ High contrast                                    (─●)
+ Stronger text, borders and focus outlines. Also turns on when your computer asks for
+ more contrast.

Interface text                               [ 100% ▾ ]
Enlarges the app's text and controls. Your pictures are not affected.

+ Viewer background (?)               [ Checkerboard ▾ ]
```

The Contrast row is a `.setting-row` inside the Theme `.setting-block`, after
`<ThemePicker>` (after line 428). It has no description paragraph; the explanation is the
tip.

| Element | Text |
| --- | --- |
| Label (`<label for="settings-contrast">`) | `Contrast` |
| Options (`<select id="settings-contrast">`, values `system`, `standard`, `high`) | `Match System`, `Standard`, `High` |
| `HelpTip label="Contrast"` | `High makes text, borders and focus outlines stronger and makes see-through panels solid. Match System follows your computer's setting: Increase Contrast on a Mac, a contrast theme on Windows, High Contrast on Linux. Your pictures are not affected.` |

Markup follows the Hardware row (`SettingsSheet.svelte:479-486`): `span.label-with-tip`
with the label and the `HelpTip`, then `div.setting-control` with the select.
`on:change` calls `setPreference('contrast', value)`. The select shows
`settings.contrast ?? 'system'`.

### How it resolves

`lib/theme.ts` sets one more attribute on `<html>`: `data-contrast="high"` or
`"standard"` (never `"system"`).

```ts
type Query = Pick<MediaQueryList, 'matches' | 'addEventListener' | 'removeEventListener'>;
type ThemeSettings = Pick<
  UiSettings,
  'appearance' | 'colour_blind_palette' | 'contrast' | 'viewer_background'
>;
const query = (text: string): Query =>
  typeof window.matchMedia === 'function'
    ? window.matchMedia(text)
    : { matches: false, addEventListener() {}, removeEventListener() {} };

export function createThemeController(
  media: Query = query('(prefers-color-scheme: light)'),
  nativeTheme?: (theme: 'dark' | 'light' | null) => void,
  systemContrast: Query[] = [query('(prefers-contrast: more)'), query('(forced-colors: active)')],
): { apply: (settings: ThemeSettings) => void; dispose: () => void };
```

- `render()` additionally sets
  `dataset.contrast = contrast === 'system' ? (systemContrast.some((q) => q.matches) ? 'high' : 'standard') : contrast`
  and `dataset.viewerBg = viewerBackground` (attribute `data-viewer-bg`).
- `apply()` reads `settings.contrast ?? 'system'` and
  `settings.viewer_background ?? 'checker'`.
- `render` listens for `change` on `media` and on every `systemContrast` query;
  `dispose()` removes all of them.
- The existing two-argument calls and the existing test keep working.

Match System therefore means: Theme follows light/dark, Contrast follows the contrast
setting, each on its own. All nine combinations are valid.

### Tokens

Four blocks in `styles.css`, inserted after the light colour-blind block (after line 468,
before `html.theme-loading`), in this order and with exactly these selectors:

```css
/* 1 */ [data-contrast='high'],
        [data-contrast='high'] [data-theme='dark'] { /* Dark column, table A */ }
/* 2 */ [data-theme='light'][data-contrast='high'],
        [data-contrast='high'] [data-theme='light'] { /* Light column, table A */ }
/* 3 */ [data-contrast='high'][data-palette='colour-blind'],
        [data-contrast='high'] [data-theme='dark'][data-palette='colour-blind'] { /* Dark column, table B */ }
/* 4 */ [data-theme='light'][data-contrast='high'][data-palette='colour-blind'],
        [data-contrast='high'] [data-theme='light'][data-palette='colour-blind'] { /* Light column, table B */ }
```

- Block 1 has no theme in its first selector, so it also applies to the light theme. Block
  2 must therefore set **every** token of block 1, and block 4 every token of block 3;
  otherwise dark values leak into light. A test checks this.
- The second selector of each block themes the Dark/Light/Match System tiles
  (`ThemePicker.svelte` puts `data-theme` and `data-palette` on the mock-ups). No change to
  `ThemePicker.svelte` is needed.
- Tokens not listed keep the value of the theme (illustrations, shadows, benchmark
  drawing, the marks drawn on the picture such as `--unfinished-line`).

What the values do: surfaces go to near-black or white and are told apart by borders;
text is white/black with two greys that stay above 7:1 on every surface, also on hover and
selection fills; control borders and focus rings are solid; panels that are see-through
over the picture (chips, zoom and transport bars, notices) become solid; in the dark theme
the accent turns light with black text, so the Start button and the progress bars stand out
against black (a dark terracotta would need its own outline).

Table A, default palette (117 tokens):

| Tokens | Dark | Light |
| --- | --- | --- |
| `--stage` | `#000000` | `#ebebeb` |
| `--base` | `#0a0a0a` | `#ffffff` |
| `--panel`, `--running-card-bg`, `--fact-card-bg`, `--device-card-bg` | `#0f0f0f` | `#ffffff` |
| `--chrome`, `--header-bg` | `#141414` | `#f2f2f2` |
| `--raised` | `#1c1c1c` | `#ffffff` |
| `--hover` | `#2e2e2e` | `#e0e0e0` |
| `--recess`, `--meter-track` | `#000000` | `#f2f2f2` |
| `--dialog-bg`, `--welcome-bg`, `--notice-surface` | `#141414` | `#ffffff` |
| `--pane-seam`, `--border`, `--surface-border`, `--card-border-strong`, `--chip-border`, `--note-border`, `--button-border`, `--notice-outline`, `--memory-border`, `--hero-border`, `--cpu-card-border`, `--benchmark-stage-border`, `--scrubber-track` | `#8f8f8f` | `#6b6b6b` |
| `--side-placeholder-bg` | `#0f0f0f` | `#f2f2f2` |
| `--preview-canvas-background`, `--chip-bg`, `--hud-bg`, `--overlay-pill-bg`, `--thumb-badge-bg`, `--badge-backdrop`, `--preview-hud-bg`, `--dropzone-pill-bg`, `--compare-handle-bg`, `--on-accent`, `--recipe-button-text` | `#000000` | `#ffffff` |
| `--well-checker` | `#262626` | `#ffffff` |
| `--text`, `--chip-text`, `--hud-text`, `--overlay-text`, `--badge-text`, `--memory-text`, `--cpu-score-text`, `--hero-score-text`, `--compare-handle-border`, `--focus`, `--accent-line`, `--control-accent`, `--scrubber-fill`, `--gauge-arc`, `--gauge-needle`, `--spinner-head`, `--meter-dot-fill`, `--activity-dot` | `#ffffff` | `#000000` |
| `--secondary`, `--dialog-muted-text`, `--benchmark-muted-text`, `--memory-secondary`, `--memory-label` | `#d9d9d9` | `#333333` |
| `--tertiary` | `#c7c7c7` | `#404040` |
| `--memory-control`, `--dropzone-stroke`, `--gauge-mid`, `--cpu-bar-fill` | `#c2c2c2` | `#404040` |
| `--divider`, `--section-divider`, `--hairline`, `--row-divider`, `--tint-subtle`, `--memory-divider`, `--gauge-track`, `--spinner-track` | `#575757` | `#b8b8b8` |
| `--hairline-faint` | `rgba(255, 255, 255, 0.08)` | `rgba(0, 0, 0, 0.06)` |
| `--accent-fill` | `rgba(255, 255, 255, 0.1)` | `rgba(0, 0, 0, 0.08)` |
| `--row-hover` | `rgba(255, 255, 255, 0.07)` | `rgba(0, 0, 0, 0.05)` |
| `--row-selected`, `--item-active` | `rgba(255, 255, 255, 0.12)` | `rgba(0, 0, 0, 0.1)` |
| `--control-active` | `rgba(255, 255, 255, 0.16)` | `rgba(0, 0, 0, 0.12)` |
| `--control-hover` | `rgba(255, 255, 255, 0.2)` | `rgba(0, 0, 0, 0.16)` |
| `--active-tile-border` | `#ffffff` | `#ffffff` |
| `--tile-inset` | `#000000` | `#000000` |
| `--modal-backdrop` | `rgba(0, 0, 0, 0.8)` | `rgba(255, 255, 255, 0.85)` |
| `--accent` | `#f2a48a` | `#8a2f17` |
| `--accent-hi` | `#ffb9a3` | `#943419` |
| `--danger`, `--danger-border`, `--danger-card-border`, `--danger-overlay-border`, `--danger-pill-border`, `--memory-error-border` | `#ff7d80` | `#8a1c24` |
| `--danger-text`, `--danger-text-soft`, `--danger-text-warm`, `--danger-text-muted`, `--memory-error-text` | `#ffb3b5` | `#8a1c24` |
| `--danger-surface`, `--danger-banner-bg`, `--danger-overlay-bg` | `#2a0c0d` | `#ffffff` |
| `--danger-fill`, `--danger-pill-bg` | `rgba(255, 125, 128, 0.14)` | `rgba(138, 28, 36, 0.08)` |
| `--danger-hover-bg` | `rgba(255, 125, 128, 0.22)` | `rgba(138, 28, 36, 0.14)` |
| `--warning`, `--warning-strong`, `--warning-border`, `--warning-pill-border` | `#f2b25c` | `#5e4000` |
| `--warning-text`, `--warning-text-muted` | `#ffd08a` | `#5e4000` |
| `--warning-fill` | `rgba(242, 178, 92, 0.14)` | `rgba(94, 64, 0, 0.08)` |
| `--success-strong` | `#8fdba6` | `#0d5232` |
| `--success-fill`, `--success-pill-bg` | `rgba(143, 219, 166, 0.14)` | `rgba(13, 82, 50, 0.08)` |
| `--success-glow` | `rgba(143, 219, 166, 0.3)` | `rgba(13, 82, 50, 0.2)` |

Table B, colour-blind palette on top of A (30 tokens, the same set the existing
colour-blind blocks define):

| Tokens | Dark | Light |
| --- | --- | --- |
| `--accent` | `#7cc8f0` | `#004f78` |
| `--accent-hi` | `#9bd6f7` | `#005d8c` |
| `--danger`, `--danger-border`, `--danger-card-border`, `--danger-overlay-border`, `--danger-pill-border`, `--memory-error-border` | `#ffa257` | `#7d3100` |
| `--danger-text`, `--danger-text-soft`, `--danger-text-warm`, `--danger-text-muted`, `--memory-error-text` | `#ffc799` | `#7d3100` |
| `--danger-surface`, `--danger-banner-bg`, `--danger-overlay-bg` | `#2a1708` | `#ffffff` |
| `--danger-fill`, `--danger-pill-bg` | `rgba(255, 162, 87, 0.14)` | `rgba(125, 49, 0, 0.08)` |
| `--danger-hover-bg` | `rgba(255, 162, 87, 0.22)` | `rgba(125, 49, 0, 0.14)` |
| `--warning`, `--warning-strong`, `--warning-border`, `--warning-pill-border` | `#f0c35a` | `#5e4000` |
| `--warning-text`, `--warning-text-muted` | `#f7d77a` | `#5e4000` |
| `--warning-fill` | `rgba(240, 195, 90, 0.14)` | `rgba(94, 64, 0, 0.08)` |
| `--success-strong` | `#6fd9b9` | `#004f38` |
| `--success-fill`, `--success-pill-bg` | `rgba(111, 217, 185, 0.14)` | `rgba(0, 79, 56, 0.08)` |
| `--success-glow` | `rgba(111, 217, 185, 0.3)` | `rgba(0, 79, 56, 0.2)` |

### Rules that tokens cannot express

At the end of `styles.css`:

```css
/* High contrast: states that are only a faint fill get a line as well. */
[data-contrast='high'] .media-row.selected,
[data-contrast='high'] .row.selected,
[data-contrast='high'] .rail-item.active {
  box-shadow: inset 3px 0 0 var(--text);
}
[data-contrast='high'] .settings-rail .rail-item.active {
  box-shadow: none; /* has its own bar, styles.css:4224 */
}
[data-contrast='high'] .seg-inline button.active {
  box-shadow: inset 0 0 0 1px var(--text);
}
[data-contrast='high'] .zoom-hud button.active,
[data-contrast='high'] .video-transport .transport-audio button.active {
  box-shadow: inset 0 -2px 0 var(--hud-text);
}
/* Bars on the picture need an edge once they are solid. */
[data-contrast='high'] .zoom-hud,
[data-contrast='high'] .video-transport {
  box-shadow: 0 0 0 1px var(--border);
}
[data-contrast='high'] .comparison-error {
  border: 1px solid var(--danger-border);
}
/* Tracks drawn in a border colour would be too close to their fill. */
[data-contrast='high'] .row-progress,
[data-contrast='high'] .benchmark-reference-track {
  background: var(--divider);
}
/* The count badge is text on the border colour (styles.css:675). */
[data-contrast='high'] .compact-nav span {
  background: transparent;
  box-shadow: inset 0 0 0 1px var(--border);
}
[data-contrast='high'] .button.primary:disabled {
  border-color: var(--border);
}
[data-contrast='high'] .help-tip:focus-visible {
  outline-width: 2px;
}
/* Disabled stays readable (0.35 to 0.45 today). Component styles set their own
   opacity with higher specificity, hence !important. */
[data-contrast='high'] :is(button, input, select):disabled,
[data-contrast='high'] [aria-disabled='true'] {
  opacity: 0.6 !important;
}
```

These selectors also reach classes that are styled inside components
(`.comparison-error` in `PreviewPane.svelte:1300`): Svelte scopes its own rules, not the
class names, and none of the properties set here is set there.

Focus rings need no rule: every ring is `2px solid var(--focus)` (one is 1 px, fixed
above), and `--focus` becomes solid white/black.

### Windows contrast themes

After the rules above. This is the minimum that keeps states visible when Chromium replaces
our colours; it applies whatever `data-contrast` says.

```css
@media (forced-colors: active) {
  /* The picture area keeps the chosen background and the marks drawn on the picture. */
  .canvas-well,
  .setup-mock,
  .bg-swatch {
    forced-color-adjust: none;
  }
  /* Controls and messages inside it follow the system colours again. */
  .canvas-well :is(.zoom-hud, .video-transport, .original-chip, .unfinished-chip,
      .divider-labels, .comparison-note, .comparison-error, .slot-state, .canvas-empty, .box-bar) {
    forced-color-adjust: auto;
  }
  /* Fills are removed: selected and pressed states get a system-colour edge. */
  .media-row.selected,
  .row.selected,
  .rail-item.active,
  .chip.on,
  .setup-theme-tile.active,
  .saved-recipe button.active,
  .recipe-grid button.active,
  .seg-inline button.active,
  .zoom-hud button.active {
    outline: 2px solid Highlight;
    outline-offset: -2px;
  }
  .switch {
    forced-color-adjust: none;
    border-color: ButtonText;
    background: Canvas;
  }
  .switch::before {
    background: ButtonText;
  }
  .switch:checked {
    border-color: Highlight;
    background: Highlight;
  }
  .switch:checked::before {
    background: HighlightText;
  }
  .progress i,
  .row-progress i,
  .download-track i,
  .meter-clip > i {
    forced-color-adjust: none;
    background: Highlight;
  }
  .button.primary {
    border-color: ButtonText;
  }
  :is(button, input, select):disabled,
  [aria-disabled='true'] {
    color: GrayText;
    opacity: 1 !important;
  }
}
```

### Screens to check by eye in both high-contrast themes

| Screen | What must hold |
| --- | --- |
| Media list, Outputs | Selected row: fill plus the 3 px bar. Row progress visible on its track. |
| Enhance: plan rows | Stage label, model name, state and the warning state readable; "Try others…" links. |
| Chips, pills, presets | Off: solid border. On: white/black border plus fill. Pills keep a border. |
| Start, status bar | Start: light accent with black text (dark), dark accent with white text (light); disabled Start has a border. Progress bar on its track. |
| Preview | Zoom bar, Original/Unfinished chips and divider labels solid with a border; divider line and handle; live tile outline white with a black inner line. |
| Unfinished tiles | Unchanged (already white on black, theme-independent). |
| Compare | Slot cards, box bars, slot messages, divider, both zoom bars. |
| Video | Transport and scrubber (played part against track 3:1). |
| Dialogs, Settings, Model library | Border around the dialog; rail selection; switches on and off; selects; `?` tips. |
| Update, welcome and GPU notices | Solid surface with a border; primary button. |
| Empty stage, Benchmark | Not re-tokened in full. Check that nothing important is faint. |

### Computed contrast

WCAG 2.2 relative-luminance ratios of the token values above; translucent fills are
composited on the named surface first. All 99 text pairs and 84 boundary pairs per
combination were computed; the table shows the tightest and the most used.
Minimum over all text pairs: 7.2 (dark), 7.6 (light), 7.2 (dark + colour-blind),
7.1 (light + colour-blind). Minimum over all boundary pairs: 3.2, 3.8, 3.2, 3.6.

| Text pair (needs 7:1) | Dark | Light | Dark, colour-blind | Light, colour-blind |
| --- | --- | --- | --- | --- |
| `--text` on `--base` | 19.8 | 21.0 | 19.8 | 21.0 |
| `--text` on `--raised` | 17.0 | 21.0 | 17.0 | 21.0 |
| `--text` on `--hover` | 13.6 | 15.9 | 13.6 | 15.9 |
| `--secondary` on `--panel` | 13.6 | 12.6 | 13.6 | 12.6 |
| `--secondary` on `--hover` | 9.6 | 9.6 | 9.6 | 9.6 |
| `--tertiary` on `--raised` | 10.1 | 10.4 | 10.1 | 10.4 |
| `--tertiary` on `--hover` | 8.0 | 7.9 | 8.0 | 7.9 |
| `--tertiary` on `--row-selected on --panel` | 8.3 | 8.3 | 8.3 | 8.3 |
| `--tertiary` on `--accent-fill on --raised` | 7.5 | 8.7 | 7.5 | 8.7 |
| `--secondary` on `--hairline-faint on --raised` | 9.6 | 11.1 | 9.6 | 11.1 |
| `--text` on `--tint-subtle` | 7.2 | 10.6 | 7.2 | 10.6 |
| `--chip-text` on `--chip-bg` | 21.0 | 21.0 | 21.0 | 21.0 |
| `--hud-text` on `--control-active on --hud-bg` | 14.6 | 16.0 | 14.6 | 16.0 |
| `--on-accent` on `--accent` | 10.5 | 8.4 | 11.4 | 8.8 |
| `--on-accent` on `--accent-hi` | 12.7 | 7.6 | 13.4 | 7.1 |
| `--danger-text` on `--panel` | 11.3 | 9.2 | 12.7 | 9.1 |
| `--danger-text` on `--danger-fill on --panel` | 9.2 | 8.0 | 10.0 | 7.9 |
| `--danger-text-warm` on `--danger-surface` | 10.7 | 9.2 | 11.3 | 9.1 |
| `--warning-text` on `--raised` | 11.9 | 9.5 | 12.1 | 9.5 |
| `--warning-text` on `--warning-fill on --raised` | 8.9 | 8.3 | 8.9 | 8.3 |
| `--success-strong` on `--raised` | 10.4 | 9.3 | 10.0 | 9.7 |
| `--success-strong` on `--success-fill on --raised` | 7.6 | 8.1 | 7.4 | 8.4 |

| UI pair (needs 3:1) | Dark | Light | Dark, colour-blind | Light, colour-blind |
| --- | --- | --- | --- | --- |
| `--border` against `--base` | 6.1 | 5.3 | 6.1 | 5.3 |
| `--border` against `--raised` | 5.3 | 5.3 | 5.3 | 5.3 |
| `--border` against `--hover` | 4.2 | 4.0 | 4.2 | 4.0 |
| `--chip-border` against `--recess` | 6.5 | 4.8 | 6.5 | 4.8 |
| `--focus` against `--base` | 19.8 | 21.0 | 19.8 | 21.0 |
| `--focus` against `--hover` | 13.6 | 15.9 | 13.6 | 15.9 |
| `--accent-line` against `--recess` | 21.0 | 18.8 | 21.0 | 18.8 |
| `--accent` against `--base` | 9.9 | 8.4 | 10.7 | 8.8 |
| `--accent` against `--raised` | 8.5 | 8.4 | 9.2 | 8.8 |
| `--accent-hi` against `--well-checker` | 9.2 | 7.6 | 9.6 | 7.1 |
| `--accent-hi` against `--divider` | 4.4 | 3.8 | 4.6 | 3.6 |
| `--danger-border` against `--panel` | 7.8 | 9.2 | 9.6 | 9.1 |
| `--warning-border` against `--panel` | 10.3 | 9.5 | 11.6 | 9.5 |
| `--success-strong` against `--panel` | 11.7 | 9.3 | 11.2 | 9.7 |
| `--control-accent` against `--panel` | 19.2 | 21.0 | 19.2 | 21.0 |
| `--scrubber-fill` against `--scrubber-track` | 3.2 | 3.9 | 3.2 | 3.9 |
| `--scrubber-track` against `--hud-bg` | 6.5 | 5.3 | 6.5 | 5.3 |
| `--compare-handle-border` against `--compare-handle-bg` | 21.0 | 21.0 | 21.0 | 21.0 |

Separators (`--divider`, `--hairline`, `--row-divider`, `--section-divider`,
`--tint-subtle`) are deliberately weaker than control borders: 2.9:1 on black, 2.0:1 on
white. They divide regions and are not needed to identify a control. `--hairline-faint`
stays a faint fill because pills use it as a background. Disabled text at opacity 0.6 is
7.3:1 on dark `--base` and 5.7:1 on white (WCAG exempts disabled controls).

## 2. Viewer background

### Choices

| Value | Label | Drawn |
| --- | --- | --- |
| `checker` (default) | `Checkerboard` | Today's stage: `--stage` with `--well-checker` squares, in the theme's tones. Transparent areas of a picture show it. |
| `black` | `Black` | `#000000` |
| `grey` | `Grey` | `#767676` (18 % grey) |
| `white` | `White` | `#ffffff` |

- The colour fills the whole stage (`.canvas-well`), so it surrounds the picture and shows
  through transparent pixels.
- It applies only while a picture or video is on the stage. Empty states and "Preview
  unavailable" keep the theme, because their text is drawn straight on the stage.
- With a fixed colour the picture has no drop shadow: a shadow darkens the surround at the
  edge that is being judged.
- The divider line switches to light with dark edges so it reads on all three colours in
  both themes (on black 19.5:1 from the line; on grey 4.2:1; on white 5.7:1 from the edges).
- Chips, the zoom bar, slot messages and the loading note carry their own background and
  are unaffected.
- Black, Grey and White are the same in every theme and in high contrast. Checkerboard
  follows the theme; in high contrast its squares are stronger (1.39:1 dark, 1.19:1 light,
  against 1.03:1 and 1.09:1 today).

### In Settings

Last row of Appearance (after the Interface text row, `SettingsSheet.svelte:475`); see the
mock-up above.

| Element | Text |
| --- | --- |
| Label (`<label for="settings-viewer-background">`) | `Viewer background` |
| Options (`<select id="settings-viewer-background">`, values `checker`, `black`, `grey`, `white`) | `Checkerboard`, `Black`, `Grey`, `White` |
| `HelpTip label="Viewer background"` | `What is shown behind the picture in the viewer and in Compare. Grey is the neutral 18 % grey used to judge brightness and colour. Black and White show how edges and transparent areas hold up. Checkerboard shows where a picture is transparent. Saved files are not changed.` |

`on:change` calls `setPreference('viewer_background', value)`; the select shows
`settings.viewer_background ?? 'checker'`.

### In the viewer

One button at the end of every zoom bar. It shows the current background as a swatch and
steps to the next one: Checkerboard → Black → Grey → White → Checkerboard.

```
image:   ┌─────────────────────────────────┐
         │  −   100%   +   Fit   1:1   ▒  │      (appears on hover, as today)
         └─────────────────────────────────┘
video:   ┌──────────────────────────────┐ ┌──────────────────────────┐
         │ ▶  0:03 ━━━━●────── 0:12  1× │ │ −  100%  +  Fit  1:1  ▒  │
         └──────────────────────────────┘ └──────────────────────────┘
```

| Element | Text (example: current Grey) |
| --- | --- |
| `aria-label` | `Viewer background: Grey` |
| `title` | `Background: Grey · click for White` |

New `desktop/src/BackgroundSwitch.svelte`:

```svelte
<script lang="ts">
  import { backgroundLabel, nextBackground, type ViewerBackground } from './lib/viewerBackground';
  export let value: ViewerBackground = 'checker';
  export let set: (value: ViewerBackground) => void = () => {};
  $: next = nextBackground(value);
</script>

<button
  type="button"
  class="bg-switch"
  aria-label={`Viewer background: ${backgroundLabel(value)}`}
  title={`Background: ${backgroundLabel(value)} · click for ${backgroundLabel(next)}`}
  on:click={() => set(next)}><i class="bg-swatch" data-bg={value} aria-hidden="true"></i></button
>
```

New `desktop/src/lib/viewerBackground.ts`: `type ViewerBackground`,
`VIEWER_BACKGROUNDS` (the four ids with their labels, in cycle order; the Settings select
is built from it), `backgroundLabel(id)`, `nextBackground(id)`.

Placement, as the last child of each `.zoom-hud`:

| File | Where |
| --- | --- |
| `PreviewPane.svelte` | after the `1:1` button, line 1078 |
| `VideoComparison.svelte` | after the `1:1` button, line 492 |
| `ComparePane.svelte` | after `Fit` in the image bar (about line 1069) and in the video bar (line 1125) |

`PreviewPane`, `ComparePane` and `VideoComparison` each get two props,
`viewerBackground: ViewerBackground = 'checker'` and
`setViewerBackground: (value: ViewerBackground) => void = () => {}`. `PreviewPane` passes
them on to `VideoComparison` (line 972). `App.svelte` passes
`snapshot.settings.viewer_background ?? 'checker'` and
`(value) => void setPreference('viewer_background', value)` at lines 4314 and 4332.

### CSS

After `.canvas-well.rendering` (`styles.css:1094`). The stage needs no new class or
attribute: the choice is on `<html>` and `:has(.image-stage)` is true exactly while a
picture or video is mounted (`:has` is already used at line 1264).

```css
/* Viewer background: only while a picture or video is on the stage. */
:root[data-viewer-bg='black'] .canvas-well:has(.image-stage) {
  --viewer-bg: #000000;
}
:root[data-viewer-bg='grey'] .canvas-well:has(.image-stage) {
  --viewer-bg: #767676; /* 18 % grey */
}
:root[data-viewer-bg='white'] .canvas-well:has(.image-stage) {
  --viewer-bg: #ffffff;
}
:root:is([data-viewer-bg='black'], [data-viewer-bg='grey'], [data-viewer-bg='white'])
  .canvas-well:has(.image-stage) {
  background-color: var(--viewer-bg);
  background-image: none;
  /* The divider reads on a fixed colour in either theme: light line, dark edges. */
  --compare-line-color: #f6f6f9;
  --shadow-hairline: rgba(0, 0, 0, 0.6);
}
:root:is([data-viewer-bg='black'], [data-viewer-bg='grey'], [data-viewer-bg='white'])
  .canvas-well
  .image-stage {
  box-shadow: none;
}
```

After the `.zoom-hud` rules (`styles.css:1298`):

```css
.zoom-hud .bg-switch {
  margin-left: 3px;
}
.bg-swatch {
  display: block;
  width: 14px;
  height: 14px;
  border: 1px solid var(--hud-text);
  border-radius: 1px;
}
.bg-swatch[data-bg='checker'] {
  background: conic-gradient(#b3b3b3 25%, #ffffff 0 50%, #b3b3b3 0 75%, #ffffff 0);
}
.bg-swatch[data-bg='black'] {
  background: #000000;
}
.bg-swatch[data-bg='grey'] {
  background: #767676;
}
.bg-swatch[data-bg='white'] {
  background: #ffffff;
}
```

`ComparePane.svelte:1492`: `.box { background: var(--viewer-bg, var(--stage)); }`.

Not changed: the canvas fill for live tiles (`PreviewPane.svelte:578`,
`--preview-canvas-background`); it is covered by the source picture.

## Files and functions to change

Host (`desktop/src-tauri/src`):

| Where | Change |
| --- | --- |
| `types.rs:356` | After `colour_blind_palette`: the two fields, each with `#[serde(default = "default_…", deserialize_with = "deserialize_…")]`. |
| `types.rs:509-537` | `default_contrast()` → `"system"`, `default_viewer_background()` → `"checker"`, and `deserialize_contrast` / `deserialize_viewer_background`, which return the default for an unknown value. |
| `types.rs:539-544` | Both defaults in `impl Default for UiSettings`. |
| `commands.rs:510-515` | `keep_update_switches`: copy both from `current`. |
| `commands.rs:721-727` | `apply_preference`: arms `"contrast"` and `"viewer_background"` like `"appearance"`; errors `unknown contrast` and `unknown viewer background`. |
| `commands.rs:3724` | `validate_start_input` builds a `UiSettings` literal; add the fields if it does not end in `..Default::default()`. |

UI (`desktop/src`):

| Where | Change |
| --- | --- |
| `lib/types.ts:309-311` | The two optional fields. |
| `lib/api.ts:148-169` | `PreferenceName` gains `'contrast'` and `'viewer_background'`; the value union of `setPreference` gains both value types. |
| `lib/theme.ts` | As in "How it resolves". |
| `lib/viewerBackground.ts`, `BackgroundSwitch.svelte` | New. |
| `App.svelte:1495-1497` | `themeForRefresh` takes the four-field `ThemeSettings`. |
| `App.svelte:443-453`, `1654-1658` | The first-run preview keeps the saved contrast and background: pass `{ ...snapshot.settings, appearance, colour_blind_palette }`. |
| `App.svelte:2462-2492` | `setPreference`: widen the value union; call `themeForRefresh` for `contrast` and `viewer_background` too (both branches). |
| `App.svelte:4314`, `4332` | The two props on `ComparePane` and `PreviewPane`. |
| `SettingsSheet.svelte:41-48` | Widen the `setPreference` prop's value union. |
| `SettingsSheet.svelte:428`, `475` | The Contrast row and the Viewer background row. |
| `PreviewPane.svelte`, `ComparePane.svelte`, `VideoComparison.svelte` | Props and `<BackgroundSwitch>` as listed. |
| `styles.css` | Token blocks after 468; viewer rules after 1094 and 1298; high-contrast rules and the forced-colours block at the end. |
| `lib/demo.ts:61-62` | `contrast: 'system'`, `viewer_background: 'checker'`. |

Docs: two entries under 0.6.0 in `CHANGELOG.md`:

- `**High contrast.** Settings > Appearance > Contrast gives the dark and the light theme stronger text, borders and focus outlines. Match System follows the computer's contrast setting.`
- `**Viewer background.** The picture can sit on a checkerboard, black, neutral grey or white: Settings > Appearance, or the swatch beside the zoom controls. Preview, Compare and video use it.`

One sentence about the background in `docs/comparing-results.md`.

## Tests

### Host (`cargo test`)

| Test | Expected |
| --- | --- |
| `settings.rs`, new, next to `appearance_defaults_round_trips_and_rejects_unknown_values` (378) | `{}` loads `contrast == "system"`, `viewer_background == "checker"`. `"high"` / `"grey"` round-trip. `{"contrast":"max","viewer_background":"pink"}` loads the defaults. |
| `commands/tests.rs:356` (`set_preference_round_trips…`) | Add `("contrast", "high")` and `("viewer_background", "grey")` to the list and assert the saved values. `apply_preference` with `"contrast": "loud"`, `"contrast": true`, `"viewer_background": "blue"` is an error and leaves the saved value. |
| `commands/tests.rs:425` (`a_full_settings_save_never_changes…`) | `current` has `contrast: "high"`, `viewer_background: "white"`; after a full save with defaults both are still `high` and `white`. |

### vitest

| File | Case | Expected |
| --- | --- | --- |
| `lib/theme.test.ts` | Existing test | Unchanged, passes. |
| | `contrast: 'system'`, both queries false | `dataset.contrast === 'standard'` |
| | then `prefers-contrast` query `matches = true`, fire `change` | `'high'` |
| | only the `forced-colors` query true | `'high'` |
| | `contrast: 'standard'` while both queries are true | `'standard'` |
| | `contrast: 'high'` while both are false | `'high'` |
| | settings without `contrast` and `viewer_background` | `'standard'` (queries false) and `dataset.viewerBg === 'checker'` |
| | `viewer_background: 'grey'` | `dataset.viewerBg === 'grey'` |
| | `dispose()` | `removeEventListener('change', …)` called on all three queries |
| `lib/contrast.test.ts` | Add a third dimension `high` to `palette()`; a rule counts when one of its comma-separated selectors is wanted (`[data-contrast="high"]`, `[data-theme="light"][data-contrast="high"]`, and the two colour-blind forms). Existing four cases | Unchanged, pass. |
| | Four high-contrast cases: the existing text pairs plus `--tertiary` on `--base`/`--panel`/`--raised`/`--hover`, `--secondary` on `--hover`, `--chip-text` on `--chip-bg`, `--hud-text` on `--hud-bg`, `--on-accent` on `--accent-hi` | every ratio ≥ 7 |
| | Same cases: `--border`, `--chip-border`, `--accent-line`, `--focus`, `--accent` on `--base`/`--panel`/`--raised`/`--hover`; `--accent-hi` on `--well-checker` and `--divider`; `--scrubber-fill` on `--scrubber-track` | every ratio ≥ 3 |
| | Token sets | Block 2 defines exactly the tokens of block 1; block 4 exactly those of block 3; block 3's set equals the existing `[data-palette='colour-blind']` set; every token exists in `:root`. |
| `lib/viewerBackground.test.ts` (new) | `nextBackground` over four steps | `black`, `grey`, `white`, `checker` |
| | `backgroundLabel` | `Checkerboard`, `Black`, `Grey`, `White` |
| `BackgroundSwitch.test.ts` (new) | `value="grey"` | Button named `Viewer background: Grey`, title `Background: Grey · click for White`, swatch `data-bg="grey"`. |
| | click | `set` called once with `'white'` |
| `SettingsSheet.test.ts` (Appearance, near 161) | Fields missing | `Contrast` shows `system`, `Viewer background` shows `checker`. |
| | `selectOptions(getByLabelText('Contrast'), 'high')` | `setPreference('contrast', 'high')` |
| | `selectOptions(getByLabelText('Viewer background'), 'grey')` | `setPreference('viewer_background', 'grey')` |
| | Tips | Buttons `About Contrast` and `About Viewer background` exist with the texts above. |
| | `settingsLocked` | Both selects stay enabled. |
| `PreviewPane.test.ts` | A photo with `viewerBackground="black"` | The `Preview zoom` group holds `Viewer background: Black`; a click calls `setViewerBackground('grey')`. |
| `ComparePane.test.ts`, `VideoComparison.test.ts` | Same check in `Compare zoom` and `Video zoom`. | Same. |
| `App.test.ts` | Click the swatch in the preview | `api.setPreference('viewer_background', 'black')`, then `<html data-viewer-bg="black">`. |
| | Choose Contrast → High in Settings | `api.setPreference('contrast', 'high')`, then `<html data-contrast="high">`. |
| | First-run preview with saved `contrast: 'high'`: choose the Light tile | `data-theme="light"` and still `data-contrast="high"`. |

### e2e (Playwright, `desktop/e2e`)

`themes.e2e.ts`, extend the loop with a contrast value and the shots to take:

| Scenario | Shots (`@visual`, new baselines in all four projects) |
| --- | --- |
| dark, `contrast: 'high'` | `theme-high-contrast-dark-main`, `-enhance`, `-dialog` |
| light, `contrast: 'high'` | `theme-high-contrast-light-main`, `-enhance`, `-dialog` |
| dark + colour-blind, high | `theme-high-contrast-dark-colour-blind-enhance` |
| light + colour-blind, high | `theme-high-contrast-light-colour-blind-enhance` |

Each also expects `html[data-contrast="high"]`.

New cases in `themes.e2e.ts`:

| Case | Expected |
| --- | --- |
| No `contrast` in the settings, `page.emulateMedia({ contrast: 'more' })` | `data-contrast="high"`; back to `'no-preference'` → `"standard"` without a reload. |
| `contrast: 'standard'` with `contrast: 'more'` emulated | `"standard"` |
| `contrast: 'high'`, nothing emulated | `"high"` |
| Chromium projects only: `emulateMedia({ forcedColors: 'active' })`, no `contrast` | `"high"`; a checked and an unchecked `.switch` have different computed `background-color`; `.canvas-well` has `forced-color-adjust: none`. Baseline `forced-colors-enhance` (2 files). |
| High contrast, dark and light: measured contrast (move `contrastOnPage` from `layout.e2e.ts:617` to `app.ts`) of a plan row's stage label and state, a chip, a pill, the Start button label, a media row's secondary line on the selected row, the zoom bar's percentage | each ≥ 7 |
| High contrast: Settings → Appearance | Tiles show high-contrast mock-ups. Baselines `settings-appearance` (default dark) and `settings-appearance-high-contrast-light`. |

New `viewer-background.e2e.ts` (scenario `withOutputs` unless noted):

| Case | Expected |
| --- | --- |
| Default | `html[data-viewer-bg="checker"]`; `.canvas-well` computed `background-image` is not `none`. |
| `viewer_background: 'grey'` | `.canvas-well` `background-color` is `rgb(118, 118, 118)`, `background-image` `none`, `.image-stage` `box-shadow` `none`. |
| Hover the picture, click `Viewer background: Checkerboard` | Last `set_preference` call is `{ name: 'viewer_background', value: 'black' }` (as in `stop-video.e2e.ts:33`); stage is `rgb(0, 0, 0)`; the button is now `Viewer background: Black`. Three more clicks end at Checkerboard. |
| `viewer_background: 'white'`, no media | The empty stage keeps the theme background and its text. |
| `viewer_background: 'white'`, Compare with boxes | Every `.box` is `rgb(255, 255, 255)`; the box bars keep the theme. |
| `viewer_background: 'grey'`, a finished video | The stage behind the video is grey; the swatch is in the `Video zoom` group. |
| Settings → Appearance → Viewer background → White | `set_preference` with `white`; after closing, the stage is white. |
| `@visual` | `viewer-bg-grey-preview` (dark), `viewer-bg-black-preview-light` (light theme, split view with the divider), `viewer-bg-white-compare-boxes` (dark). |

Existing baselines: only shots in which a zoom bar is visible change (the video bar is
always visible: `compare-unfinished-video`, and any video shot in `advanced-video`).
Regenerate those and check that the only difference is the added swatch. All other existing
baselines must stay identical, because the default is today's look.

## Open points

1. **WebView2 and contrast themes.** No WebView2-specific source was found; the behaviour
   is inferred from Edge. Check on the Windows laptop with a contrast theme on: the app
   must come up with `data-contrast="high"`, switches and selected rows must stay
   distinguishable, the picture area must keep its background.
2. **Linux.** WebKitGTK only looks at the GTK 3 theme name. Whether a current GNOME sets it
   to `HighContrast` when High Contrast is switched on could not be confirmed. Check on
   ddp; if it does not, Match System simply stays Standard there and High is the way.
3. **The checkerboard is faint** (1.03:1 between squares in the dark theme), so on a
   transparent picture it reads as "dark" more than as a pattern. This spec keeps it,
   because it is the default behind every opaque photo and a strong pattern there would
   work against judging results. A strong checkerboard only inside the picture's bounds
   would be the proper fix, but it needs the host to report whether a file has an alpha
   channel (`MediaItem` has no such field); without that it would flash behind opaque
   pictures while they load and show in letterbox areas.
4. **Following the system is the default.** After the update, a user who has Increase
   Contrast on sees LocalSR in high contrast without having chosen it. That is the intent
   of "following the system setting"; Standard turns it off.
5. **Light accent in dark high contrast** changes the Start button from terracotta with
   white text to a light terracotta with black text. The alternative is a darker
   terracotta with a white outline.
6. **The swatch in the zoom bar** goes beyond "a setting in Settings". It is one small
   component; leaving it out removes `BackgroundSwitch.svelte`, the six props and the
   baseline changes, nothing else.
7. **Forced colours** can only be checked here with Playwright's emulation, which uses a
   default palette, not a real Windows theme.
