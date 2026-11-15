# LocalSR mobile: design

A small photo upscaler for iPhone (main target) and Android. One Swift codebase built with
[Skip Lite](https://skip.dev/docs/modes/) (Swift transpiled to Kotlin/Compose for Android).
Two bundled 4x models, run on the NPU when the device passes a check, else GPU, else CPU.
No network, no accounts, no downloads.

## Modules

- `Sources/LocalSRCore` (shared, transpiled): pure logic, no platform imports.
  - `TilePlanner`: tile grid with halos and fixed-size windows (see "Tiling").
  - `TileStitcher`: copies each tile's core region into the output RGBA8 buffer.
  - `PixelMath`: RGBA8 <-> planar float conversion helpers, PSNR, 4x box downsample.
  - `DeviceCheck`: decides NPU / GPU / CPU from measured probe results (see "Device check").
  - `SizePolicy`: input cap so the output stays <= 48 MP (prescale big inputs).
  - `JobQueue` / view models: batch state, progress, ETA.
  - `SREngine` protocol: `load(model:unit:)`, `run(tile:) -> output`, implemented per platform.
- `Sources/LocalSR` (app UI, transpiled): SwiftUI screens. Platform code lives here behind
  `#if SKIP` (Kotlin/Android APIs) and `#if !SKIP` (Apple APIs).
  - iOS: `CoreMLEngine` (Core ML, `MLComputePlan`), `ImageIOCodec` (decode, HEIC encode,
    ISO gain map), `PhotoLibrary` (PhotosPicker input, `PHAssetCreationRequest` save from file URL).
  - Android: `LiteRTEngine` (LiteRT `CompiledModel`: NPU, GPU, CPU), `AndroidCodec`
    (`ImageDecoder`, JPEG encode, EXIF copy), `MediaStoreLibrary` (photo picker, MediaStore save).
- `tools/export_models.py`: converts the PyTorch checkpoints to the bundled model files and
  checks them numerically against PyTorch FP32.

## Models

| Key | Name in UI | Source checkpoint | Arch |
|---|---|---|---|
| `best` | Best | `localsr_photo_pulse48_v1_x4.safetensors` | PULSE-48 (needs `localsr_train.spandrel_pulse.register()`) |
| `fast` | Fast | `localsr_photo_span28_v1_x4.safetensors` | SPAN-28 |

Both are 4x and fully convolutional. Fixed input window 384x384, output 1536x1536.

- iOS: Core ML ML Program, FP16 weights, **FP16 input and output tensors** (`image` [1,3,384,384]
  float16 -> `out` [1,3,1536,1536] float16), `minimum_deployment_target=iOS17`, branch fusion via
  `prepare_export_model` before tracing. Shipped precompiled as `.mlmodelc`.
  Measured on M1 Pro: PULSE 18.9 ms/tile on the Neural Engine, 100 % of ops on the ANE.
- Android: LiteRT `.tflite`, FP16 weights, NHWC float32 I/O ([1,384,384,3] -> [1,1536,1536,3]).
- Never ship W8 linear-quantised weights: they produce garbage on the Apple Neural Engine.
- PULSE-48 v1 is fine-tuned from Phips/PULSE (MIT). Its notice must appear in the app's About screen.

## Tiling

Same idea as the desktop (`src/localsr/core/tiling.py`, `_fixed_window` in `inference.py`):
halo 16 px, so each window is 384 px and its core is 352 px. Edge windows are shifted inside the
image (real pixels instead of padding); only the core region (in output coordinates) is copied.
Images smaller than 384 px on a side are edge-padded (replicate) to 384.

## Device check

Runs once per model and compute unit, cached in UserDefaults under
`(device model, OS version, app build, model key)`. No reference outputs are shipped.

1. Placement (iOS only): `MLComputePlan` with `.cpuAndNeuralEngine`; NPU candidate only if every
   op with a device usage prefers the Neural Engine.
2. Load watchdog: if the first NPU load takes > 15 s, use the GPU for this session and retry later.
3. Probe tile (bundled 384x384 PNG): run it on CPU (reference), NPU and GPU.
   - A unit passes if PSNR vs the CPU output >= 45 dB, no NaN/Inf, output not constant,
     and the 4x box-downsampled output vs the input >= 26 dB (catches a wrong CPU path too).
4. Choose the NPU if it passes, unless the GPU also passes and is >= 1.3x faster.
   Else GPU if it passes, else CPU.

The simulator has no NPU. Its Core ML GPU path returned wrong pixels in one probe and correct ones in another; the check handles both (GPU if it passes, else CPU).

## Limits

- Output capped at 48 MP: 4x is 16x the pixels, so inputs above 3 MP are prescaled first (a 12 MP phone photo goes in at 3 MP and comes out at 48 MP).
- One output RGBA8 buffer per job (<= 192 MB), released before the next photo.
- Background: iOS 26 `BGContinuedProcessingTask` for batches started by the user, NPU/CPU only
  (no background GPU on iPhone). Android: foreground service with a progress notification.

## UI

Three screens, no settings screen.

1. Home: "Choose photos" (system photo picker, no permission prompt), recent results grid.
2. Progress: thumbnails with per-photo progress, overall ETA, cancel.
3. Result: before/after slider with pinch zoom on a <= 4096 px preview; Best/Fast switch
   (re-runs); "Save to Photos" (keeps EXIF date/location; iOS keeps the HDR gain map); Share.
About sheet: model notices, licences, which unit the device check chose.

## Checks

- `swift test` (Swift) and Skip's transpiled Kotlin tests for `LocalSRCore`.
- `tools/export_models.py --check`: PSNR >= 55 dB vs PyTorch FP32 for both formats.
- iOS simulator: build, UI test flow with photos added by `simctl addmedia`.
- Android emulator `localsr_api36`: build, install, run the same flow.
