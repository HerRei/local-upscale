# PULSE vendored code — attribution

This directory contains the PULSE inference network and a Spandrel loader for
it, so that LocalSR's own models (`4xLocalSRPhoto_PULSE-64x8` and
`4xLocalSRPhoto_PULSE-96x12`) as well as users' own PULSE checkpoints load cleanly.

## `arch.py`: the network (MIT)

- Source: [HerRei/localsr-models](https://github.com/HerRei/localsr-models),
  release `v1` (`pulse_arch.py`), MIT License (reproduced below).
- Specification credit: the network layout follows Philip Hofmann's
  [PULSE](https://huggingface.co/Phips/PULSE) specification (MIT). `arch.py` is
  an independent reimplementation, and the weights in `localsr-models` come
  from LocalSR's own training runs, not Philip Hofmann's checkpoints.
- Modifications by LocalSR:
  - Reformatted with ruff (`line-length = 100`). No functional changes to network
    layers, buffers, or checkpoint keys.

## `loader.py`: detection and hyperparameters (MIT)

- Written for LocalSR to detect PULSE state dicts and infer `dim`, `n_blocks`,
  and `scale` for Spandrel's `ImageModelDescriptor`.

## Weights and training data

Weights are not bundled in the application package. The catalog downloads the
pinned `4xLocalSRPhoto_PULSE-64x8.safetensors` and `4xLocalSRPhoto_PULSE-96x12.safetensors`
files from `HerRei/localsr-models` release `v1` (weights CC BY 4.0, see `LICENSE-WEIGHTS` there) with size and SHA-256
verification, and users may also load their own PULSE `.safetensors`
checkpoints.

- Training data: Philip Hofmann's
  [`lucid-cc0-v2-hc`](https://huggingface.co/datasets/Phips/lucid-cc0-v2-hc)
  (CC0 photographs published on pxhere).
- DINOv2 (Apache-2.0) was used as a training loss only and is not part of the
  shipped weights.

## MIT License (`HerRei/localsr-models`)

```
MIT License

Copyright (c) 2026 Hermes Reisner

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OF THIS SOFTWARE,
OUT OF OR IN CONNECTION WITH THE SOFTWARE.
```
