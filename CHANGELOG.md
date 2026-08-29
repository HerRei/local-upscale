# Changelog

All notable changes will be documented here. The project follows semantic versioning once the first
stable release is published.

## [Unreleased]

## [0.0.9-alpha] - 2026-08-29

### Security

- Blocked unverified pickle and TorchScript checkpoints by default. Curated `.pth` models must
  match their catalog SHA-256 before Spandrel sees them; custom `.pth`, `.pt`, and `.ckpt` files
  require an explicit trusted-code override, while `.safetensors` remains available normally.
- Replaced size-only installed-model checks with cached SHA-256 verification for image and SeedVR2
  model files, including same-size tamper detection.
- Replaced the plaintext LAN bearer token used for release-artifact transfer with timestamped,
  nonce-bound HMAC authentication and replay rejection; rotated the upload secret.
- Pinned `actions/checkout` to its verified full commit, disabled credential persistence, and
  limited release write permission to the publishing job. Future Apple signing/notarization
  credentials are exposed only to the dedicated signing step, not dependency installation or the
  rest of the macOS build.
- Made the Windows portable installer require and verify the archive's release checksum before it
  removes or installs anything; removed shell execution from the Python source installer.
- Updated maintained Linux and Windows CPU/CUDA/XPU/ROCm builds to PyTorch 2.13.0 and modern
  platforms' Labs dependency to Diffusers 0.38.x.

### Changed

- Synchronized app, bundle, installer, documentation, tag, and release metadata at 0.0.9-alpha.
- Expanded the beta gate register to state the legacy Windows DirectML runtime and unavailable
  repository security controls explicitly, without deciding their product-policy outcomes.

## [0.0.8-alpha] - 2026-08-29

### Added

- A version-synchronized, CI-validated beta-readiness register that keeps credential, licensing,
  platform, physical-test, public-access, and Labs gates explicitly open until they are resolved.
- A reusable acceptance-record template for artifact digest, hardware, OS/driver, diagnostics, real
  media, resource-pressure, and thermal results.
- The beta-readiness snapshot as a release asset and a reference in the release index.

### Changed

- Bounded Windows CI scratch removal and moved it to a short-path `rd` subprocess so multi-gigabyte
  CUDA cleanup cannot occupy the only Windows runner indefinitely.
- Kept clean-shutdown acceptance bounded while allowing Windows time to unload PyTorch DLLs on the
  memory-constrained test VM.
- Synchronized app, bundle, installer, documentation, tag, and release metadata at 0.0.8-alpha.

## [0.0.7-alpha] - 2026-08-28

### Added

- A release-time empty-cache acceptance test downloads and runs the real Quick and Best models.
- Privacy-filtered Copy Diagnostics support and the official Slint attribution widget.
- Conditional Developer ID signing, notarization, stapling, and Gatekeeper validation for macOS
  builds when production credentials are configured.
- Explicit release limitations, third-party notices, and automated/manual acceptance checklists.

### Changed

- Replaced four dead catalog downloads with verified SPAN NomosUni, RealPLKSR NomosWebPhoto,
  RealPLKSR HFA2k anime, and pinned NAFNet GoPro checkpoints.
- Labeled all video/SeedVR2 features Labs / Experimental and fixed sequential multi-video batches.
- Included SeedVR2 YAML, embeddings, NOTICE, and vendor license in wheels and source distributions.
- Synchronized app, bundle, installer, documentation, tag, and release metadata at 0.0.7-alpha.
- macOS archives now contain the actual `LocalSR.app`; Homebrew is no longer advertised.

### Fixed

- Prerelease tags now pass `--prerelease` to GitHub release creation.
- Full release verification now rejects duplicate archive digests in addition to validating the
  expected PE, ELF, and Mach-O content.

### Added

- SeedVR2-3B temporal video upscaling (experimental): the Upscale Video task now offers
  "SeedVR2-3B — Temporal" and its FP8 variant — one-step diffusion video restoration with
  true temporal consistency inside each clip window, running fully locally on Apple Silicon
  (MPS) and NVIDIA GPUs. Model bundles (3.9–7.3 GB) download on demand with revision-pinned
  URLs and SHA-256 verification, so the application package stays small. The engine streams
  clips with context-frame conditioning, supports cancellation between pipeline batches, and
  the vendored Apache-2.0 implementation (ByteDance SeedVR2 via the numz ComfyUI project,
  pinned commit, NOTICE and license included) is kept byte-faithful to upstream.
  Frame-by-frame remains the default and the fast path on Macs.
- Video outputs now keep their audio: the source's audio stream is remuxed untranscoded
  into the upscaled MP4 (best-effort — incompatible or absent audio yields a silent video,
  and trimmed jobs skip audio to avoid desync).
- Video upscaling (beta): MP4, MOV, M4V, MKV, WebM, and AVI clips can be added to the queue
  alongside images. The Upscale Video task runs the selected restoration model frame by
  frame with optional temporal-median de-flicker, encodes to MP4 with atomic writes, and
  reports per-frame progress with a live ETA. Clips show their first frame on the canvas
  and duration/frame-count in the queue; batch mode processes the files matching the active
  task. Face-aware companion pairing applies to video too. Temporal (clip-based) models are
  the next phase — see docs/video-upscaling-plan.md.

### Changed

- Face-aware restoration no longer has a manual Detect Faces button — it never influenced
  jobs. It is now automatic: when the selected model has an installed face-specialized
  companion (`pair_with` in the catalog), jobs carry it and the worker detects faces and
  composites the face model over face regions on its own.
- Removed the abandoned Qt Quick/QML interface (`ui/qml_app.py`, `ui/controller.py`, the
  `ui/qml/` tree) — dead outside one offscreen test. The preset and preview tests it hosted
  moved to `tests/test_presets_preview.py`. The Qt Widgets `--legacy` window remains for now
  because several protocol/integration test suites still drive core behavior through it.

## [0.0.3-alpha] - 2026-08-21

### Added

- Custom recipes: "Save Current Settings as Recipe" snapshots the task, model, scale,
  format, quality, and hardware configuration under a chosen name. Saved recipes appear in
  the Recipes section, apply with one click (settings are re-clamped through the same
  validation as a normal restore), delete via a hover disc, and persist in settings.json
  across sessions.
- Info popovers: an ⓘ next to "Restoration model" lists every compatible model and what it
  is for, and an ⓘ next to "Safe memory mode" explains what the mode trades for safety.
- GPU-detection test coverage now spans the full vendor matrix: NVIDIA CUDA (multi-GPU,
  fp16 gated by compute capability), AMD ROCm, Intel XPU (integrated and discrete), and
  Apple Metal, with the CPU fallback always enumerated last.
- Added `HAT-S ×4 Face — Restoration` model (`hat_s_x4_face`) to the curated catalog,
  fine-tuned and blended ($\alpha=0.10$) for enhanced portrait restoration and clean
  fidelity retention, with face-model pairing between `hat_s_x4` and `hat_s_x4_face`
  for face-aware pipelines (ADR 0004).

### Changed

- Rebalanced the entire palette on an engineered OKLCH ramp: one hue (266°), chroma
  proportional to lightness, and a uniform lightness staircase. Chrome bars gained a full
  step over panels, recessed fields now genuinely sink below the base plane, the accent
  family was re-derived from a single anchor with computed white-text contrast on every
  gradient stop, loudness was re-ranked (quieter catch-lights and selection borders,
  legible disabled text), and the memory gauge's healthy state became green so blue stays
  the exclusive color of interaction.
- Safe memory mode is no longer the default. It still switches itself on automatically
  when free memory is very low.

- Redesigned the entire interface as "Machined Graphite", a bespoke dark design language for
  the Slint workspace: four tonal planes (chrome, panels, stage, canvas well) joined by
  shadow-and-catch-light seams, a shared token system (`Theme` global) for color, type,
  radius, spacing, and motion, and accent controls built from one anodized gradient material.
- The preview canvas is now a carved well with a rim reveal and edge vignette; its zoom,
  fit, and compare controls moved into a floating glass HUD that fades while the image is
  being dragged, and the Original/Preview labels became floating chips in the top corners —
  reclaiming the old 50px bottom control bar for the image.
- The toolbar became a 46px command strip with a centered, recessed page switcher in the
  medium and compact layouts; the inspector gained uppercase micro-cap section headers, a
  uniform 4px label-to-control rhythm, a recessed "receipt card" for the run summary, and
  content-sized captions and warning callouts that grow with their text.
- Media queue rows were rebuilt: 44px thumbnails, an animated selection spine, and a
  hover-revealed remove disc in place of the "Remove" text label; segmented controls,
  combo boxes, checkboxes, and buttons were all restyled with full hover/pressed/disabled/
  focus states and 120–180ms motion. All existing callbacks, shortcuts, accessibility
  labels, and the responsive three-mode layout are preserved unchanged.
- Cleaned up the toolbar to a single macOS-style command strip: the redundant Advanced and Compare
  toolbar buttons are gone. Advanced settings stay in the Enhance inspector's disclosure row and
  Compare stays with the preview's own control bar.
- Detect Faces now toggles: once faces are found the same button becomes "Clear Faces (n)" so
  face-aware restoration can be deselected again.
- The Output scale control only appears after a task is chosen instead of rendering an empty box.

### Fixed

- Removing (or replacing) the image that produced the last finished result now retires the
  Open Result and Show in Folder buttons instead of leaving them pointing at an output
  whose source is gone.
- Fixed overlapping text in the empty preview state (the plus icon rendered on top of the caption)
  and several controls that were centered instead of anchored: the queue selection bar, section
  titles, value-line labels, and the top separators of the preview, action, and status bars.
- The "Advanced settings" disclosure summary no longer truncates to "Model, output, har…".

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
