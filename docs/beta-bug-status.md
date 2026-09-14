# Reported bug status — 13 September 2026

**Not every reported problem can be called fully fixed.** The interface and
queue regressions below have fixes with targeted evidence. Restoration quality
still has specific limitations, and acceptance on the final distribution files
is incomplete. The public beta remains **not ready for publication**.

This is the isolated beta workspace. The active `.12` release, its checkouts,
jobs and runners are unchanged. This Mac's GUI has not been used: Mac evidence
is headless UI testing and real MPS worker execution. Windows and DDP evidence
includes native installed interfaces. A pass on one workload is not a claim
about all models, hardware or images.

**Current runtime qualification:** the GUI observations below belong to the
previous packages/source versions in the linked acceptance record. The new
Windows ONNX Runtime worker passes separate CPU/iGPU benchmark v2.1, tiles/ETA,
five-minute small-resolution export, custom imports, cancellation and allocation
recovery in source tests. It has not been packaged. NAFNet SIDD still has three
CPU/GPU numerical discrepancies; no temporary CPU restriction was approved.
See the [current handoff](beta-handoff-2026-09-13.md) for precise scope.

## Fixed with targeted verification

| Reported problem | Current behavior and evidence |
| --- | --- |
| Denoise/NomosWeb tile squares differ from the work being processed | The outline uses actual worker coordinates and source tile size; delayed JPEG decoding cannot move it backwards. DDP denoise and a three-image denoise → NomosWeb queue had zero sampled coordinate mismatches. |
| First opening an already-running HAT image gives the wrong grid | Full output geometry and the regular tile size establish the grid, including a first selection on a narrow edge tile. DDP: 1,526 samples; native Windows: 3,501 samples; no coordinate mismatches. |
| Switching stages or queued images shows tiles from the previous stage | Stage and media ownership reject stale events. Both denoise and upscale stages were observed on DDP and native Windows. |
| Original/enhanced comparison stops working during another queued job | A separate bounded result-preview service loads the selected completed output without waiting for inference. Native Windows loaded it in 0.283 seconds while HAT continued; real pointer input moved the slider to 82%. DDP completed the corresponding check in 0.593 seconds. |
| Returning to completed video A while B runs shows a black/broken comparison | Original and result players retain A's ownership. The exact A → start B → return A → play sequence passed on native Windows and DDP. Both Windows outputs fully decoded to 24 frames with increasing timestamps and readable audio. |
| Video progress stands still and has no ETA | The interface shows real frame/tile events and a measured ETA, including an explicit measuring state before enough timing exists. Final Windows MSIX 1.0.7.0 completed two real eight-frame HAT-S exports; an actual screenshot records the active edge tile and ETA. |
| Queue shows only the current item's timing | Current-job, next-item and whole-queue estimates are shown. Unmeasured work is identified; large queues are no longer truncated at 200 jobs for this calculation. |
| Choosing a folder scatters outputs | Add Folder selects a `LocalSR Results` subdirectory. Native Windows folder selection and four grouped HAT exports passed. DDP's configured-folder output path passed; its final AppImage native-dialog acceptance remains open. |
| Cancellation leaves the application stuck | A watchdog stops only the owned stalled worker, cleans its temporary output and restarts it. DDP recovered in 10.572 seconds; Windows in 13.908 seconds. Subsequent real jobs completed on both. |
| Settings can change incompatibly during processing | Incompatible controls remain locked through processing and cancellation. Media/result viewing remains available. |
| Benchmark button/run does not finish; CPU and GPU scores overwrite each other | A non-finite timing-variability value was rejected by the host, leaving it busy. Unmeasured variability is now `null` with a clear label. Installed Windows CPU and UHD 620 runs completed, retained separate scores through a reboot, and had no sampled tile-coordinate mismatches. |
| Intel integrated graphics unavailable | The native UHD 620 completed real DirectML HAT-S and SPAN jobs and a separate GPU benchmark. The actual device and reported CPU operator fallback are recorded. This is not validation of every Intel iGPU or every model. |

Detailed workloads, test versions and limitations are in
[the acceptance record](beta-acceptance-2026-09-12.md). Earlier MOV orientation,
HDR output/compatibility, SeedVR2 ROCm/memory behavior and loading-state work is
recorded in [video support](video-support.md) and
[platform acceptance](platform-acceptance-2026-09.md). These earlier fixes still
need to be carried into final-package acceptance; they are not new all-platform
passes inferred from the current screenshots.

## Improved, with remaining limitations

- **Scanned-document artifacts:** the NAFNet denoiser produced unstable values
  on some scan contexts on both CPU and ROCm. NomosWeb can amplify that damage.
  Bounded context retries and instability checks recover nine of eleven tested
  scans. Two remain rejected with a clear error and **no exported output**.
  This protects those reproduced cases; it does not prove artifact-free
  restoration for every input or replace the selected model.
- **SeedVR2:** smaller VAE tiles can still leave visible seams. Memory advice,
  compatible backend restrictions, reduced-memory options and bounded failures
  improve handling, but 16 GB is not a guarantee for arbitrary 4K workloads.
  The model retains its documented Labs limits.
- **HDR:** a compatible preservation/export path and model restrictions do not
  establish that SDR-trained HAT checkpoints have validated HDR restoration
  quality. SeedVR2 remains SDR-only in this integration. Custom-model HDR support
  retains explicit compatibility and quality requirements.

## Final-package verification still open

- A Linux AppImage initially failed to create an EGL display on DDP. Removing
  conflicting bundled Wayland libraries reproduced the fix; the corrected CPU
  AppImage launches and plays both video-comparison streams. Final CPU/ROCm
  folder, cancellation and resource-pressure cases are still being completed.
- Windows 1.0.7.0 upgrades preserve settings, recipes, installed model hashes
  and logical queue data. Its full WACK result remains **WARNING**, despite a
  validated embedded manifest and a live native PerMonitorV2/DPI check passing.
  The kit's DPI inspection reports COM E_FAIL; optional blocked-executable
  findings also remain. This is not an all-pass certification result.
- A full five-minute 480×854 SPAN/ROCm export and all 9,000 decoded frames passed.
  This does **not** establish a complete four-minute 4K export. Bounded 4K and
  final-package resource-pressure coverage remain on the checklist.
- The retained macOS app/DMG were signed, notarized and stapled, with real
  signed-worker MPS checks passing. The changed source needs a new candidate,
  fresh signing/notarization and final installed upgrade/recovery checks after
  the explicit build go-ahead.

See [the beta checklist](beta-release-checklist.md) for package, licensing,
publication and user decisions. No public release or Store submission has been
made.
