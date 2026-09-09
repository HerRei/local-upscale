# HAT-L ×4 Face — Restoration (`hat_l_x4_face`)

Model card for the face-specialized HAT-L checkpoint shipped as an optional, user-supplied
model in LocalSR. Catalog id: `hat_l_x4_face`, file: `hat_l_x4_face_task4.pth`,
SHA-256: `8a5548208310fcc7195abd4e1cf17ed87faaf3e5c38e2edeb63a45ac5b9c2af4`.

## Intended use

Restoring degraded, low-resolution, or compressed **face** photographs at 4× scale — for
example old family photos, web-compressed portraits, or noisy low-light face crops. It
trades a small amount of clean-image fidelity for noticeably better recovery on damaged
faces. It is **not** a general-purpose upgrade: for clean or non-face images, stock
HAT-L (`hat_l_x4_imagenet`) remains the better default, and the two models are paired in
the catalog (`pair_with`) so the UI can present them as alternatives.

## What was trained

The official HAT-L ×4 ImageNet-pretrained checkpoint (XPixel Group, Apache-2.0 code terms)
was fine-tuned on a research-only face corpus (60% FFHQ, 20% identity/clip-balanced EFHQ,
20% general high-resolution replay). The fine-tune deliberately repeated none of the
mistakes of an earlier failed HAT-S face run (which used GAN + VGG perceptual loss at a high
learning rate and regressed 5 dB on clean images):

- **Loss:** L1 only — no GAN, no perceptual loss. The model cannot hallucinate detail.
- **Learning rate:** 1e-5 for the main run, 2.5e-6 for a low-LR refinement pass.
- **Stabilizers:** EMA 0.999, gradient clipping 1.0, 15% clean-replay batches,
  high-order degradation pipeline with Poisson noise and double JPEG for damage realism.
- **Schedule:** three stages — a 250k-iteration "holiday" fidelity run, a 250k-iteration
  256px continuation (batch 4), and a 299k-iteration refinement run at 1/4 learning rate.

## How the shipped checkpoint was selected

Training plateaued: all three runs converged into a ~0.1 dB validation band, so the final
checkpoint is *not* simply the last or best single run. Fourteen candidates were evaluated
on 512-image clean/mild/hard recovery buckets (PSNR, SSIM, edge correlation, ArcFace face
identity) against the stock HAT-L base:

- the final checkpoints and 5-checkpoint uniform "soups" of each run,
- linear interpolations of the final face-tuned weights with the stock base at
  α = 0.1/0.25/0.5/0.8/0.9 (α = fraction of face-tuned weights).

The acceptance rule favored models that beat base on mild and hard buckets without losing
more than a small, bounded amount of clean fidelity. Raw face-tuned checkpoints win big on
degraded faces (mild +1.0 dB) but regress ~1.9 dB on clean images — unacceptable. The
selected compromise, **α = 0.25 (75% base + 25% face-tuned)**, measures:

| Bucket | base HAT-L | HAT-L Face (α 0.25) | Δ |
|---|---:|---:|---:|
| clean (undamaged) | 32.78 dB | 32.58 dB | −0.19 dB |
| mild degradation | 28.69 dB | 29.13 dB | **+0.44 dB** |
| hard degradation | 25.72 dB | 25.97 dB | **+0.25 dB** |

α = 0.1 is the conservative alternative (clean −0.03 dB, mild +0.19 dB) for users who
prioritize clean fidelity; α ≥ 0.5 was rejected for its clean-image cost.

## How LocalSR uses it

The model is loaded through the same Spandrel `HAT` architecture path as stock HAT-L with
identical tiling/halo requirements (`recommended_halo` 16, ×4 native scale). It is listed
in the catalog with `FACE` and `PHOTO` purposes and is **not auto-downloaded** (see
policy below); a user imports the exact file, LocalSR verifies the SHA-256, and the model
then appears alongside the other ×4 restoration models.

## Limitations and trade-offs

- On clean or non-face inputs it is up to ~0.2 dB worse than stock HAT-L; face-heavy
  general photos may also lose a little crispness relative to stock on undamaged regions.
- Training data rights are unresolved (research-only corpus), so the checkpoint is
  **not** redistributed or auto-downloaded, and no commercial-use claim is made. Users may
  import it only where they are independently permitted to use it. See
  [docs/model-licenses.md](model-licenses.md).
- L1-only training means no invented texture: heavily degraded faces recover structure
  but not fine skin/hair micro-texture. A future perceptual "detail" stage may improve
  this and would be selected through the same bucket evaluation.
- Like all HAT-L models it is slow and memory-hungry (~6 GB VRAM class); it is the
  large sibling of the lighter HAT-S Face model, with correspondingly better results on
  difficult inputs.