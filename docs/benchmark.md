# LocalSR benchmark

## Visual system benchmark (v2)

The desktop's **Run Benchmark** opens a render viewer with real source/output captures for each
device and scene. Select a captured study, move the comparison slider, or inspect its detail crop.
The current v2 workload, input pixels, dimensions, repetition rules, and score formula are unchanged.
The three procedural studies cover compute (512×512), tiled processing (3072×2048), and export
(768×512). These are technical texture studies rather than photos or a perceptual quality ranking.

Each preview comes from the existing unmeasured per-scene warm-up. Thumbnail creation and transport
occur before the measured loop, so preview work is excluded from inference timings. Captures are
bounded to 640 pixels per side, emitted once per scene/device, and retained with the latest local
result and JSON export. They are scaled previews, not full-resolution exports. Old saved results
without captures remain readable. Cancellation discards the current captures and returns to the
last completed result; an in-progress run does not display a previous run's score as its own.

The separate [video benchmark](video-support.md#visual-local-benchmark) renders actual video files
through the local production pipeline and produces a browsable report with playback and timing
checks. It makes no hardware score or SeedVR2 inference claim.

## Legacy single-device benchmark (v1)

`localsr-benchmark-v1` is a deterministic, local workload for comparing one LocalSR installation
over time. It is not a universal hardware ranking and results are comparable only when the workload
version is identical.

The workload creates a code-generated 128×128 RGB pattern, uses the checksum-verified
`span_photo_x4` Quick checkpoint at native 4× scale, FP32, 128-pixel tiles, and a 16-pixel halo. It
runs one unmeasured warm-up and five measured frames through the same `InferenceEngine.process_frame`
path used for production frame-by-frame video. No user media or benchmark result leaves the device.

The result records the backend/device, model and scale, dimensions, warm-up/measured counts, median
and p95 inference time, end-to-end frames per second, processed input megapixels per second, total
elapsed time, best-effort peak process RSS, and workload version. The transparent score is:

```text
score = end-to-end processed input megapixels per second × 1000
```

Higher is better only for `localsr-benchmark-v1`. The GUI can cancel the run, copy its JSON, export
it atomically, and keeps only the latest result in LocalSR's application state. The CLI equivalent
is:

```bash
localsr benchmark --device auto --json
```

The benchmark and production jobs share one worker queue and never execute concurrently.
