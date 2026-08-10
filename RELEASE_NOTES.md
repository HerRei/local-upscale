# LocalSR 0.0.1 Alpha

This is the first public testing release of LocalSR: a focused desktop application for running
image upscaling and denoising locally. Processing stays on the computer, model files are downloaded
only when selected, and compatible Spandrel checkpoints can also be supplied manually.

## Highlights

- A compact native Slint interface for macOS, Windows, and Linux.
- Single-image and sequential batch workflows.
- Upscale, Denoise, and Upscale + Denoise tasks with Quick and Best automatic recipes.
- Before/after comparison with synchronized zoom and pan, progressive tile previews, progress,
  cancellation, ETA, and resource telemetry.
- On-demand, hash-verified model downloads instead of bundling large checkpoints in the installer.
- HAT, HAT-L, HAT-S, SPAN, RealPLKSR, SCUNet, and NAFNet catalog options, plus compatible custom
  Spandrel models.
- DNG camera RAW input through isolated LibRaw development.
- A separate inference worker process, tiled inference, automatic tile-size recovery, and atomic
  output saving.

## Downloads

- **macOS:** `LocalSR-macOS-arm64.dmg` for Apple Silicon on macOS 12 or newer.
- **Windows:** `LocalSR-Windows-x86_64-Setup.exe` for 64-bit Windows.
- **Linux:** `LocalSR-Linux-x86_64.AppImage` or the portable `.tar.gz` archive.
- **Verification:** `SHA256SUMS.txt` contains a checksum for every distributable.

The packaged macOS build supports Apple Metal (MPS). The first Windows and Linux packages use the
CPU PyTorch runtime for broad compatibility; NVIDIA CUDA, AMD ROCm, and Intel XPU acceleration can
be used from a suitable source installation and remain an area for future installer work.

## Alpha notes

This release is intended for testing. Keep the original image and verify important output. Model
support varies by checkpoint, very large scales can require substantial memory and disk space, and
the time/memory estimates become more accurate after local calibration.

The current artifacts are not backed by paid Apple Developer ID or Windows Authenticode
certificates. macOS Gatekeeper and Windows SmartScreen may therefore show an unidentified-developer
warning. Each final distributable is nevertheless built and smoke-tested on its native GitHub
Actions runner, including mounting the DMG, installing/uninstalling the Windows setup package, and
launching both Linux formats.

Please report reproducible problems through the repository's issue tracker with the operating
system, hardware/backend, selected model, input dimensions, scale, tile size, and the displayed
error message. Do not attach private source images unless you intend to share them.
