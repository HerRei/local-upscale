# Changelog

All notable changes will be documented here. The project follows semantic versioning once the first
stable release is published.

## [Unreleased]

### Added

- On-demand HAT-S, HAT, and HAT-L model catalog with pinned SHA-256 verification.
- Bring-your-own Spandrel checkpoint support.
- Worker-side MPS, CUDA, CPU, RAM, and VRAM detection.
- Hardware-constrained tile, halo, precision, and Safe Memory settings.
- Time ranges, locally calibrated throughput, projected memory/disk headroom, and live memory.
- Windows, macOS, and Linux GitHub Actions test matrix.

### Fixed

- Model download progress now disappears when the background download thread finishes.
- Live ETA is based on completed tiles and smoothed tile duration instead of model-loading time.
- Process-lifecycle tests no longer depend on Unix-only `ps`, `pgrep`, or signal-zero behavior.
