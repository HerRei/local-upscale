# HAT-L ×4 Face — Restoration (`hat_l_x4_face`)

Model card for the face-specialized HAT-L checkpoint supported as an optional, user-supplied
model in LocalSR. Catalog id: `hat_l_x4_face`, file: `hat_l_x4_face_task4.pth`,
SHA-256: `8a5548208310fcc7195abd4e1cf17ed87faaf3e5c38e2edeb63a45ac5b9c2af4`.

## Intended use

Restoring degraded, low-resolution, or compressed **face** photographs at 4× scale — for
example old family photos, web-compressed portraits, or noisy low-light face crops. It
trades a small amount of clean-image fidelity for measured gains on degraded
face validation crops. It is **not** a general-purpose upgrade: for clean or non-face images, stock
HAT-L (`hat_l_x4_imagenet`) remains the better default, and the two models are paired in
the catalog (`pair_with`) so the UI can present them as alternatives.

## What was trained

The official HAT-L ×4 ImageNet-pretrained checkpoint (XPixel Group, Apache-2.0 code terms)
was fine-tuned on a research-only face corpus (60% FFHQ, 20% identity/clip-balanced EFHQ,
20% general high-resolution replay). The fine-tune deliberately repeated none of the
mistakes of an earlier failed HAT-S face run (which used GAN + VGG perceptual loss at a high
learning rate and regressed 5 dB on clean images):

- **Loss:** L1 only — no GAN, no perceptual loss. This does not guarantee accurate
  reconstruction of missing detail or preservation of identity.
- **Learning rate:** 1e-5 for the main run, 2.5e-6 for a low-LR refinement pass.
- **Stabilizers:** EMA 0.999, gradient clipping 1.0, 15% clean-replay batches,
  high-order degradation pipeline with Poisson noise and double JPEG for damage realism.
- **Schedule:** three stages — a 250k-iteration "holiday" fidelity run, a 250k-iteration
  256px continuation (batch 4), and a 299k-iteration refinement run at 1/4 learning rate.

## How the selected checkpoint was chosen

Training plateaued: all three runs converged into a ~0.1 dB validation band, so the final
checkpoint is *not* simply the last or best single run. Fourteen candidates were evaluated
on 512-image clean/mild/hard recovery buckets (PSNR, SSIM, edge correlation, ArcFace face
identity) against the stock HAT-L base:

- the final checkpoints and 5-checkpoint uniform "soups" of each run,
- linear interpolations of the final face-tuned weights with the stock base at
  α = 0.1/0.25/0.5/0.8/0.9 (α = fraction of face-tuned weights).

The selection tool accepts a positive mild-bucket PSNR gain with a clean-bucket drop of
at most 0.62 dB by default. It ranks accepted candidates by mild gain, then clean PSNR,
then hard PSNR; hard improvement is an observed result, not an acceptance requirement. Raw face-tuned checkpoints win big on
degraded faces (mild +1.0 dB) but regress ~1.9 dB on clean images — unacceptable. The
selected compromise, **α = 0.25 (75% base + 25% face-tuned)**, measures:

| Bucket | base HAT-L | HAT-L Face (α 0.25) | Δ |
|---|---:|---:|---:|
| clean (undamaged) | 32.78 dB | 32.58 dB | −0.19 dB |
| mild degradation | 28.69 dB | 29.13 dB | **+0.44 dB** |
| hard degradation | 25.72 dB | 25.97 dB | **+0.25 dB** |

The values above are from the September 2026 Task 4 report; the original
`task4/selection.json` and per-bucket aggregates remain on DDP. Deltas were reported
from unrounded values, so subtracting the displayed clean scores gives −0.20 dB.
These selection-set measurements are not an independent real-world or video benchmark.
SSIM and ArcFace were evaluated, but numeric results are not reproduced here.

α = 0.1 is the conservative alternative (clean −0.03 dB, mild +0.19 dB) for users who
prioritize clean fidelity; α ≥ 0.5 was rejected for its clean-image cost.

## How LocalSR uses it

The model is loaded through the same Spandrel `HAT` architecture path as stock HAT-L with
identical tiling/halo requirements (`recommended_halo` 16, ×4 native scale). It is listed
in the catalog with `FACE` and `PHOTO` purposes and is **not auto-downloaded** (see
policy below); a user imports the exact file, LocalSR verifies the SHA-256, and the model
becomes available as a companion. In the Tauri desktop, select stock HAT-L as the
primary checkpoint, enable its face-aware pass, acknowledge the checkpoint terms,
and choose the exact external file. Face-aware processing requires the local OpenCV
detector runtime. Face checkpoints are companions, not primary choices in Tauri.

## Limitations and trade-offs

- The evaluated clean-face bucket loses about 0.2 dB against stock HAT-L.
  Non-face photographic quality is not established by these face-bucket scores.
- Training data rights are unresolved (research-only corpus), so the checkpoint is
  **not** redistributed or auto-downloaded, and no commercial-use claim is made. Users may
  import it only where they are independently permitted to use it. See
  [checkpoint license notes](../model-licenses.md).
- L1-only training tends toward smoother results; fine skin/hair texture and identity
  can still be wrong. The proposed VGG perceptual detail stage was not launched.
  Any future stage needs a separate reviewed decision and evaluation.
- Like all HAT-L models it is slow and memory-hungry (~6 GB VRAM class); it is the
  large sibling of the lighter HAT-S Face model. These reports do not establish a
  controlled head-to-head comparison between the two variants.

## Availability

The LocalSR integration is prepared on `codex/hat-face-models` for a later release.
It does not change the ongoing v0.0.12-alpha release or its installer assets.
The catalog reserves a `v1.0.1-hat-l-face` asset URL, but that release does not exist
and is not an available download. The URL is never used for automatic acquisition.
No HAT-L weights are published by this integration; the established rights policy remains.
