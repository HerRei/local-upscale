**LocalSR beta platform coverage**

Scope confirmed by the user on 12 September 2026: retain the full feature set
and all eight existing Tauri backend targets. Use Labs for paths with limited
hardware testing. The main testing focus is macOS Apple Silicon/MPS, Linux
AMD ROCm and Windows CPU/Intel integrated graphics. This replaces the earlier
proposal to defer other backends from the beta.

“Tested” describes a recorded workload, device and build. It does not promise
that every model, resolution or GPU in that family works. Installed release
acceptance remains separate from the preview results below. The table records
the **previous package set**, not packages containing the current source. All
new builds are explicitly paused until user authorization.

The later Windows source-worker run uses Torch 2.13.0 CPU / ONNX Runtime DirectML
1.24.4. Separate v2.1 benchmarks passed: CPU 1.16 MP/s, UHD 620 0.86 MP/s, with
measured consistency. Larger tiles, video/audio, cancellation and real allocation
failure/recovery pass. Three NAFNet SIDD photo comparisons still fail GPU
tolerance; no CPU restriction is approved. [Current runtime evidence](windows-inference-runtime-review.md).

| Existing target | Planned beta coverage | Recorded preview evidence | Remaining verification |
| --- | --- | --- | --- |
| `macos-arm64-mps` | Main path; functional checks recorded | Earlier HAT/face/HDR/SeedVR2 checks; maintained signed ARM64 beta passed bundled-host handshake and six real MPS image/video/recovery cases headlessly. | App/DMG notarization, stapling, Gatekeeper and mounted-worker checks pass. Native installed upgrade/data checks remain; no current-Mac GUI claim. |
| `windows-x86_64-cuda` | Labs | No matching NVIDIA GPU acceptance run in the current local record. | Package and split-engine checks; gather actual NVIDIA device/driver results before making a tested claim. |
| `windows-x86_64-cpu` | Main path; functional checks recorded | Native installed CPU/DirectML MSIX passed CPU images/video, audio, cancellation/recovery, batch comparison, benchmark and data-preserving upgrades through 1.0.7.0. | WACK WARNING and external Open/Reveal observation remain; explicit reinstall/backup recovery passed. Separate direct CPU package/trust is pending. |
| `windows-x86_64-directml` | Intel iGPU is a requested main path; other adapters remain Labs | Native Windows 11 / UHD 620 driver 24.20.100.6286: real DirectML HAT-S image and short video, SPAN image/video, audio, cancel/recovery; actual `privateuseone:0` execution with an `aten::roll` CPU fallback. [Details](beta-acceptance-2026-09-12.md). | Installed GUI, separate CPU/iGPU benchmarks and upgrades passed. Final 1.0.7.0 WACK reports WARNING despite native PerMonitorV2 verification; External Open/Reveal observation is pending behind the locked desktop; reinstall/verified-backup recovery passed. Other DirectML GPUs are untested. |
| `linux-x86_64-cpu` | CPU companion; functional checks recorded | Installed DDP frozen worker passed the six real image/video acceptance cases; native playback and CPU benchmark checked. | Final CPU AppImage host and six real worker cases pass. Native playback and profile-version backup/preservation pass; signed native upgrades and folder dialog remain. |
| `linux-x86_64-cuda` | Labs | No matching NVIDIA GPU acceptance run in the current local record. | Final AppImage architecture inspection, production signature and bundled-host handshake pass. Actual NVIDIA device inference remains untested. |
| `linux-x86_64-xpu` | Labs | No matching Intel XPU acceptance run in the current local record. | Final AppImage architecture inspection, production signature and bundled-host handshake pass. Actual Intel XPU device inference remains untested. |
| `linux-x86_64-rocm` | Main path; functional checks recorded | Fedora 44, RX 9060 XT 16 GB: real image/video inference, playback, separate benchmarks, cancellation/recovery and short SeedVR2 FP8 exports. | Five-minute source-worker export is retained. Final AppImage host, six worker cases, live result switching, six-frame 4K-to-8K export and forced file-limit recovery pass. Native signed upgrades/folder dialog remain. |

The target IDs come from [the existing registry](../ci/tauri-targets.json).
Windows vendor coverage remains: AMD and Intel through compatible DirectML,
NVIDIA through CUDA or compatible DirectML, with CPU available separately.
The DirectML target needs both a direct installer for the first website/GitHub
beta and the later Store MSIX; only UHD 620 has native Windows GPU acceptance
evidence. SeedVR2 has no DirectML implementation, so vendor compatibility does
not imply every Labs model works in that edition.
[DirectML hardware requirements](https://onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider.html).

The Windows Intel iGPU path refers to DirectML, not Linux's Intel XPU package.
A VM running on an Intel host does not establish Intel GPU inference. Capture
the selected adapter and actual processing device, including any CPU fallback.
Compatible Intel iGPUs are included: [local selection, naming and recovery
fixes](intel-gpu-support.md) address gaps found during this review. The UHD 620 evidence establishes this particular adapter and workload; it does
not establish support for every Intel GPU.
Intel macOS belongs to the separate legacy workflow and is absent from this
Tauri registry; this decision does not add a new native target.

**What Labs means for this beta**

- Retain experimental features and backend targets. Mark missing hardware or
  quality evidence clearly; a full physical GPU matrix is not a prerequisite
  for offering a backend as Labs.
- Every distributed package still needs packaging/launch checks, working model
  compatibility checks, cancellation/recovery, preserved user data and the
  appropriate signing/update path. Known installation failures or destructive
  behavior need fixes; a Labs label does not establish that a broken package works.
- Run the core image/SDR-video acceptance workflow on the main tested paths.
  Record the device, driver, package hash, model and workload. Broader hardware,
  model-quality and performance coverage can develop through volunteer feedback.
- Keep backend testing and model compatibility separate. SeedVR2 3B FP16 is
  configured for CUDA/ROCm/MPS; FP8 is configured for CUDA/ROCm. A DirectML or XPU
  package does not make SeedVR2 compatible with that backend. HDR preservation,
  SeedVR2, de-flicker and video-face processing retain their feature-level Labs
  limitations even on a tested device.

**Checks to carry into the beta candidate**

- [x] Record full target scope and the main-path/Labs distinction.
- [x] Record bounded real Intel UHD 620 DirectML inference on native Windows,
  with the driver, actual device and operator fallback identified. Final installed
  candidate acceptance remains separate.
- [ ] Complete main-path candidate acceptance and package checks for the Labs
  artifacts. Missing broad Labs hardware/quality coverage is disclosed, not an
  automatic reason to drop those targets.
- [ ] Implement Windows engine delivery for the retained CPU/CUDA/DirectML
  targets. A single Store identity does not route alternative x64 packages by GPU.
- [ ] Apply the same coverage labels to the eventual app, downloads, Store text
  and release notes, identifying which results belong to the actual candidate.
- [x] Prepare a separate beta readiness configuration reflecting this decision:
  retain common distribution/data checks and main-path functional checks; track
  remaining Labs hardware and quality coverage as experimental follow-up.

Prior evidence: [platform acceptance](platform-acceptance-2026-09.md),
[video/HDR and SeedVR2 checks](video-support.md),
[local update/cancellation record](local-update-acceptance.json).
Next steps remain in the [beta checklist](beta-release-checklist.md).
The separate beta work does not modify `.12`, its readiness register or release
jobs, publish packages or relaunch the Mac application. Current candidate work
is recorded in the separate public-beta plan and evidence.
