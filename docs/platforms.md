# Platforms and hardware

Each LocalSR package bundles the app, a frozen Python worker and one PyTorch
build for its engine. You install one download and nothing else; models are
fetched on demand.

| Package | Engine | Status | Tested on |
| --- | --- | --- | --- |
| macOS · Apple Silicon | MPS | Published (v0.1.2-beta) | MacBook Pro M1 Pro, 16 GB |
| Windows · x86-64 | CPU | Test builds only | Windows 11, Core i7-8550U |
| Windows · x86-64 | DirectML (AMD, Intel, NVIDIA GPUs) | Test builds only | Intel UHD 620, driver 24.20.100.6286 |
| Windows · x86-64 | CUDA | Labs · no hardware run | — |
| Linux · x86-64 | CPU | Built, not published | Fedora 44 |
| Linux · x86-64 | AMD ROCm | Built, not published | Radeon RX 9060 XT 16 GB, ROCm 7.2 |
| Linux · x86-64 | NVIDIA CUDA | Labs · no hardware run | — |
| Linux · x86-64 | Intel XPU | Withheld | — |

"Tested" means the recorded checks in [Testing](testing.md) passed on that
machine with that package: images, video with audio, cancellation, worker
recovery, benchmarks and, where applicable, an installed upgrade. It does not
mean every model and resolution works on every GPU of that family.

## Why the other packages are not out yet

- **Windows.** LocalSR ships only royalty-free media code, built from an
  allowlisted LGPL FFmpeg ([details](licensing-media.md)). That build does not
  run on Windows yet, so the Windows workers cannot pass the codec gate. The CPU
  and DirectML test builds that passed acceptance used a different media runtime
  and are not distributed. The DirectML engine is also moving from
  `torch-directml` (Torch 2.4.1) to ONNX Runtime DirectML 1.24.4 on Torch 2.13;
  the new worker passed its source-level checks on the same PC, with NAFNet
  SIDD photo comparisons still outside the fixed GPU tolerance.
- **Windows CUDA** additionally waits for NVIDIA's written confirmation that the
  cuDNN 9 DLLs may be redistributed.
- **Linux.** The CPU, CUDA and ROCm AppImages build and carry verified update
  signatures. The GPU packages are 5–6 GB, beyond GitHub's release asset limit,
  and go out once the download host serves them. They also carry one known open
  advisory: Tauri's GTK3 stack pulls in `glib` 0.18, which has the unsoundness
  advisory [GHSA-wrw7-89jp-8q8g](https://rustsec.org/advisories/RUSTSEC-2024-0429.html).
  It is fixed only by the upstream move to GTK4, so it stays open until Tauri's
  Linux backend migrates.
- **Intel XPU on Linux** is withheld because Intel's oneAPI runtime licence asks
  the distributor to indemnify Intel and forbids reverse engineering, which
  conflicts with the LGPL relinking terms of the bundled media libraries. The
  source keeps XPU support.

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
