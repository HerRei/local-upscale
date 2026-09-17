# Platforms and hardware

Each LocalSR package bundles the app, a frozen Python worker and one PyTorch
build for its engine. You install one download and nothing else; models are
fetched on demand.

| Package | Engine | Status | Tested on |
| --- | --- | --- | --- |
| macOS · Apple Silicon | MPS | Published (v0.1.3-beta) | MacBook Pro M1 Pro, 16 GB |
| Windows · x86-64 | CPU | Published (v0.1.3-beta) · not code-signed | Earlier builds: Windows 11, Core i7-8550U |
| Windows · x86-64 | DirectML (AMD, Intel, NVIDIA GPUs) | Test builds only | Intel UHD 620, driver 24.20.100.6286 |
| Windows · x86-64 | CUDA | Withheld | — |
| Linux · x86-64 | CPU | Published (v0.1.3-beta) | Earlier builds: Fedora 44 |
| Linux · x86-64 | AMD ROCm | Withheld | Earlier builds: Radeon RX 9060 XT 16 GB, ROCm 7.2 |
| Linux · x86-64 | NVIDIA CUDA | Withheld | — |
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
themselves yet.

## Why the other packages are not out

- **CUDA (Windows and Linux)** is withheld until NVIDIA's redistribution terms are
  settled ([legal assessment](licensing-media.md#legal-assessment)) and a package
  has run on NVIDIA hardware.
- **AMD ROCm (Linux)** builds and passes the same checks, but PyTorch's ROCm build
  bundles its own copies of libnuma and elfutils (LGPL), built outside Ubuntu, whose
  exact sources are not identified yet, and AMD's closed-source aqlprofile library,
  whose redistribution terms are unconfirmed. It is withheld until both are settled.
- **DirectML** is moving from `torch-directml` (Torch 2.4.1) to ONNX Runtime
  DirectML 1.24.4 on Torch 2.13; the new worker passed its source-level checks on
  the UHD 620 PC, with NAFNet SIDD photo comparisons still outside the fixed GPU
  tolerance, so it stays a test build.
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
