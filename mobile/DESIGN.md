# LocalSR mobile: design

A photo upscaler for iPhone and Android, built with Skip Lite. Two bundled 4x models run on the NPU when it passes a device check, or on the GPU or CPU. The app does not use the network.

## Modules

- `Sources/LocalSRCore`: shared logic for tile planning and stitching, pixel math, device checks, size policy, formatting and the `SREngine` protocol.
- `Sources/LocalSR`: SwiftUI screens, batch state and services. Apple code uses `#if !SKIP`; Android code uses `#if SKIP` and hand-written Kotlin in `Skip/`.
- `tools/export_models.py`: converts checkpoints and checks model output against PyTorch FP32.

### iOS

- Core ML inference; ImageIO decode and HEIC encode.
- Photos picker input and photo library save.

### Android

- LiteRT inference; ImageDecoder decode and JPEG encode.
- Photo picker input and MediaStore save.

## Models

| Key | Name in UI | Source checkpoint | Architecture |
|---|---|---|---|
| `best` | Best | `localsr_photo_pulse48_v1_x4.safetensors` | PULSE-48 v1 |
| `fast` | Fast | `localsr_photo_span28_v1_x4.safetensors` | SPAN-28 v1 |

Both are 4x models with a 384 x 384 input window and a 1536 x 1536 output.

- iOS: compiled Core ML ML Program, FP16 weights and FP16 input and output tensors. The Darwin Xcode target bundles the models, probe and notice.
- Android: LiteRT models with FP16 weights and NHWC float32 input and output. The Gradle task `stageLocalsrModels` stages the models, probe and notice as assets.
- The About sheet includes the PULSE notice.

## Tiling

Each 384 px window has a 16 px halo and a 352 px core. Edge windows shift inside the image where possible; only the core is copied to the output. Images smaller than a window are edge-padded.

## Device check

The check runs per model and caches a verdict by device model, OS version, app build and model key: UserDefaults on iOS and SharedPreferences on Android. A bundled probe provides the input; CPU output is the reference.

1. On iOS, `MLComputePlan` checks that every operation with device usage prefers the Neural Engine before trying the NPU.
2. An NPU load taking over 15 seconds causes a session-only fallback and a later retry.
3. Probe outputs must have PSNR of at least 45 dB against CPU, at least 26 dB against the input after 4x box downsampling, no non-finite values and no constant output.
4. A passing NPU is chosen unless a passing GPU is at least 1.3 times faster. Otherwise a passing GPU or CPU is chosen. If all units fail, there is no verdict and upscaling is disabled.

On Android, a selected accelerator must also be faster than the CPU; the GPU probe has a 60-second timeout. The simulator has no NPU. During a job, a GPU tile failure falls back to CPU and retries the tile; Android does the same for NPU tile failures.

## Limits

- Output is capped at 48 MP; inputs above 3 MP are prescaled. The output RGBA8 buffer is at most 192 MB.
- iOS 26 uses a continued processing task for user-started batches when available. GPU work falls back to CPU in the background.
- Android uses a batch-level foreground service with a progress notification. NPU use requires a vendor runtime already on the device.
- Android writes sRGB JPEG without an HDR gain map; the picker may strip GPS. iOS carries supported gain maps and available metadata into HEIC output.
- Completed results are recorded in `results.json` in the cache and restored when their files exist. Pending batches are not restored after process death.

## UI

1. Home: choose photos and view recent results.
2. Progress: per-photo progress, overall ETA and cancel.
3. Result: before/after comparison, model switch, save and share. The comparison uses a preview no larger than 4096 px on its longest side.

The About sheet shows model notices and device-check information.

## Checks

- Swift and Android tests cover shared logic and platform behavior.
- `tools/export_models.py --check` compares model output with PyTorch FP32; CPU paths must reach 55 dB.
- iOS simulator UI tests and Android emulator tests exercise the app flow. Real-device NPU and GPU performance has not yet been measured.
