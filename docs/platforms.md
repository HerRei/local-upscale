# Platforms and hardware

Each LocalSR package bundles the app, a frozen Python worker and one PyTorch
build for its engine. You install one download and nothing else; models are
fetched on demand.

| Package | Engine | Status | Tested on |
| --- | --- | --- | --- |
| macOS · Apple Silicon | MPS | Published (v0.2.2-beta) | MacBook Pro M1 Pro, 16 GB |
| Windows · x86-64 | CPU | Published (v0.2.2-beta) · not code-signed | Windows 11 virtual machine, 4 cores (v0.2.0-beta); earlier builds: Core i7-8550U |
| Windows · x86-64 | DirectML (AMD, Intel, NVIDIA GPUs) | Published (v0.2.2-beta) · **untested** · not code-signed | Earlier test builds: Intel UHD 620, driver 24.20.100.6286 |
| Windows · x86-64 | NVIDIA CUDA | Published (v0.2.2-beta) · **untested** · not code-signed | Not yet run on an NVIDIA GPU |
| Linux · x86-64 | CPU | Published (v0.2.2-beta) | Earlier builds: Fedora 44 |
| Linux · x86-64 | AMD ROCm | Published (v0.2.2-beta) | Earlier builds: Radeon RX 9060 XT 16 GB, ROCm 7.2 |
| Linux · x86-64 | NVIDIA CUDA | Published (v0.2.2-beta) · **untested** | Not yet run on an NVIDIA GPU |
| Linux · x86-64 | Intel XPU | Withheld | — |

"Tested" means the recorded checks in [Testing](testing.md) passed on that
machine with that package: images, video with audio, cancellation, worker
recovery, benchmarks and, where applicable, an installed upgrade. It does not
mean every model and resolution works on every GPU of that family.

The Windows and Linux packages of v0.2.2-beta were built on GitHub-hosted runners
(`.github/workflows/windows-installers.yml` and `linux-appimages.yml`) with the
allowlisted LGPL media runtime, passed the codec policy and an automated smoke test
of the installed app, and ship with their corresponding-source bundles. The Windows
CPU installer of v0.2.0-beta was also installed and used in a Windows 11 virtual
machine; v0.2.1-beta and v0.2.2-beta change only the preview's comparison and video playback on top of it. None of them
update themselves yet. The CUDA and DirectML packages are published **untested**: they pass
the same automated checks on runners without a GPU, but CUDA has never run on an
NVIDIA GPU and DirectML has not produced results within the accuracy tolerance on
one. The CUDA and ROCm downloads exceed GitHub's 2 GB file limit and come in parts.

## What is untested or withheld

- **CUDA (Windows and Linux)** is published untested. It contains only the NVIDIA
  libraries that NVIDIA's CUDA Toolkit EULA and cuDNN supplement identify as
  distributable (`packaging/nvidia/redistributables.json`, checked on every build);
  files the license does not list are left out. The Windows engine ships as verified parts next to
  the installer. Nobody has run either package on an NVIDIA GPU yet.
- **AMD ROCm (Linux).** PyTorch's ROCm wheel bundles libnuma and libelf built on
  AlmaLinux. The package ships Ubuntu 24.04's own builds instead, found by soname under
  every file name they appear as (every library that uses them is link-checked with
  `ldd -r`), and its source bundle carries their Ubuntu sources. AMD's aqlprofile is MIT-licensed in
  [ROCm/rocm-systems](https://github.com/ROCm/rocm-systems).
- **DirectML** moved from `torch-directml` (Torch 2.4.1) to ONNX Runtime
  DirectML 1.24.4 on Torch 2.13. It is published untested: the worker passed its
  source-level checks on the UHD 620 PC, but NAFNet SIDD photo comparisons are still
  outside the fixed GPU tolerance, so denoising results can differ from the CPU's.
- **Intel XPU on Linux** is withheld because Intel's oneAPI runtime licence asks
  the distributor to indemnify Intel. The source keeps XPU support.
- **Linux GTK advisory.** Tauri's GTK3 stack pulls in `glib` 0.18, which has the
  unsoundness advisory [GHSA-wrw7-89jp-8q8g](https://rustsec.org/advisories/RUSTSEC-2024-0429.html).
  It is fixed only by the upstream move to GTK4, so it stays open in the published
  AppImages until Tauri's Linux backend migrates.

## Engine notes

- **MPS.** Model weights are converted on the CPU before upload so two copies
  never sit in unified memory at once. SeedVR2 3B FP16 runs on MPS; FP8 needs
  CUDA or ROCm. H.264, HEVC and AAC decode and encode through the system codecs
  ([media formats](licensing-media.md)).
- **Neural Engine (Core ML).** On Apple Silicon, SPAN, Real-ESRGAN (RRDBNet) and
  FBCNN run through Core ML on the Neural Engine, and RealPLKSR runs on the GPU
  through Core ML, whose program is faster than MPS for it. Every other model keeps
  MPS: HAT, SwinIR and NAFNet produce broken FP16 output and SCUNet does not convert.
  The first job with a model and tile size converts the checkpoint into an FP16 Core
  ML program (1 to 40 seconds and up to about 4 GB of memory for RealPLKSR on an M1
  Pro), compiles it and caches it in
  `~/Library/Caches/LocalSR/coreml`; the program never leaves the Mac. Its first tile
  then runs both ways. Core ML is kept only if it matches the PyTorch result (at
  least 45 dB PSNR) and is not more than 1.3× slower than MPS, or not slower at all
  for the GPU program; the verdict is stored per Mac, macOS version and toolchain,
  and anything that fails, times out or errors keeps MPS. Each model runs at one
  fixed input size, so edge tiles get a full window of neighbouring pixels instead
  of padding. Tiles larger than 512 px with a 32 px halo (16 px for Real-ESRGAN,
  whose conversion needs too much memory beyond that) and the benchmark keep MPS.
  This is the *Neural Engine* entry under Hardware, the default on Macs where Core
  ML is available (it is not listed elsewhere); the status bar names the unit the
  selected model will use, or says the model is unsupported on the Neural Engine.
  *Apple GPU (Metal)* keeps every model on MPS, and `LOCALSR_DISABLE_COREML=1` does
  the same for the worker and the command line.
  Measured on the M1 Pro (2026-10-24, 256 px tiles): per tile, SPAN ran 2.0× as
  fast as MPS FP32, Real-ESRGAN anime 6B 3.4×, FBCNN 2.4× and RealPLKSR 1.3×.
  Whole images match the MPS output at 52 to 62 dB for SPAN, Real-ESRGAN and FBCNN
  (FP16 rounding) and 46 dB for RealPLKSR, where the edge windows differ most. Against
  an untiled reference the Core ML path is the closer one for RealPLKSR and
  Real-ESRGAN, because their receptive field exceeds the halo.
- **Video on the Neural Engine.** Frames of a video share one size, so SPAN runs
  each frame in one Core ML call (a rectangular program converted once per frame
  size) instead of overlapping tiles. Programs run through Core ML's Objective-C
  API (PyObjC), which releases Python's GIL, unlike coremltools; decoding,
  restoring and encoding then overlap on separate threads. The *Automatic* video
  format, the default, streams frames straight into the Mac's hardware HEVC
  encoder (VideoToolbox, MP4, SDR) without the lossless intermediate the H.264/HEVC
  exports otherwise use; MKV, HDR preservation and trimmed clips keep AV1 or the
  intermediate. No Apple chip encodes AV1 in hardware. Measured on the M1 Pro with
  a 15-second 720×1280 iPhone clip upscaled 4× with SPAN (461 frames): 1.58 frames
  per second with tiles and AV1 before, 3.6 now (2 minutes 8 seconds).
- **DirectML.** Any DXGI adapter with D3D12 support can be selected; only the
  UHD 620 has been run. Some graph nodes execute on the CPU. SeedVR2 has no
  DirectML implementation.
- **ROCm.** PyTorch addresses the GPU as `cuda:0`; that is normal. On gfx1200 a
  tall `[N,3] @ [3,3]` matrix multiply returned wrong values after row 524,288,
  which corrupted colour conversion; LocalSR uses per-channel arithmetic there
  instead (mean absolute error below 1e-6 against the CPU).
- **CPU.** Every package can run on the CPU. On the i7-8550U the CPU benchmark
  was faster than the integrated GPU.

## Memory guidance

| Model | Memory while running (catalog estimate) |
| --- | --- |
| SPAN ×4 NomosUni (Quick) | about 500 MB |
| RealPLKSR ×4 NomosWebPhoto (Best) | about 1.2 GB |
| HAT-S ×4 | about 2 GB |
| NAFNet SIDD (camera noise) | about 4 GB |
| HAT-L ×4 | about 6 GB |
| SeedVR2 3B FP8, five 4K frames, memory saving on | measured 9.9 GiB GPU peak on the RX 9060 XT, plus 10 GiB system RAM |

Tiling keeps large images within budget; the *Safe memory* option lowers the
tile size further. Video adds the decoder, encoder and preview buffers on top.
When a job does not fit, LocalSR reports the allocation failure and recovers the
worker; it does not lift GPU limits or reduce the output size silently.
