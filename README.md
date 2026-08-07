# LocalSR

[![CI](https://github.com/HerRei/local-upscale/actions/workflows/ci.yml/badge.svg)](https://github.com/HerRei/local-upscale/actions/workflows/ci.yml)

LocalSR is a cross-platform desktop application for running local image super-resolution
models on your own hardware. Apple Silicon (MPS) is prioritized, with NVIDIA CUDA, AMD ROCm,
Intel XPU, and CPU support when the installed PyTorch build exposes those backends.

## What it does

- Runs open-source PyTorch upscaling models (HAT, ESRGAN, SwinIR, etc.) via Spandrel.
- Lets the user choose the final enlargement up to the model's native scale (for example, 2×,
  3×, or native 4× from a HAT 4× model).
- Performs robust tiled inference to keep memory usage low and prevent system crashes on large images.
- Safely cancels jobs cooperatively without locking up your system.
- Correctly handles color profiles (ICC), preserving image colors faithfully.
- Converts everything safely to sRGB during processing and re-embeds the profile on output.
- Develops `.dng` camera RAW files through LibRaw using camera white balance and sRGB output.
- Offers three HAT sizes as optional, on-demand downloads while still accepting your own
  Spandrel-compatible checkpoints.
- Detects available Apple, NVIDIA, AMD, and Intel devices and memory in the isolated worker, then
  limits tile and precision choices to settings supported by the selected hardware and model.
- Provides conservative first-run estimates for time, device memory, RAM, disk, and tile count.
- Explains memory, disk, and device failures with practical recovery steps.

## What it intentionally does NOT do

- No generative AI (Stable Diffusion, outpainting, generative fill).
- No bundled model weights. Curated checkpoints are downloaded only when selected.
- No batch processing or video (first version focuses on single-image stability).
- No cloud processing or analytics.

## Current Platform Support

- macOS with Apple Silicon MPS or CPU
- Windows with NVIDIA CUDA, supported Intel XPU GPUs/iGPUs, or CPU
- Linux with NVIDIA CUDA, AMD ROCm, supported Intel XPU GPUs/iGPUs, or CPU

The source application and automated tests are cross-platform. Signed native installers are not yet
part of the project.

AMD ROCm intentionally uses `cuda:N` device identifiers internally because PyTorch reuses its CUDA
API for HIP; the GUI labels these devices as ROCm. Intel GPUs use `xpu:N` and are shown only when
`torch.xpu.is_available()` succeeds. This includes supported Intel client and integrated GPUs.
Vendor drivers and a matching PyTorch build are still required.

Upscayl reaches a wider set of consumer GPUs through NCNN/Vulkan. LocalSR does not currently use
that backend because NCNN models are not interchangeable with arbitrary Spandrel/PyTorch
checkpoints. DirectML is also not exposed because this version requires an enforceable per-process
GPU-memory ceiling; unsupported GPUs fall back to CPU instead of being advertised optimistically.

## Models

The model menu contains three HAT ×4 choices:

- **HAT-S — Fast**: the lowest memory and compute cost.
- **HAT — Balanced**: the normal ImageNet-pretrained model.
- **HAT-L — Maximum**: the largest and most demanding variant.

Each download is pinned to a specific remote revision and verified against an embedded SHA-256
digest before the temporary file is atomically installed. A failed or cancelled download removes
its partial file. Models live in the platform application-data directory and are not included in
the LocalSR package. The GUI links to the [official HAT project](https://github.com/XPixelGroup/HAT)
and shows its declared Apache-2.0 license.

Choose **Use my own checkpoint…** to load any local `.pth`, `.pt`, or `.safetensors` model that
Spandrel supports.

## Launching LocalSR

1. Create a virtual environment and install dependencies:
   ```bash
   python3.11 -m venv .venv
   source .venv/bin/activate
   pip install -e ".[dev]"
   ```
2. Run the application:
   ```bash
   localsr
   ```
   (Alternatively, `python -m localsr`)

## Supported Formats

- Input: `.jpg`, `.jpeg`, `.png`, `.tif`, `.tiff`, `.webp`, `.dng`
- Output: `.jpg`, `.png`, `.tif`

DNG input is developed from the original RAW data before upscaling. LocalSR does not overwrite the
source DNG; the result is written as the selected standard output format.

## Output Scale

After a model is inspected, the Output section lists every integer enlargement from 2× through the
model's native scale. The neural model always performs its native restoration pass; selecting a
smaller final factor applies one Lanczos downsample before the atomic save. LocalSR does not claim
that conventional resizing beyond a model's native scale creates additional model detail.

## Apple MPS Safe Mode

PyTorch on MPS can easily exhaust unified memory and crash macOS. LocalSR combats this by:
1. Spawning a completely isolated Python worker for inference.
2. Forcing strict `PYTORCH_MPS_HIGH_WATERMARK_RATIO` environment variables before Torch loads.
3. Quantizing output progressively using disk-backed memory (`numpy.memmap`) so the huge final 32-bit float output tensor never lives in RAM.

## Advanced Settings Explained

- **Device**: Populated by the inference worker after it checks MPS, every CUDA GPU, and CPU.
- **Tile Size**: Only sizes recommended for the currently available memory are offered. Smaller
  sizes use less memory but run more tiles.
- **Halo (Overlap)**: Tiling creates visible seams. LocalSR processes surrounding pixels for
  context, then crops that region away. Halo choices are constrained by the tile size.
- **Precision**: FP16 appears only when both the selected device and inspected model support it.
- **Safe Memory Mode**: Automatically required when less than 3 GB of device memory is available.

Every GPU job has a runtime allocator ceiling. CUDA, ROCm, and Intel XPU jobs are capped against
the memory that is free when the job starts and never above 90% of total device memory. Metal uses
PyTorch's hard MPS high-watermark limit. Apple unified-memory pressure is advisory rather than a
start blocker because macOS can compress and swap inactive memory; disk-space and discrete-GPU
VRAM failures remain blocking.

Before a first run, LocalSR displays a broad time range because hardware generation, thermal state,
model architecture, tile overlap, and storage speed cannot be inferred reliably. After a successful
run it stores a rolling local calibration for that model/device pair and narrows the range. Once
inference begins, the live ETA uses smoothed completed-tile timings.

The resource panel shows currently available RAM, VRAM or Apple unified memory and disk space,
together with conservative projected headroom. Live remaining memory is sampled during inference.
These figures are safety guidance rather than allocation guarantees.

## Tests

Run tests using:
```bash
python -m pytest -q
ruff check src tests smoke_test_gui.py
ruff format --check src tests smoke_test_gui.py
```

GitHub Actions runs the suite independently on Windows, macOS, and Linux and builds a wheel and
source distribution on every push and pull request.

The automated suite verifies real Spandrel ESRGAN/HAT loading and inference using lightweight
generated checkpoints. It also tests the curated catalog, checksum/partial-file behavior, resource
estimation, and hardware-aware GUI restrictions. Those random-weight fixtures validate application
mechanics, not visual restoration quality. Use a trusted pretrained checkpoint for meaningful
output.

## Security Risks

Do not load custom `.pth` or `.pt` files from untrusted sources. Pickle-based PyTorch checkpoints
can execute arbitrary code. Prefer `.safetensors` for custom models when available.

## Licensing

LocalSR is distributed under the [MIT License](LICENSE). Spandrel, PyTorch, and individual model
checkpoints retain their own licenses.
