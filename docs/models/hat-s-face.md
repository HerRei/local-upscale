# HAT-S ×4 Face — Restoration (`hat_s_x4_face`)

An optional face-restoration companion to stock HAT-S. Use stock HAT for general
images; this research checkpoint trades clean-image fidelity for degraded-face recovery.

## Exact checkpoint

- File: `base_95k_interp_a0p1.pth`
- Size: 40,484,805 bytes (40.48 MB)
- SHA-256: `92277daf002214307bea6f1e06b4fa745acdb7690728a0a9a619076e7bc8d7f2`
- Blend: 90% stock HAT-S + 10% face-tuned 95k checkpoint (α = 0.10).
- Source: [HAT-S recovery report](https://github.com/HerRei/HAT/blob/main/FACE_SR_SALVAGE_REPORT.md).

## Reported recovery evidence

The HAT-S report evaluates 512 held-out faces in deterministic degradation buckets:

| Bucket | Stock HAT-S PSNR | Selected PSNR | Difference |
| --- | ---: | ---: | ---: |
| Clean | 32.59 dB | 31.97 dB | −0.62 dB |
| Mild | 28.70 dB | 28.83 dB | +0.13 dB |
| Hard | 25.78 dB | 25.86 dB | +0.08 dB |

Hard-bucket SSIM rises from 0.6842 to 0.7012. The original GAN/perceptual face
fine-tune regressed badly on clean inputs; interpolation recovers a useful compromise.
A PSNR ratio is not a percentage of preserved fidelity, and these measurements do
not establish zero hallucination, identity preservation, or performance on arbitrary video.
The HAT-S and HAT-L reports are separate experiments, not a controlled size comparison.

## Use in LocalSR

In Tauri, choose stock HAT-S, enable its face-aware pass, acknowledge the terms,
and import the exact checkpoint. LocalSR verifies its size and SHA-256. The local
OpenCV face detector must be available. The Slint frontend also permits direct
selection and automatically pairs an installed companion with stock HAT-S.

Both face checkpoints remain Labs, with no trusted automatic download and no
commercial-use claim. The pre-existing HAT-S release is a provenance reference;
its existence does not establish checkpoint or training-data rights. Import a copy
only where you are independently permitted to use it. See [license notes](../model-licenses.md)
and the [HAT-L model card](hat-l-face.md).
