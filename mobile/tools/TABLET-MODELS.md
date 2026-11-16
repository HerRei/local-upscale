# Tablet model conversion results

Measured on M1 Pro, 2026-11-15. Sizes are MiB (1024² bytes), including the compiled Core ML directory. Times are median milliseconds per fixed window after warm-up (3 runs); LiteRT CPU uses 4 threads. PSNR is against the deterministic FP32 PyTorch probe, without clipping; each listed lane passed ≥45 dB. The Core ML labels ANE and GPU mean CPU_AND_NE and CPU_AND_GPU; they do not guarantee exclusive placement. These are Mac measurements, not iPad or Android tablet benchmarks.

| Model / fixed window | Format | Size MiB | ANE ms / dB | GPU ms / dB | CPU ms / dB | Recommended lane |
|---|---|---:|---:|---:|---:|---|
| PULSE-48 v1 (best) / 384 | Core ML FP16 | 0.71 | 23.9 / 71.87 | 33.3 / 73.14 | 47.2 / 65.03 | ANE |
| ↳ PULSE-48 v1 (best) | LiteRT FP16 weights | 0.71 | — | — | 398.6 / 77.25 | CPU checked |
| SPAN-28 v1 (fast) / 384 | Core ML FP16 | 0.33 | 17.3 / 61.34 | 20.3 / 72.16 | 43.9 / 57.69 | ANE |
| ↳ SPAN-28 v1 (fast) | LiteRT FP16 weights | 0.34 | — | — | 215.7 / 78.49 | CPU checked |
| 4× Nomos WebPhoto RealPLKSR / 256 | Core ML FP16 | 14.63 | 412.3 / 58.45 | 270.6 / 72.23 | 808.4 / 62.56 | GPU |
| ↳ 4× Nomos WebPhoto RealPLKSR | LiteRT FP16 weights | 18.83 | — | — | 4094.0 / 87.99 | CPU checked |
| 1× DeNoise RealPLKSR / 256 | Core ML FP16 | 14.58 | 411.0 / 70.24 | 263.3 / 76.07 | 802.3 / 66.82 | GPU |
| ↳ 1× DeNoise RealPLKSR | LiteRT FP16 weights | 18.77 | — | — | 4551.5 / 97.74 | CPU checked |
| FBCNN color / 256 | Core ML FP16 | 137.32 | 38.9 / 62.34 | 74.2 / 76.00 | 153.7 / 56.36 | ANE |
| ↳ FBCNN color | Core ML pal8 | 68.83 | 37.3 / 58.07 | 124.6 / 58.39 | 152.2 / 54.33 | ANE |
| ↳ FBCNN color | LiteRT FP16 weights | 137.39 | — | — | 1376.6 / 88.18 | CPU checked |
| RealESRGAN anime 6B / 384 | Core ML FP16 | 8.74 | 170.7 / 71.70 | 351.3 / 73.76 | 1264.4 / 50.40 | ANE |
| ↳ RealESRGAN anime 6B | LiteRT FP16 weights | 8.93 | — | — | 6661.2 / 79.98 | CPU checked |
| 4× NomosUni SPAN multijpg / 384 | Core ML FP16 | 0.86 | 30.8 / 46.83 | 39.2 / 71.67 | 69.5 / 51.81 | ANE |
| ↳ 4× NomosUni SPAN multijpg | LiteRT FP16 weights | 0.87 | — | — | 508.6 / 79.31 | CPU checked |
| 4× SPAN ch48 / 384 | Core ML FP16 | 0.86 | 29.7 / 49.66 | 39.2 / 70.78 | 72.9 / 53.53 | ANE |
| ↳ 4× SPAN ch48 | LiteRT FP16 weights | 0.87 | — | — | 530.4 / 78.63 | CPU checked |

Core ML total: **178.03 MiB** all FP16, or **109.53 MiB** substituting the FBCNN palette (the only FP16 compiled model above 20 MiB). Android FP16-weight total: **186.71 MiB**. These totals exclude app code and container overhead.

## MLComputePlan preferred placement

Counts are operations for which MLComputePlan returned a preferred device. Operations without a reported device are shown as `?`; they must not be assumed to execute on a particular unit. Placement is an estimate, and fused operations make counts an imperfect measure of work. Ordering below is CPU_AND_NE / CPU_AND_GPU / CPU_ONLY.

| Model | CPU_AND_NE | CPU_AND_GPU | CPU_ONLY |
|---|---|---|---|
| PULSE-48 v1 (best) | ? 119, ANE 40 | ? 119, GPU 40 | ? 119, CPU 40 |
| SPAN-28 v1 (fast) | ? 165, ANE 62 | ? 165, GPU 62 | ? 165, CPU 62 |
| 4× Nomos WebPhoto RealPLKSR | ? 1869, ANE 626, CPU 224 | ? 1869, GPU 850 | ? 1869, CPU 850 |
| 1× DeNoise RealPLKSR | ? 1863, ANE 620, CPU 224 | ? 1863, GPU 844 | ? 1863, CPU 844 |
| FBCNN color | ? 572, ANE 218 | ? 572, GPU 218 | ? 572, CPU 218 |
| RealESRGAN anime 6B | ? 756, ANE 276 | ? 756, GPU 276 | ? 756, CPU 276 |
| 4× NomosUni SPAN multijpg | ? 165, ANE 62 | ? 165, GPU 62 | ? 165, CPU 62 |
| 4× SPAN ch48 | ? 165, ANE 62 | ? 165, GPU 62 | ? 165, CPU 62 |

The palettized FBCNN plan has the same counts as its FP16 plan for each unit.

## Window and FBCNN decisions

SPAN, PULSE, and ESRGAN use a 384×384 fixed input; RealPLKSR and FBCNN use 256×256 to reduce activation memory. Both are 16-aligned fixed windows: the window includes the tile and both halos, so an app must choose tile + 2 × halo no larger than the window and pad as needed. The 256 choice has not been benchmarked against other sizes and is not a proven optimum.

FBCNN uses its own estimated quality factor, matching Spandrel’s standard image-only inference. There is no externally adjustable QF input; a user-selectable QF would require a separately traced interface and checks. The model returns the restored image only.

## Failures and regeneration

The single SCUNet Core ML attempt failed in coremltools 9.0: `ValueError: Op "attn_mask.1_internal_tensor_assign_1" (op_type: torch_tensor_assign) Input x="attn_mask.1" expects tensor or scalar of dtype from type domain ['fp16', 'fp32', 'int32'] but got tensor[32,32,8,8,8,8,bool]`. SCUNet is excluded. All eight listed models converted to LiteRT and passed the CPU probe.

```sh
PY=<model-library checkout>/.venv/bin/python
$PY mobile/tools/export_models.py --set tablet
$PY mobile/tools/export_models.py --set tablet --check
```

The check exits nonzero for a missing artifact, failed worker, non-finite output, or any measured lane below 45 dB. Results are generated one model per worker; intermediate packages are deleted. The model inputs are read from the locations documented in `tablet_models.py`. The experiment uses the desktop `prepare_export_model` and PULSE registration. Never use linear W8 for ANE: the optional variant uses 8-bit k-means palettization.
