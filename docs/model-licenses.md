# Curated checkpoint provenance and license evidence

LocalSR is an MIT application harness. It does not bundle or relicense model weights. The catalog
offers pinned upstream downloads only where the repository records a defensible checkpoint path;
otherwise it accepts an exact user-supplied file after size and SHA-256 verification.

## v0.0.11 additions

### YuNet face detector (`yunet_face_detector_2023mar`)

- Exact upstream asset: `face_detection_yunet_2023mar.onnx` from the official
  [OpenCV Zoo YuNet directory](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet).
- Size: `232,589` bytes.
- SHA-256: `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`.
- Code/checkpoint terms: the model directory's
  [MIT license](https://github.com/opencv/opencv_zoo/blob/main/models/face_detection_yunet/LICENSE)
  expressly covers every file in that directory. The headless OpenCV runtime is Apache-2.0.
- Policy: this is a detector, not a selectable restoration model. It is downloaded on first
  face-aware use through the same HTTPS, size, digest, and atomic-install checks as catalog models.

### Real-ESRGAN ×2 (`realesrgan_x2plus`)

- Exact upstream asset: `RealESRGAN_x2plus.pth`, official
  [Real-ESRGAN `v0.2.1` release](https://github.com/xinntao/Real-ESRGAN/releases/tag/v0.2.1).
- Size: `67,061,725` bytes.
- SHA-256: `49fafd45f8fd7aa8d31ab2a22d14d91b536c34494a5cfe31eb5d89c2fa266abb`.
- Code/project terms: [BSD-3-Clause in the official repository](https://github.com/xinntao/Real-ESRGAN/blob/master/LICENSE).
- Weight evidence: the official release explicitly introduces and distributes the named model;
  there are no separate asset terms on that release. LocalSR therefore applies the repository's
  project license to this official project asset, preserves attribution, and does not mirror it.
- Selection evidence: the 67,061,725-byte asset was downloaded from that release, verified against
  the catalog digest, loaded by the release's Spandrel version as `ESRGAN`, and produced a finite
  32×32 result from a 16×16 input at native 2× scale. ArtCNN, SeemoRe, and Real-CUGAN were not added
  to this intentionally small catalog because this release did not establish all three of an exact
  permissively licensed upstream checkpoint, a pinned digest, and a passing current-Spandrel load
  test for them. This is a release-scope decision, not a claim that those projects are unsuitable.

### FBCNN Color (`fbcnn_color`)

- Exact upstream asset: `fbcnn_color.pth`, official
  [FBCNN `v1.0` model-zoo release](https://github.com/jiaxi-jiang/FBCNN/releases/tag/v1.0).
- Size: `287,755,111` bytes.
- SHA-256: `8b0e4ef23d59cf7ac934a342cb31a17619e4fa4a0b3374a9d78c5174312387e8`.
- Code/project terms: [Apache-2.0](https://github.com/jiaxi-jiang/FBCNN/blob/main/LICENSE); the
  official README states that the project is released under Apache-2.0.
- Weight evidence: the same official project publishes the exact file as a pretrained FBCNN model
  and gives no separate asset terms. LocalSR treats it as part of that Apache-2.0 project release,
  preserves notice/provenance, and does not mirror it.
- Compatibility evidence: the 287,755,111-byte asset was downloaded from the official release,
  verified against the catalog digest, loaded by Spandrel as `FBCNN`, and produced a finite 16×16
  restoration result from a 16×16 input. LocalSR uses the checkpoint's own blind quality-factor
  prediction and does not invent a model strength control.

### HAT-S Face (`hat_s_x4_face`)

- Exact file: `base_95k_interp_a0p1.pth` from the
  [HAT-S Face asset release](https://github.com/HerRei/HAT/releases/tag/v1.0.0-face-interp).
- Size: `40,484,805` bytes.
- SHA-256: `92277daf002214307bea6f1e06b4fa745acdb7690728a0a9a619076e7bc8d7f2`.
- The HAT implementation has Apache-2.0 source terms, but neither the asset release nor the private
  fine-tuning repository establishes independent redistribution/use rights for this checkpoint and
  its training data.
- Policy: rights unresolved; no trusted automatic download and no commercial-use claim. A user may
  import only an exact matching checkpoint they are independently permitted to use.

### HAT-L Face (`hat_l_x4_face`)

- Exact file: `hat_l_x4_face_task4.pth`, produced by the private HAT-L face fine-tune
  (Task 4 selection pipeline, September 2026).
- Size: `165,676,233` bytes.
- SHA-256: `8a5548208310fcc7195abd4e1cf17ed87faaf3e5c38e2edeb63a45ac5b9c2af4`.
- Provenance: a linear weight interpolation at α = 0.25 (75% ImageNet-pretrained base HAT-L,
  25% face-tuned final checkpoint of refinement run 3). The face tuning was L1-only (no GAN, no
  perceptual loss) on a research-only face corpus; the interpolation back toward the base keeps
  general-image fidelity close to stock HAT-L.
- Evaluation summary (512-pair recovery buckets, Y-PSNR): clean 32.58 dB vs base 32.78
  (−0.19), mild 29.13 vs 28.69 (+0.44), hard 25.97 vs 25.72 (+0.25). It is a face-restoration
  model, not a general upgrade; on clean or non-face images stock HAT-L remains the better choice.
- Rights: same unresolved status as `hat_s_x4_face` — Apache-2.0 code terms, no independent
  checkpoint/training-data rights established. No trusted automatic download, no commercial-use
  claim; a user may import only an exact matching checkpoint they are independently permitted
  to use.

RestoreFormer was considered for this role, but no exact checkpoint with independently verified
redistribution and training-data terms was established for this release. It is therefore not in the
trusted auto-download catalog. This avoids presenting source-code licensing as if it also proved
checkpoint rights.

The Best/anime release pages still use the nonstandard identifier `CC-BY-0.4`; those entries remain
Labs with unverified commercial permission. The desktop preview allows a direct publisher download
after two separate acknowledgements: read the ambiguous terms, then restrict use to personal,
non-commercial research until rights are clarified. A reminder appears whenever either checkpoint
is selected; stock HAT-S (Apache-2.0) is offered as the alternative. The download does not resolve
license ambiguity or authorize redistribution. Face checkpoints still require verified manual import.
