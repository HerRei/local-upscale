# LocalSR benchmark

## Separate CPU / GPU benchmark (v2.1 beta candidate)

**Run Benchmark** lets you select CPU or one detected GPU, including the tested
Intel DirectML adapter. The worker runs exactly that device and rejects
an unavailable selection. It does not automatically append a CPU phase. Each
completed device score is retained locally, with its own confidence and timestamp;
a CPU run preserves the previous GPU result and vice versa. JSON separates the
current run's `device_results` from `device_history`, the most recent completed
result for each device using the same workload version. Cancellation/failure
preserves previous results. Older multi-device and v1 files remain readable.

On a fresh installation, choose **Download & run benchmark** to download and
verify the 4.3 MiB SPAN Quick checkpoint. The selected device starts once the
download succeeds. Download progress, startup errors, and any work that must
finish first are shown beside the button; failed or cancelled downloads do not
start a benchmark. Subsequent runs reuse the installed checkpoint.

The score is each device's geometric mean of output megapixels/second across the
three fixed SPAN scenes. CPU and GPU never contribute to a combined score. GPU
reference comparisons apply only to GPU results. Results above 5% timing spread
remain visibly unstable. Workload `localsr-benchmark-v2.1` retains the model,
scene dimensions and score formulas, but uses bounded sensor
noise in its third image region. The earlier full-range RGB noise made SPAN
produce extreme raw values that its descriptor hid by clipping. The revised
scenes complete through the same stability checks as normal image processing.

Each scene normally collects at least six measurements over 20 seconds. After
45 seconds it may finish with three complete measurements. The time budget is
soft: slower hardware must still collect those three samples, even when one
render exceeds 45 seconds. A single render cannot establish consistency. This
means a full benchmark can take several minutes; cancellation remains available.

The changed input pixels require a new workload identity. Earlier results remain
readable but are not merged with v2.1 CPU/GPU results or used as reference scores.

The viewer renders actual model squares, with an outline on the active tile. It
uses the same tile coordinates as HAT's production renderer, but the fixed model
is **SPAN**. Each square contains that tile's model output. The three
procedural scenes cover compute (512×512), tiled processing (3072×2048), and export
(768×512). They measure processing speed, not restoration quality.

Tile JPEGs come only from the unmeasured per-scene warm-up. Scored iterations have
no tile-preview callback or JPEG work. The viewer retains the warm-up render during
measurement and labels it accordingly. Preview canvases are bounded; the saved
full-scene proxy is at most 640 pixels per side. There is no simulated progress or
before/after slider in this benchmark viewer.

This is isolated preview work for a later release; it does not change the v0.0.12
release branch, tag, workflow, or installer claims.

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
