# Changelog

All notable changes will be documented here. The project follows semantic versioning once the first
stable release is published.

## [Unreleased]

### Changed

- Cleaned up the toolbar to a single macOS-style command strip: the redundant Advanced and Compare
  toolbar buttons are gone. Advanced settings stay in the Enhance inspector's disclosure row and
  Compare stays with the preview's own control bar.
- Detect Faces now toggles: once faces are found the same button becomes "Clear Faces (n)" so
  face-aware restoration can be deselected again.
- The Output scale control only appears after a task is chosen instead of rendering an empty box.

### Fixed

- Fixed overlapping text in the empty preview state (the plus icon rendered on top of the caption)
  and several controls that were centered instead of anchored: the queue selection bar, section
  titles, value-line labels, and the top separators of the preview, action, and status bars.
- The "Advanced settings" disclosure summary no longer truncates to "Model, output, har…".

### Added

- Added `HAT-S ×4 Face — Restoration` model (`hat_s_x4_face`) to the curated catalog, fine-tuned and blended ($\alpha=0.10$) for enhanced portrait restoration and clean fidelity retention.
- Implemented face-model pairing between `hat_s_x4` and `hat_s_x4_face` for face-aware pipelines (ADR 0004).

## [0.0.2-alpha] - 2026-08-18

### Added

- A cross-platform interactive installer that selects a CPU, CUDA, ROCm, or Intel XPU PyTorch
  backend before installing LocalSR.

### Changed

- Redesigned the Slint workspace around a clear Media, Preview, and Enhance workflow, with the
  Upscale/Denoise choice and Quick/Best actions visible before expert settings.
- Added wide, medium, and compact layouts so the preview remains usable across desktop window sizes
  while side panes become focused in-window views on smaller screens.
- Restored operating-system-native logical-pixel scaling instead of forcing a global scale factor.
- Reduced the curated catalog to four clearly differentiated models: HAT-S and HAT-L for upscaling,
  and RealPLKSR and NAFNet for denoising. Compatible custom Spandrel checkpoints remain supported.

### Fixed

- Declared the source package layout explicitly for reliable editable installs and release builds.
- Preserved the existing wheel-safe selectors, accessibility semantics, progressive preview,
  synchronized pan/zoom, cancellation, and sequential one-accelerator batch behavior in the new UI.

## [0.0.1-alpha] - 2026-08-10

### Added

- A compact Slint desktop interface with flat platform-neutral panels, reusable controls, a large
  before/after canvas, Single and Batch queues, task-specific model choices, hardware telemetry,
  collapsible advanced settings, and a persistent status/action strip.
- A pinned, optional NAFNet SIDD Width64 checkpoint for maximum-fidelity real camera denoising,
  alongside SCUNet for general blind noise and RealPLKSR for quick denoising.
- A standard-library subprocess bridge for the Slint host so Torch and Spandrel remain isolated,
  plus a bounded Pillow compositor for live tile previews.
- Sequential batch processing for Upscale, Denoise, and Upscale + Denoise tasks.
- Modern Qt Quick/QML interface with a Qt Design Studio project and design-time mock data.
- Quick and Best Quality presets with transparent model/device/precision/tile decisions.
- Lightweight SPAN ×4 and photo-oriented RealPLKSR ×4 catalog entries with pinned hashes.
- Active-tile overlay and bounded progressive output preview over the worker protocol.
- Live macOS memory-pressure, compression, swap, and MPS allocator telemetry.
- Native macOS DMG, Windows Inno Setup, and Linux AppImage release automation.
- Separate packaged worker executable so stdout remains valid JSON in windowed Windows builds.
- On-demand HAT-S, HAT, and HAT-L model catalog with pinned SHA-256 verification.
- Bring-your-own Spandrel checkpoint support.
- Worker-side MPS, CUDA, CPU, RAM, and VRAM detection.
- AMD ROCm and Intel XPU/iGPU discovery when exposed by the installed PyTorch build.
- Hard per-job allocator ceilings for MPS, CUDA/ROCm, and Intel XPU devices.
- DNG camera RAW selection and isolated LibRaw development with camera white balance and sRGB.
- Selectable final output scales up to the model's native factor.
- Hardware-constrained tile, halo, precision, and Safe Memory settings.
- Time ranges, locally calibrated throughput, projected memory/disk headroom, and live memory.
- Windows, macOS, and Linux GitHub Actions test matrix.

### Fixed

- Before/after preview zoom and pan preserve image aspect ratio and stay synchronized on both
  sides; the comparison divider is hidden while a render is incomplete.
- The primary processing action is centered within the inspector pane at desktop widths.
- The macOS bundle is a foreground application even though it contains a console worker, so the
  visible LocalSR window can reliably become active and receive pointer/keyboard input.
- Immediate cancellation is retained while a just-submitted job is still waiting to become active,
  instead of being lost in the worker startup race.
- The macOS image picker now uses valid AppleScript multiple-selection syntax, and stale missing
  custom-model settings fall back to a compatible catalog model instead of exposing a false 1×
  upscale choice.
- macOS file and folder panels are hosted by foreground Finder instead of a background-only
  `osascript` process, preventing the visible picker from becoming click-through above LocalSR.
- Packaged macOS builds use a small bundled AppKit picker helper, avoiding Automation permission
  prompts while keeping file, folder, model, DNG, and multi-select dialogs foreground-interactive.
- Download progress fills are anchored to the left edge, and cancelling an automatic-recipe
  download now clears the pending recipe/status as well as its partial checkpoint.
- The left workflow pane remains stationary before task selection, and custom Slint controls expose
  button, checkbox, selection, and combo semantics to desktop accessibility and GUI automation.
- Trackpad scrolling over a closed settings selector now scrolls the workflow pane instead of
  silently cycling the selector's value; open selector popups retain their own scrolling.
- Removed the redundant in-app branding/hardware bar, moved every job-changing control—including
  Advanced—to the left workflow pane, and made the right pane a read-only job/resource inspector.
- Quick and Best now appear only after task selection and start processing automatically after
  resolving settings and, when needed, downloading and verifying the selected model.
- Slint and Qt/Winit event loops no longer coexist in one macOS process; native dialogs and legacy
  tests run out of process.
- Slint model, format, device, tile, halo, and precision selectors use two-way state bindings.
- Idle hardware and memory pressure refresh automatically every five seconds.
- Model download progress now disappears when the background download thread finishes.
- Live ETA is based on completed tiles and smoothed tile duration instead of model-loading time.
- Packaged QML dependencies exclude unused WebEngine and 3D modules.
- Process-lifecycle tests no longer depend on Unix-only `ps`, `pgrep`, or signal-zero behavior.
- Apple unified-memory pressure is advisory and no longer prevents a job from starting.
- Hardware details, progress state, and the disabled Cancel control remain readable in dark mode.
