# LocalSR

[![CI](https://github.com/HerRei/local-upscale/actions/workflows/ci.yml/badge.svg)](https://github.com/HerRei/local-upscale/actions/workflows/ci.yml)

LocalSR is a cross-platform desktop application for running local image super-resolution
models on your own hardware. Apple Silicon (MPS) is prioritized, with NVIDIA CUDA, AMD ROCm,
Intel XPU, and CPU support when the installed PyTorch build exposes those backends.

## What it does

- Runs open-source PyTorch upscaling models (HAT, ESRGAN, SwinIR, etc.) via Spandrel.
- Reveals **Quick** and **Best** only after the user chooses Upscale, Denoise, or both. Either recipe
  chooses a compatible model, accelerator, precision, tile size, and overlap, then starts immediately;
  when its model is missing, LocalSR downloads and verifies it before starting automatically.
- Lets the user choose the final enlargement up to the model's native scale (for example, 2×,
  3×, or native 4× from a HAT 4× model).
- Performs robust tiled inference to keep memory usage low and prevent system crashes on large images.
- Safely cancels jobs cooperatively without locking up your system.
- Correctly handles color profiles (ICC), preserving image colors faithfully.
- Converts everything safely to sRGB during processing and re-embeds the profile on output.
- Develops `.dng` camera RAW files through LibRaw using camera white balance and sRGB output.
- Offers slim and large HAT upscale models plus RealPLKSR and NAFNet denoisers as optional,
  on-demand downloads while still accepting your own Spandrel-compatible checkpoints.
- Detects available Apple, NVIDIA, AMD, and Intel devices and memory in the isolated worker, then
  limits tile and precision choices to settings supported by the selected hardware and model.
- Provides conservative first-run estimates for time, device memory, RAM, disk, and tile count.
- Shows the active tile and progressively composites finished tiles into a bounded live preview.
- Shows live macOS memory pressure, compression, swap, and MPS allocator values during work.
- Explains memory, disk, and device failures with practical recovery steps.

## What it intentionally does NOT do

- No generative AI (Stable Diffusion, outpainting, generative fill).
- No bundled model weights. Curated checkpoints are downloaded only when selected.
- No video processing. Batch mode deliberately runs images sequentially so only one inference job
  occupies the accelerator at a time.
- No cloud processing or analytics.

## Current Platform Support

The 0.0.2 alpha packages support Apple Silicon MPS or CPU on macOS and CPU processing on Windows
and Linux. Source installations can additionally use Windows NVIDIA CUDA or supported Intel XPU
GPUs/iGPUs, and Linux NVIDIA CUDA, AMD ROCm, or supported Intel XPU GPUs/iGPUs when their installed
PyTorch build exposes that backend.

The release workflow builds a macOS Apple Silicon DMG, Windows x86-64 Setup executable, and Linux
x86-64 AppImage plus portable archive. Builds are unsigned unless the repository signing secrets
documented in [the release guide](docs/releasing.md) are configured.

AMD ROCm intentionally uses `cuda:N` device identifiers internally because PyTorch reuses its CUDA
API for HIP; the GUI labels these devices as ROCm. Intel GPUs use `xpu:N` and are shown only when
`torch.xpu.is_available()` succeeds. This includes supported Intel client and integrated GPUs.
Vendor drivers and a matching PyTorch build are still required.

Upscayl reaches a wider set of consumer GPUs through NCNN/Vulkan. LocalSR does not currently use
that backend because NCNN models are not interchangeable with arbitrary Spandrel/PyTorch
checkpoints. DirectML is also not exposed because this version requires an enforceable per-process
GPU-memory ceiling; unsupported GPUs fall back to CPU instead of being advertised optimistically.

## Native Desktop Integrations

LocalSR integrates directly into your operating system's desktop environment:

### 🍏 macOS Integrations

* **Finder Quick Actions & Services**:
  * Right-click any image or video in Finder.
  * Hover over **`Quick Actions >`** (or **`Services >`**) and select **`Upscale with LocalSR`**.
  * An interactive recipe selector popup will appear asking which preset you'd like to use:
    * **⚡ Quick Preset** (Fast)
    * **✨ Best Quality Preset**
    * Any of your **Custom Saved Recipes**
  * LocalSR opens automatically with your selected files and immediately starts processing.
* **Native Cocoa Top Menu Bar**:
  * Standard macOS system menu bar (` LocalSR`, `File`, `Presets`, `View`, `Window`, `Help`).
  * Full keyboard shortcut support:
    * `⌘O` — Add Media (Native open panel)
    * `⇧⌘O` — Add Folder
    * `⌘E` — Open Output Folder in Finder
    * `⌘R` — Start Upscaling
    * `⌘.` — Cancel Job
    * `⌘1` — ⚡ Quick Preset
    * `⌘2` — ✨ Best Quality Preset
    * `⌘3...` — Dynamic Custom Recipes
* **Dock Drag-and-Drop**: Drag any images, folders, or video files straight onto the `LocalSR` Dock icon to queue them instantly.
* **Native System Notifications**: Delivers system banner alerts with audio when single jobs, batches, or video jobs complete.

### 🪟 Windows Integrations

* **Explorer Context Menu**: Right-click on supported image or video files in Windows Explorer to trigger recipe selection and immediate processing.
* **WinRT Toast Notifications**: Delivers rich Windows Action Center toast notifications with direct action buttons ("Open Result", "Reveal in Explorer").
* **Installer Registration**: Inno Setup installer automatically configures `HKCU\Software\Classes` shell verbs and file type associations.

### 🐧 Linux Integrations

* **File Manager Actions**: Preconfigured context menu actions for GNOME Nautilus (`~/.local/share/nautilus/scripts`) and KDE Dolphin ServiceMenus (`kservices5/ServiceMenus`).
* **Interactive Recipe Dialogs**: Seamless recipe picker via native Zenity or KDialog.
* **FreeDesktop `.desktop`**: Compliant application manifest and MIME-type associations.
* **D-Bus Desktop Notifications**: Uses `/org/freedesktop/Notifications` protocol with `notify-send` fallback.

### 💻 Global CLI & Integration Commands

```bash
# Ingest and auto-start processing with a preset
localsr photo.png --preset quick --auto-start

# Ingest with a custom recipe
localsr video.mp4 --recipe "My 4K Preset" --auto-start

# Re-register or remove OS desktop integrations
localsr --install-integrations
localsr --uninstall-integrations
```

## Models and automatic modes

The model menu is a generic catalog rather than a HAT-only selector:

| Model | Intended use | Download | License |
|---|---|---:|---|
| HAT-S ×4 | High-quality laptop/default model | 81 MB | Apache-2.0 |
| HAT-S ×4 Face | Face-specialized restoration model | 40 MB | Apache-2.0 |
| HAT-L ×4 ImageNet | Maximum-quality, high-cost model | 166 MB | Apache-2.0 |
| RealPLKSR Denoise ×1 | Fast photographic denoising | 30 MB | CC-BY-4.0 |
| NAFNet SIDD Width64 ×1 | Maximum-fidelity real camera denoising | 464 MB | MIT |

The speed and quality labels are relative to this curated LocalSR catalog and to each model's
intended degradation; they are not claims of global state of the art across every restoration
benchmark or source image.

**Quick** prioritizes the fastest suitable catalog model and a safe accelerated FP16 configuration
when both the model and device support it. **Best** prioritizes the highest-fidelity compatible
checkpoint and FP32. For upscaling, Quick chooses HAT-S and Best chooses HAT-L. For denoising,
Quick chooses the lightweight RealPLKSR checkpoint and Best chooses the official NAFNet SIDD
Width64 checkpoint. These are conventional image-to-image restoration networks, not generative
synthesis.

Quick and Best are immediate commands, not configuration toggles. Their derived settings remain
visible and editable under Manual Configuration and Advanced for subsequent runs.

Each download is pinned to a specific remote revision and verified against an embedded SHA-256
digest before the temporary file is atomically installed. A failed or cancelled download removes
its partial file. Models live in the platform application-data directory and are never included in
the LocalSR installer. Every entry records its source, author, architecture, intended content, and
license. HAT comes from the [official HAT project](https://github.com/XPixelGroup/HAT), RealPLKSR
comes from [Philip Hofmann's model releases](https://github.com/Phhofm/models), and NAFNet comes
from the [official NAFNet project](https://github.com/megvii-research/NAFNet). The NAFNet download
points to a pinned checkpoint uploaded by the project's coauthor and is verified before
installation.

Choose **Use my own checkpoint…** to load any local `.pth`, `.pt`, or `.safetensors` model that
Spandrel supports.

## Launching LocalSR

1. Run the interactive installation script (requires Python 3.11):
   ```bash
   python3 install.py
   ```
   *The script will detect your GPU and ask you to select the correct PyTorch backend (CUDA, ROCm, XPU, or CPU).*

2. Run the application:
   ```bash
   # On Linux/macOS:
   .venv/bin/python3 -m localsr
   # On Windows:
   .venv\Scripts\python -m localsr
   ```

The Slint interface is the default. `localsr --legacy` remains temporarily available as an optional
rollback path for the previous QWidget interface; install `.[legacy]` if you need it.

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
together with conservative projected headroom. On MPS it additionally reports macOS pressure,
compressed memory, swap, tensor allocations, Metal driver allocations, and Metal's recommended
maximum. LocalSR refreshes idle pressure periodically and samples it during inference. These figures
are safety guidance rather than allocation guarantees.

The live canvas is intentionally bounded to 1600 pixels on its longest side. Finished output tiles
are JPEG-encoded at preview quality inside the worker and composited into this small display image;
the full-resolution output still goes directly to the disk-backed memmap and atomic writer. Preview
rendering therefore does not create a second full-size output in GUI memory.

## Interface development

The visible interface is written in Slint in `src/localsr/ui/slint/main.slint`, with reusable controls
in `components.slint`. The Slint extension for VS Code provides syntax support and a live visual
preview while editing these files. `slint_app.py` owns presentation state and speaks JSON lines to
the isolated worker through `slint_worker.py`; neither the Slint files nor the UI host imports Torch
or Spandrel.

The workspace follows an adaptive document-tool layout: Media and the batch queue lead, the image
canvas remains dominant, and Enhance contains the explicit Upscale/Denoise choice, immediate
Quick/Best commands, the manual Start action, and one disclosure for model, output, and hardware
settings. Wide windows show all three panes; medium and compact windows expose the same workflow
through toolbar pane navigation. Slint uses native logical-pixel scaling, and the operating-system
title bar remains the only branded header.

File selection uses the host operating system's own dialog service, so the normal application does
not carry a second GUI toolkit. PySide is an optional dependency only for the temporary `--legacy`
rollback path and development tests.

## Native packages

Install PyInstaller support and build the current platform package with:

```bash
python -m pip install -e ".[package]"
python packaging/build_icons.py
pyinstaller --clean --noconfirm packaging/localsr.spec
```

The spec emits a windowed `LocalSR` executable and a separate console `LocalSRWorker`, allowing
JSON-line IPC to keep working in Windows GUI packages. Release tags matching `v*` run the native
build on all three operating systems. Full commands, artifact names, signing secrets, and the manual
workflow procedure are in [docs/releasing.md](docs/releasing.md).

## Tests

Run tests using:
```bash
python -m pip install -e ".[dev]"
python -m pytest -q
ruff check src tests smoke_test_gui.py
ruff format --check src tests smoke_test_gui.py
python -m localsr.ui.slint_check
```

GitHub Actions runs the suite independently on Windows, macOS, and Linux, compiles the Slint files,
and builds a wheel and source distribution on every push and pull request. The separate native
release workflow smoke-tests each packaged GUI/worker pair before publishing installers.

The automated suite verifies real Spandrel ESRGAN/HAT loading and inference using lightweight
generated checkpoints. It also tests the curated catalog, checksum/partial-file behavior, resource
estimation, and hardware-aware GUI restrictions. Those random-weight fixtures validate application
mechanics, not visual restoration quality. Use a trusted pretrained checkpoint for meaningful
output.

## Security Risks

Do not load custom `.pth` or `.pt` files from untrusted sources. Pickle-based PyTorch checkpoints
can execute arbitrary code. Prefer `.safetensors` for custom models when available.

## Licensing

LocalSR is distributed under the [MIT License](LICENSE). Slint, Spandrel, PyTorch, and individual
model checkpoints retain their own licenses; review Slint's royalty-free/GPL/commercial terms for
the way you distribute the application.
