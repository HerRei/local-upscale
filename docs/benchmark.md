# LocalSR benchmark

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
