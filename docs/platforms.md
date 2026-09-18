# Platforms and hardware

Each LocalSR package bundles the app, a frozen Python worker and one PyTorch
build for its engine. You install one download and nothing else; models are
fetched on demand.

| Package | Engine | Status | Tested on |
| --- | --- | --- | --- |
| macOS · Apple Silicon | MPS | Published (v0.1.3-beta) | MacBook Pro M1 Pro, 16 GB |
| Windows · x86-64 | CPU | Published (v0.1.3-beta) · not code-signed | Earlier builds: Windows 11, Core i7-8550U |
| Windows · x86-64 | DirectML (AMD, Intel, NVIDIA GPUs) | Published (v0.1.3-beta) · **untested** · not code-signed | Earlier test builds: Intel UHD 620, driver 24.20.100.6286 |
| Windows · x86-64 | NVIDIA CUDA | Published (v0.1.3-beta) · **untested** · not code-signed | Not yet run on an NVIDIA GPU |
| Linux · x86-64 | CPU | Published (v0.1.3-beta) | Earlier builds: Fedora 44 |
| Linux · x86-64 | AMD ROCm | Published (v0.1.3-beta) | Earlier builds: Radeon RX 9060 XT 16 GB, ROCm 7.2 |
| Linux · x86-64 | NVIDIA CUDA | Published (v0.1.3-beta) · **untested** | Not yet run on an NVIDIA GPU |
| Linux · x86-64 | Intel XPU | Withheld | — |

"Tested" means the recorded checks in [Testing](testing.md) passed on that
machine with that package: images, video with audio, cancellation, worker
recovery, benchmarks and, where applicable, an installed upgrade. It does not
mean every model and resolution works on every GPU of that family.

The Windows and Linux packages of v0.1.3-beta were built on GitHub-hosted runners
(`.github/workflows/windows-installers.yml` and `linux-appimages.yml`) with the
allowlisted LGPL media runtime, passed the codec policy and an automated smoke test
of the installed app, and ship with their corresponding-source bundles. They have
not been through this release's manual desktop checks, and they do not update
themselves yet. The CUDA and DirectML packages are published **untested**: they pass
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
