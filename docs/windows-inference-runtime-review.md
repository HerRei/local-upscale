# Windows inference runtime review

Updated 13 September 2026. The replacement is implemented in isolated beta
source and exercised through the real worker on the native Intel acceptance PC.
**No package containing it has been built.** Installed MSIX 1.0.7.0 and its
successful upgrades remain the earlier comparison baseline.

## Implementation and dependencies

The prepared DirectML lock uses Torch 2.13.0 CPU, torchvision 0.28.0, ONNX 1.22.0,
ONNX Runtime DirectML 1.24.4 and ml_dtypes 0.5.4. It replaces the old
`torch-directml` dependency, which pins Torch 2.4.1. This is ONNX Runtime's
DirectML provider, not an implementation of the Windows ML App SDK.

The adapter converts a verified Spandrel descriptor, preserves padding/cropping,
and validates raw output before quantization. Shape-specific sessions have a
bounded two-entry cache. DXGI supplies real adapter indices and names. Runtime
telemetry is disabled before creating a session. The first GPU profile must
contain DirectML execution; an all-CPU run cannot earn a GPU score. Some graph
nodes can still execute on CPU; node counts are not a compute-offload percentage.
Cancellation and allocation failures release owned runtime resources and allow
subsequent jobs. No checkpoint or license policy is changed by conversion.

The worker specification collects provider libraries through the upstream
PyInstaller hook and explicitly includes ONNX Runtime's LICENSE and
ThirdPartyNotices.txt. Their presence and execution in a frozen worker still
require the deferred build. The 56-distribution lock passes pip dependency
validation and its dated OSV lookup returned no advisories. This does not audit
all bundled native libraries or establish redistribution compliance.

Primary references: [old plugin requirements and telemetry notice](https://pypi.org/project/torch-directml/),
[PyTorch loading advisory](https://github.com/pytorch/pytorch/security/advisories/GHSA-53q9-r3pm-6pq6),
[DirectML provider requirements](https://onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider.html),
[versioned runtime notices](https://github.com/microsoft/onnxruntime/blob/v1.24.4/ThirdPartyNotices.txt).

## Numerical results and remaining choice

The initial 153 small comparisons are retained under
`build/beta-review/runtime-and-slint/`; they preceded these corrections.
The current evidence is under `build/beta-review/windows-runtime-native/`.

| Current native comparison | Result |
| --- | --- |
| HAT-S, SPAN, NAFNet SIDD: five patterns at 32 × 32, 47 × 65 and 96 × 128 | 39 pass; three SPAN checker cases rejected as unstable on both paths; three NAFNet SIDD photo comparisons fail the fixed tolerance |
| Nine remaining catalog models: five patterns at 32 × 32 | 45/45 pass, with actual DirectML execution verified |
| SPAN worker output at 769 × 513 → 3076 × 2052 | CPU/iGPU maximum difference one 8-bit step; mean difference 0.000071 steps |

The nine-model group includes HAT-L, both HAT face forks, all three RealPLKSR
entries, Real-ESRGAN, FBCNN and NAFNet GoPro. Face checkpoints were used only in
private authorized tests and remain verified manual imports. Tiny comparisons
do not establish broad model quality or large-image performance for every model.

SPAN's original checkpoint itself produces extreme activations on the offending
high-contrast checker. The author's ONNX export and FP64 checks also expose the
instability. Freezing the exact FP32 evaluation convolution preserves its intended
path; bounded context retries and raw-output guards prevent damaged exports.
The benchmark now uses a stable bounded-noise workload, versioned **v2.1**.
Old v2 reference results retain their provenance and are not compared as v2.1.

NAFNet normalization conversion fixed the original edge discrepancy, but SIDD
photo maximum errors remain **0.000732, 0.001805 and 0.000940** at the three sizes.
The fixed gate is `atol=0.0002`, `rtol=0.001` on normalized RGB; it was not relaxed.
A double-precision normalization experiment did not resolve the discrepancy.
These are one photo fixture at three sizes, not three independent photos. The
largest observed difference is about 0.46 on a 0–255 colour scale before rounding.
It fails the strict numerical gate but does not by itself establish visible
corruption or explain the earlier scanned-document instability.

**The user chose further bounded investigation first, deferred to a later point.**
No temporary CPU restriction, model removal or silent fallback was approved or
implemented. Plan an initial 2–4-hour investigation: locate the first meaningful
operator divergence, inspect representative output, and measure practical SIDD
CPU/GPU timings before recommending a fix or policy. This is an investigation
estimate, not a promised fix time or a scheduled task. The final runtime release
treatment remains unresolved. NAFNet GoPro is separate and passed the small probes.

## Source-worker acceptance on native Windows

Windows 11 Home, Core i7-8550U, 15.89 GiB RAM, UHD 620 driver 24.20.100.6286:

| Check | Measured result |
| --- | --- |
| Separate benchmark v2.1 | CPU 1.16 output MP/s, CV 1.82%; iGPU 0.86 output MP/s, CV 3.97%; both pass the unchanged 5% consistency gate |
| Slow large benchmark scene | Three measured repetitions on each device; median CPU 87.880 s / iGPU 116.003 s |
| Benchmark activity | 212 tile events and three previews per device; separate terminal scores |
| Odd alpha image and larger tiled image | Geometry, finite output, alpha and CPU/GPU comparison pass |
| Sixteen-frame MOV → MP4 with audio | Full frame count, monotonic timestamps, motion and audio pass |
| Live rendering | Worker tile boxes/JPEGs, frame identifiers and ETA events pass; 35 boxes / 70 events on each larger-image run |
| Active cancellation | 0.125 s after an observed tile; partial output/scratch cleanup and next job on the same worker pass |
| Real allocation pressure | Only the owned worker was limited to 661,528,576 bytes by a Windows Job Object; allocation failed safely in 1.265 s; removing the limit allowed the same worker to recover |
| Sustained video export | Five minutes at 12 fps, 160 × 90 → 640 × 360: all 3,600 frames decode, timestamps increase, audio 300.011 s; 1,850.65 s processing, 563.93 MiB peak worker-tree RSS, 10.19 MiB settled spread |
| Custom models | CPU/DirectML Safetensors imports, corrupt-file/unverified-pickle rejection and same-worker recovery pass; Mac CPU/MPS source-worker equivalents also pass |

The soft 45-second benchmark sampling budget now allows at least three
measurements on slow devices. It remains bounded; no repeated benchmark was run
after both corrected results passed. Earlier single-sample runs and harness-path
errors are retained separately from the successful run.

The sustained test produced 7,200 tile updates, 7,201 preview events and 10,797
positive ETA events. Three sampled output previews were inspected. It is a small
resolution longevity test, not full-length 4K acceptance. Ten-second socket
snapshots found no remote worker connections; this is not a packet capture.
Reports are linked in the [dated handoff](beta-handoff-2026-09-13.md).
Source-worker event checks do not replace
installed webview playback, result switching, Open/Reveal, upgrade/recovery,
privacy observation or WACK on the next MSIX. Existing GUI evidence remains
labelled with the package version that actually produced it.

## Deferred acceptance

Follow the [Windows build handoff](windows-beta-build-handoff.md) only after the
user's explicit go-ahead. Native Intel remains acceptance-only. Verify frozen
provider DLLs/notices, custom imports, the approved SIDD policy, installed tiles,
benchmarks, video/result switching, cancellation, data-preserving upgrades and
WACK. SeedVR2 retains its existing compatible CUDA/ROCm/Metal editions; this work
does not add a DirectML implementation. All eight backend targets remain in scope.
