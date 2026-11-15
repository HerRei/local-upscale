# Model export pipeline

`export_models.py` converts the two LocalSR checkpoints into the files the app bundles
(`mobile/Models/`) and checks them against PyTorch FP32. Design background: `../DESIGN.md`
("Models", "Device check").

## Run

Use the model-library interpreter (torch 2.14, coremltools 9.0, spandrel 0.4.2). The sources are
read only: checkpoints in `localsr-train/candidates`, `prepare_export_model` from
`LocalSR-038-integ/src` (override with `LOCALSR_TRAIN` / `LOCALSR_SRC`).

```
PY=/Users/hermesheiniger/LocalSR-model-library/.venv/bin/python
$PY mobile/tools/export_models.py            # convert, write mobile/Models/ (about 30 s)
$PY mobile/tools/export_models.py --check    # numeric check, exit code 1 if a CPU path < 55 dB
```

LiteRT conversion needs its own torch, so it runs in `mobile/tools/.venv-litert` (Python 3.11).
The script creates it on first use (`uv venv --python 3.11`, `uv pip install litert-torch
spandrel==0.4.2`) and calls itself there. Installed: litert-torch 0.10.0, ai-edge-litert 2.3.0,
ai-edge-quantizer 0.10.1. The venv is about 1 GB and not part of the deliverable.

## Output (`mobile/Models/`)

| File | Content |
|---|---|
| `ios/localsr_{best,fast}.mlmodelc` | Core ML ML Program, iOS17, FP16 compute, FP16 `image` [1,3,384,384] -> FP16 `out` [1,3,1536,1536], compiled with `xcrun coremlcompiler` |
| `android/localsr_{best,fast}.tflite` | LiteRT, FP16 weights (weight-only FLOAT_CASTING), NHWC float32 [1,384,384,3] -> [1,1536,1536,3] |
| `probe.png` | deterministic 384x384 RGB probe tile for the on-device check |
| `NOTICE.txt` | PULSE-48 notice + MIT text, SPAN-28 info |
| `manifest.json` | file names, sha256 (a `.mlmodelc` is hashed as a tree), shapes, layout, source checkpoint sha256, tool versions |

Android path: `litert_torch.to_channel_last_io(model, args=[0], outputs=[0])` -> `litert_torch.convert`
-> FP32 `.tflite` -> ai-edge-quantizer recipe `add_weight_only_config(num_bits=16,
algorithm_key=FLOAT_CASTING)`. PixelShuffle comes out in the right channel order (144 dB for the
FP32 model before the FP16 step).

## Check (this Mac, M1 Pro, probe.png, reference = PyTorch FP32 on CPU)

Thresholds: CPU paths >= 55 dB (fail otherwise), ANE >= 45 dB (the app's device-check bar), GPU
reported only. ms/tile is the median of 3-5 runs after a warm-up; LiteRT uses 4 CPU threads (XNNPACK).

```
model format   unit    PSNR dB   ms/tile   size KB  status
----------------------------------------------------------
best  Core ML  CPU       64.91      49.8       726  ok
best  Core ML  ANE       71.77      24.0       726  ok
best  Core ML  GPU       73.07      32.2       726  info
best  LiteRT   CPU       77.24     393.5       725  ok
fast  Core ML  CPU       57.66      42.7       342  ok
fast  Core ML  ANE       61.21      15.2       342  ok
fast  Core ML  GPU       71.93      18.8       342  info
fast  LiteRT   CPU       78.49     211.7       343  ok
```

The Core ML check also asserts the compiled model's input/output are Float16 with the expected
shapes (read from `metadata.json`; the Python API returns FP16 outputs as float32). The TFLite
check asserts float32 NHWC I/O and that FP16 weight tensors are present.
