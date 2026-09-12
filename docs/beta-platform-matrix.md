**LocalSR beta platform coverage**

Scope confirmed by the user on 12 September 2026: retain the full feature set
and all eight existing Tauri backend targets. Use Labs for paths with limited
hardware testing. The main testing focus is macOS Apple Silicon/MPS, Linux
AMD ROCm and Windows CPU/Intel integrated graphics. This replaces the earlier
proposal to defer other backends from the beta.

“Tested” describes a recorded workload, device and build. It does not promise
that every model, resolution or GPU in that family works. Installed release
acceptance remains separate from the preview results below.

| Existing target | Planned beta coverage | Recorded preview evidence | Remaining verification |
| --- | --- | --- | --- |
| `macos-arm64-mps` | Main path; functional checks recorded | Bounded real HAT/face-model MPS inference, short HLG/PQ checks and a short SeedVR2 SDR export. | Maintained native candidate, signed/notarized installation, inference and upgrade/data checks. Mac candidate work remains deferred until the other prerequisites are ready. |
| `windows-x86_64-cuda` | Labs | No matching NVIDIA GPU acceptance run in the current local record. | Package and split-engine checks; gather actual NVIDIA device/driver results before making a tested claim. |
| `windows-x86_64-cpu` | Main path; functional checks recorded | Windows VM native tests and real frozen CPU-worker image/video, audio, cancellation and recovery checks. | Rerun the final Windows timing fixes and verify the installed candidate, Store integration and upgrades. |
| `windows-x86_64-directml` | Intel iGPU is a requested main path; other adapters remain Labs | The user identifies Intel iGPU as tested. The saved Windows acceptance record establishes CPU execution and does not identify an Intel GPU run. | Capture or repeat the Intel iGPU run with GPU model, driver, build and actual inference device before using a verified GPU badge. Resolve the recorded DirectML runtime dependency issue. |
| `linux-x86_64-cpu` | CPU companion; functional checks recorded | Installed DDP frozen worker passed the six real image/video acceptance cases; native playback and CPU benchmark checked. | Verify the final AppImage installation and its upgrade/data behavior. |
| `linux-x86_64-cuda` | Labs | No matching NVIDIA GPU acceptance run in the current local record. | Package checks; gather actual NVIDIA device/driver results before making a tested claim. |
| `linux-x86_64-xpu` | Labs | No matching Intel XPU acceptance run in the current local record. | Package checks; gather actual Intel XPU device/driver results before making a tested claim. |
| `linux-x86_64-rocm` | Main path; functional checks recorded | Fedora 44, RX 9060 XT 16 GB: real image/video inference, playback, separate benchmarks, cancellation/recovery and short SeedVR2 FP8 exports. | Verify the final AppImage and upgrade path; complete representative long-video acceptance. |

The target IDs come from [the existing registry](../ci/tauri-targets.json).
The Windows Intel iGPU path refers to DirectML, not Linux's Intel XPU package.
A VM running on an Intel host does not establish Intel GPU inference. Capture
the selected adapter and actual processing device, including any CPU fallback.
Compatible Intel iGPUs are included: [local selection, naming and recovery
fixes](intel-gpu-support.md) address gaps found during this review. Pending
physical test evidence does not mean the backend is disabled or excluded.
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
- [ ] Add the Intel iGPU evidence or repeat a bounded real GPU run on an isolated
  Windows machine; retain the pending label until then.
- [ ] Complete main-path candidate acceptance and package checks for the Labs
  artifacts. Missing broad Labs hardware/quality coverage is disclosed, not an
  automatic reason to drop those targets.
- [ ] Implement Windows engine delivery for the retained CPU/CUDA/DirectML
  targets. A single Store identity does not route alternative x64 packages by GPU.
- [ ] Apply the same coverage labels to the eventual app, downloads, Store text
  and release notes, identifying which results belong to the actual candidate.
- [ ] Prepare a separate beta readiness configuration reflecting this decision:
  retain common distribution/data checks and main-path functional checks; track
  remaining Labs hardware and quality coverage as experimental follow-up.

Prior evidence: [platform acceptance](platform-acceptance-2026-09.md),
[video/HDR and SeedVR2 checks](video-support.md),
[local update/cancellation record](local-update-acceptance.json).
Next steps remain in the [beta checklist](beta-release-checklist.md).
This planning update does not modify `.12`, its readiness register or release
jobs, publish packages, run new hardware tests or relaunch the Mac application.
