# LocalSR platform acceptance — September 2026

This record covers the isolated `codex/platform-acceptance` preview based on
`b607d81`. The installed Linux host is `94d2ccc`; its packaged Python worker is
`adc191c`, including the timing correction. The shared `fix/v0.0.12-release-pipeline` checkouts,
release runners and Mac mini VMs were left unchanged. No GUI tests were run on
the user's current Mac for this platform-testing task.

## Fixes found through platform testing

| Failure | Change and verification |
| --- | --- |
| Linux comparison player rejected valid H.264 videos with media error 4 / `NotSupportedError`. | Stream native-authorized files over a private loopback listener with byte ranges. Actual WebKitGTK playback now works, including returning to completed video A while job B runs. |
| Playback speed menu initially appeared blank. | Match the selected value to the HTML option type. Component regression test and installed Linux UI show `1×`. |
| CPU benchmark displayed the GPU selected for ordinary jobs and old job metrics. | Show the selected benchmark device and its capacity. The installed UI shows CPU/system RAM for CPU runs; measured benchmark results remain separate from ordinary job telemetry. |
| Windows native test executable terminated before its first test (`0xc0000139`). | Embed the Common Controls v6 application manifest in all MSVC link targets, including test harnesses. Native tests pass on the hosted Windows VM. |
| Fast Windows video tiles could report zero elapsed time and ETA. | Use the high-resolution performance counter throughout frame-by-frame timing. The expanded Windows HDR tests exposed the coarse Python 3.11 clock; the first-tile ETA assertions remain in place. |

The Linux transport addresses a documented
[WebKitGTK custom-scheme media limitation](https://github.com/tauri-apps/tauri/issues/3725).
The Windows test startup failure matches the
[Tauri manifest-linking issue](https://github.com/tauri-apps/tauri/issues/13419).
Python 3.11 uses `GetTickCount64` for Windows monotonic time and
`QueryPerformanceCounter` for the performance counter, as recorded in its
[clock implementation](https://github.com/python/cpython/blob/v3.11.9/Python/pytime.c).
An independent UI test now controls its elapsed-time clock instead of requiring
the CI host to render during one particular wall-clock second.

## Installed Linux checks

Machine: Fedora 44, Radeon RX 9060 XT (16 GB), ROCm 7.2, PyTorch 2.13,
approximately 30 GiB system RAM. Tests used the installed frozen worker and
native Tauri/WebKitGTK application, without a development Python interpreter
inside the worker process.

Both **CPU and AMD** passed the real SPAN worker acceptance cases:

- Reject a corrupt MOV, then successfully open valid media in the same worker.
- Enhance an odd-sized transparent image using a Unicode filename; retain alpha
  and the requested output dimensions.
- Export a 16-frame trim with ordered image content, increasing timestamps,
  correct dimensions, AAC audio, actual model tile previews and positive ETA.
- Cancel an export, remove its partial output, then successfully process another
  video in the same worker.

Installed GUI checks passed for source/result playback, the initial `1×` speed,
returning to a completed video during another job, and real CPU/AMD benchmarks
with separate stored results. Both benchmark runs were marked **unstable**;
their timings are acceptance evidence, not published performance comparisons.

SeedVR2-3B FP8 completed **20 frames at 720×1280 SDR** across multiple model
windows in 199.9 seconds. All frames decoded, timestamps increased, adjacent
frames differed, and the colour output was inspected. Cancelling during
diffusion took 0.216 seconds, left no partial video, and allowed another model
job in the same worker. Small VAE tiles still produce visible grid seams.

The installed native host's SHA-256 is
`2a4b0e249129c47a5ed5acc89085dd50709c3585468204daaa2df4b8003c3d06`.
The updated packaged worker's SHA-256 is
`eea0ec5d1536d6708502961ef46eb3f6768845d52c7f87d2b513763ce4373385`.
Its compact machine record is stored alongside the app at
`verification/platform-acceptance.json`.

## Automated checks and Windows VM

- Mac CLI pipeline before the final timing correction: **567 Python tests passed, 3 skipped; 74 frontend tests;
  50 Rust tests**. Formatting, linting, type checks, workflow validation and
  Python wheel/source packaging passed.
- Linux: **89 focused Python tests and 50 Rust tests passed**, plus the real
  installed-worker and GUI checks above.
- After the timing correction: **62 video/HDR Python tests and 40 application
  UI tests passed locally**, with Svelte checks clean. The newly frozen Linux
  worker passed all six real-model cases on both CPU and AMD and the installed
  native-host handshake.
- Windows at `b77113e`: **533 Python tests passed, 37 skipped; 74 frontend tests;
  49 Rust tests**, real frozen-worker CPU acceptance and native host/worker
  startup passed. See the [completed run](https://github.com/HerRei/local-upscale/actions/runs/34657344824).
- The expanded Windows run installed FFmpeg for media fixtures: **554 Python
  tests passed, 14 skipped, 2 failed** on first-frame ETA; **73 frontend tests
  passed, 1 failed** on the elapsed-time assertion. Both causes were corrected
  in `adc191c`. The real packaged CPU worker still passed all six acceptance
  cases in that run. Remaining skips concern POSIX/macOS tools, ROCm hardware
  and an optional external-checkpoint fixture.

**Final Windows verification is blocked.** GitHub refused to start the
[`adc191c` rerun](https://github.com/HerRei/local-upscale/actions/runs/34659604826)
because of the account's payment/spending-limit setting. The final timing fixes
and the workflow's bundled-frontend startup mode have not completed a Windows
rerun. Resolve that account block, then rerun the isolated workflow; do not treat
the earlier successful subset as final beta acceptance.

The Windows VM is GitHub-hosted so the active Mac mini release infrastructure is
not disturbed. Reports are retained in job logs and summaries; the workflow does
not delete release artifacts or upload large build directories.

Repeat the packaged-worker check on another machine with:

```sh
python scripts/acceptance_worker.py /path/to/engine/localsr-worker \
  --device cpu --report /path/to/acceptance.json
# Use --device cuda:0 for a ROCm or CUDA worker; add .exe on Windows.
```

The harness requires LocalSR's video dependencies in its driving interpreter.
It downloads the small, checksum-pinned Quick/SPAN checkpoint only when it is
not installed, uses disposable fixtures, and removes its temporary files and
worker on completion or failure.

## Cleanup and remaining release gates

The DDP app was reopened with the original MOV, original model/output settings
and original benchmark history restored. Test queue entries, generated desktop
videos, build caches and duplicate temporary engines were removed. The build
container was stopped. Cleanup reclaimed **23.6 GiB** on DDP. The task's local
Mac compiler cache and temporary package build were also removed. Cleanup initially
removed the Mac preview bundle stored inside Cargo's target directory; the bundle
was restored from the same `b607d81` source and its ad-hoc signature verified.
The original Mac process stayed running throughout and was not relaunched. The
bundle is now preserved separately from the discarded compiler intermediates.
The original MOV's
SHA-256 was rechecked and remained
`f0741dc543d650cdfb78bc6736cd7aac7d5f5d4297d0b4fea81325a8c9a8604c`.

This is bounded functional acceptance. It does **not** certify a full four-minute
4K export, SeedVR2 restoration quality or seam-free output, Windows GPU drivers,
HDR display quality, signed installation/update/uninstall, or beta readiness.
The [readiness register](../ci/beta-readiness.json) still governs those gates.


## Local cancellation, licensing and updater follow-up — 12 September

The follow-up remains in the isolated preview checkout; nothing was published and
no `.12` release job or current-Mac GUI was changed.

- Full Python suite: **568 passed, 3 skipped**. Frontend: **83 passed**, with
  Svelte checks clean. Linux native host: **58 passed**, Clippy clean with
  warnings denied. The frozen worker passed all six real acceptance cases on
  both CPU and ROCm.
- Real SeedVR2 FP8 on the 480p five-minute input, requesting 1920-pixel output:
  cancellation during encoding with memory reduction **off** and previews
  **off** completed in **0.26 seconds**. The same worker then completed a real
  SPAN inference. This checks cancellation, not completion of that large export.
- A deliberately unresponsive worker tested the installed native host fallback:
  cancellation completed in **8.31 seconds**, the old process exited, a new worker
  started, its partial file disappeared and an existing finished export survived.
  Processing model controls stayed disabled until cancellation completed.
- Both RealPLKSR photo/anime checkpoints were downloaded through two separate
  native UI acknowledgement steps. Buttons were disabled before each acknowledgement;
  both files matched their catalog SHA-256. Both then completed actual ROCm
  inference from 32×32 to 128×128 and remain installed on DDP. Their commercial
  rights remain unverified; the reminder remains visible when installed.
- A loopback-only signed updater fixture upgraded **0.0.13-beta.1 → beta.2**,
  using a disposable test identity and profile. The application restarted through
  systemd, its binary matched the new artifact, and recipes, nondefault settings,
  queue entries and the model checksum survived. The unchanged engine was reused.
- A correctly signed beta.3 fixture with a broken startup was rejected after the
  startup check. The beta.2 executable was restored and the worker restarted;
  profile and model checks still passed. A CUDA contract offered to this ROCm
  installation was rejected before download, and the check button recovered.
- Unit regressions additionally cover corrupt signatures despite recomputed
  checksums, interrupted/cancelled downloads, low disk space, unreadable settings,
  WAL-aware backups and incompatible channels/protocols/engine IDs.
- The locally edited website passed link/release validation and **70 tests**
  (6 comparison, 30 site, 34 interaction assertions). No deployment was performed.

The current preview deliberately has no production update key or enabled feed.
Actual signed macOS/Windows upgrades, Windows split-engine upgrades and native
AppImage updates remain release-candidate acceptance work. The Linux test used
an unchanged engine; a complete production engine replacement still needs target
package acceptance. These local results do not close the beta readiness gates.
See [local updater setup](local-updates.md) and the [beta checklist](beta-release-checklist.md).
