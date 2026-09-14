# Video acceptance record — 5 September 2026

This is a historical source-level check from before the current beta. Its SDR-only
HDR handling and benchmark interface have since changed. Use [video support](video-support.md)
and the [beta acceptance record](beta-acceptance-2026-09-12.md) for current behavior.

## Scope

The check covered presentation timing, variable frame rate, right-angle orientation,
mirrors, selected clips, compatible audio, conservative de-flicker and bounded
benchmark captures. Source timing accompanied decoded frames through the worker.
The app and standalone report used real captured model output for their comparisons.

The run used checksum-verified `span_photo_x4` on MPS, Torch 2.13.0 and PyAV 18.1.0.

| Case | Frames | Largest timestamp shift | Result |
| --- | ---: | ---: | --- |
| Constant rate with audio | 24 | 0 ms | Pass |
| Variable rate with audio | 10 | 0 ms | Pass |
| 90° orientation with audio | 24 | 0 ms | Pass |
| VFR trim, frames 2–6, with audio | 5 | 0 ms | Pass |
| Conservative de-flicker | 24 | 0 ms | Pass |

A moving-square probe retained all seven squares. The temporal routing probe used
an identity engine and returned three selected frames with audio; it did not run
SeedVR2. MPS warm-up captures covered the three benchmark scenes, including the
3072×2048 tiled scene. A complete timed multi-device benchmark was not part of this run.

## Interface and automated checks

Headless Chromium checked playback, trim-offset seeking, scene navigation,
comparison reveal, detail controls and a 390-pixel layout. The app replayed actual
captured MPS worker envelopes for the interface check; no installed Tauri binary
was exercised in that step. The report's file playback and seeking passed.

| Check | Result at the time |
| --- | --- |
| Python suite | 483 passed, 2 skipped |
| Frontend suite | 51 passed |
| Rust library suite | 41 passed |
| Svelte/TypeScript | 0 errors, 0 warnings |
| Frontend build and changed-file lint | Passed |

The report artifacts include clip pairs, timing measurements, motion strips and
browser observations. The original operator handoff and its local artifact
locations are preserved in the maintainer's acceptance records. No private
machine paths are needed to build LocalSR.

## Reproduce a video report

With the development environment active and the Quick model already installed:

```sh
python scripts/benchmark_video.py --device auto --output ./build/video-review
```

The command requires the documented FFmpeg fixture features and an unused output
directory. It does not download weights or overwrite an existing report. See
[development checks](development.md) for the normal test commands.
