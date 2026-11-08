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

## Training and evaluation

The official HAT-L ×4 ImageNet-pretrained checkpoint (XPixel Group, Apache-2.0
code terms) was fine-tuned with L1 loss on a research-only corpus of FFHQ, EFHQ
and general high-resolution images. The selected checkpoint combines 75% stock
HAT-L weights with 25% face-tuned weights (α = 0.25).

Evaluation on 512-image clean/mild/hard recovery buckets reported these PSNR
values against the stock HAT-L base:

| Bucket | base HAT-L | HAT-L Face (α 0.25) | Δ |
|---|---:|---:|---:|
| clean (undamaged) | 32.78 dB | 32.58 dB | −0.19 dB |
| mild degradation | 28.69 dB | 29.13 dB | **+0.44 dB** |
| hard degradation | 25.72 dB | 25.97 dB | **+0.25 dB** |

Deltas were reported from unrounded values, so subtracting the displayed clean
scores gives −0.20 dB.
These selection-set measurements are not an independent real-world or video benchmark.
SSIM and ArcFace were evaluated, but numeric results are not reproduced here.

## How LocalSR uses it

The model is loaded through the same Spandrel `HAT` architecture path as stock HAT-L with
identical tiling/halo requirements (`recommended_halo` 16, ×4 native scale). It is listed
in the catalog with `FACE` and `PHOTO` purposes and is **not auto-downloaded** (see
policy below); a user imports the exact file, LocalSR verifies the SHA-256, and the model
becomes available as a companion. In the app, select stock HAT-L as the
primary checkpoint, enable its face-aware pass, acknowledge the checkpoint terms,
and choose the exact external file. Face-aware processing requires the local OpenCV
detector runtime. Face checkpoints are companions, not primary choices.

## Limitations and trade-offs

- The evaluated clean-face bucket loses about 0.2 dB against stock HAT-L.
  Non-face photographic quality is not established by these face-bucket scores.
- Training data rights are unresolved (research-only corpus), so the checkpoint is
  **not** redistributed or auto-downloaded, and no commercial-use claim is made. Users may
  import it only where they are independently permitted to use it. See
  [checkpoint license notes](../model-licenses.md).
- L1-only training tends toward smoother results; fine skin/hair texture and identity
  can still be wrong.
- Like all HAT-L models it is slow and memory-hungry (~6 GB VRAM class); it is the
  large sibling of the lighter HAT-S Face model. These reports do not establish a
  controlled head-to-head comparison between the two variants.

## Availability

The checkpoint is not published. The catalog reserves an asset URL for it, but LocalSR never
downloads from that URL; the only way to use the model is to import an exact matching file that
you are independently permitted to use.
