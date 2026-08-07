# Changelog

All notable changes will be documented here. The project follows semantic versioning once the first
stable release is published.

## [Unreleased]

### Added

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

- Model download progress now disappears when the background download thread finishes.
- Live ETA is based on completed tiles and smoothed tile duration instead of model-loading time.
- Packaged QML dependencies exclude unused WebEngine and 3D modules.
- Process-lifecycle tests no longer depend on Unix-only `ps`, `pgrep`, or signal-zero behavior.
- Apple unified-memory pressure is advisory and no longer prevents a job from starting.
- Hardware details, progress state, and the disabled Cancel control remain readable in dark mode.
