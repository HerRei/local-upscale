# Intel GPU support

Updated 13 September 2026. Windows integrated graphics remain supported through
compatible DirectML adapters. The prepared ONNX Runtime path uses DXGI adapter
indices, D3D12 capability checks and a verified DirectML execution profile. Shared
memory is described as a budget, not dedicated VRAM or a guarantee that a job fits.

The native i7-8550U / UHD 620 acceptance PC has two distinct evidence sets:

- **Installed MSIX through 1.0.7.0:** the earlier torch-directml runtime passed
  recorded images, videos, GUI comparisons, separate benchmarks and upgrades.
  WACK remains WARNING. A challenging NAFNet scan exceeded iGPU memory; the CPU
  two-stage scan completed. This did not establish NAFNet GPU quality.
- **Current source worker:** Torch 2.13.0 / ONNX Runtime DirectML 1.24.4 passed
  HAT and the other recorded model probes, tiled images, short video/audio,
  cancellation and allocation-failure recovery. Benchmark v2.1 scored CPU 1.16
  and iGPU 0.86 output MP/s, with measured consistency. NAFNet SIDD photo
  comparisons still fail the fixed GPU tolerance; no CPU restriction has been
  approved or applied. A package containing this worker is not built yet.

Other Intel adapters are not claimed as physically tested. The retained Linux XPU
package has architecture/handshake evidence, not physical Intel XPU inference.
SeedVR2 has no DirectML implementation. All agreed backend editions remain.

See the [runtime report](windows-inference-runtime-review.md),
[earlier installed acceptance](beta-acceptance-2026-09-12.md) and
[deferred build/acceptance procedure](windows-beta-build-handoff.md).
