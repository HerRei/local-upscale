# Model licenses and provenance

LocalSR is an MIT application harness. It does not bundle or relicense model weights. The catalog
offers pinned upstream downloads only where the repository records a defensible checkpoint path;
otherwise it accepts an exact user-supplied file after size and SHA-256 verification.

## Detector and restoration checkpoints

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

### Real-ESRGAN ×4 (`realesrgan_x4plus`) and ×4 Anime 6B (`realesrgan_x4plus_anime_6b`)

- Exact upstream assets: `RealESRGAN_x4plus.pth` from the official
  [Real-ESRGAN `v0.1.0` release](https://github.com/xinntao/Real-ESRGAN/releases/tag/v0.1.0)
  and `RealESRGAN_x4plus_anime_6B.pth` from the
  [`v0.2.2.4` release](https://github.com/xinntao/Real-ESRGAN/releases/tag/v0.2.2.4).
- Sizes: `67,040,989` and `17,938,799` bytes.
- SHA-256: `4fa0d38905f75ac06eb49a7951b426670021be3018265fd191d2125df9d682f1` and
  `f872d837d3c90ed2e05227bed711af5671a6fd1c9f7d7e91c911a61f155e99da`.
- Code/project terms: [BSD-3-Clause](https://github.com/xinntao/Real-ESRGAN/blob/master/LICENSE),
  the same route already accepted for the official ×2 asset above; the releases distribute the
  named files with no separate asset terms.
- Selection evidence (15 September 2026): both assets were downloaded from those releases,
  hashed, loaded by Spandrel 0.4.2 as `ESRGAN` (16.7M and 4.5M parameters) and produced finite
  256×256 output from a 64×64 input on CPU.

### SwinIR-M real-world ×4 (`swinir_m_real_x4_gan`)

- Exact upstream asset: `003_realSR_BSRGAN_DFO_s64w8_SwinIR-M_x4_GAN.pth` from the official
  [SwinIR `v0.0` model release](https://github.com/JingyunLiang/SwinIR/releases/tag/v0.0).
- Size: `67,129,861` bytes.
- SHA-256: `b9afb61e65e04eb7f8aba5095d070bbe9af28df76acd0c9405aeb33b814bcfc6`.
- Code/project terms: [Apache-2.0](https://github.com/JingyunLiang/SwinIR/blob/main/LICENSE);
  the official repository publishes the pretrained models in that release with no separate terms.
- Selection evidence (15 September 2026): downloaded from that release, hashed, loaded by Spandrel
  0.4.2 as `SwinIR` (11.7M parameters), finite 256×256 output from 64×64 on CPU.

### SCUNet colour, real-image PSNR (`scunet_color_real_psnr`)

- Exact upstream asset: `scunet_color_real_psnr.pth`, published by the SCUNet author in the
  [KAIR `v1.0` model release](https://github.com/cszn/KAIR/releases/tag/v1.0) that the
  [SCUNet README](https://github.com/cszn/SCUNet) links as the download location.
- Size: `71,982,841` bytes.
- SHA-256: `fa78899ba2caec9d235a900e91d96c689da71c42029230c2028b00f09f809c2e`.
- Code/project terms: SCUNet is [Apache-2.0](https://github.com/cszn/SCUNet/blob/main/LICENSE);
  the hosting KAIR repository is MIT. Both are permissive and by the same author; LocalSR records
  Apache-2.0 for the model and preserves attribution.
- Selection evidence (15 September 2026): downloaded from that release, hashed, loaded by Spandrel
  0.4.2 as `SCUNet` (17.9M parameters), finite 64×64 output at native 1× on CPU. The GAN variant
  was not added: same license and size, but it invents texture and duplicates the slot.

### HAT-S Face (`hat_s_x4_face`)

- Exact file: `base_95k_interp_a0p1.pth`, produced by the private HAT-S face fine-tune. Its
  public release asset was withdrawn on 17 September 2026 because the training-data rights are
  unresolved; the catalog keeps only its size and digest so a user-supplied copy can be verified.
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

### HAT-S ×4 and HAT-L ×4 ImageNet (`hat_s_x4`, `hat_l_x4_imagenet`)

- Official checkpoints: `HAT-S_SRx4.pth` and `HAT-L_SRx4_ImageNet-pretrain.pth` from the
  XPixel Group's [HAT repository](https://github.com/XPixelGroup/HAT), which publishes them in
  a Google Drive folder that cannot serve pinned, scriptable downloads.
- Download route: an unchanged copy in the Hugging Face repository
  [`jaideepsingh/upscale_models`](https://huggingface.co/jaideepsingh/upscale_models), pinned to
  commit `867e7c0ad519aba5d36b5bb4ef4a4a91781914c4`. The mirror is not the author's release; the
  pin and the digest check are what make it trustworthy.
- Sizes: `81,089,561` and `165,774,123` bytes.
- SHA-256: `a92f81bd2c0c1aaa371a6e4d6cac69e749fde2e36196885ee47a4a3667542c9a` and
  `5992bd38522f2b8faf11ea4bd8ee08de92465bb66892166576999afc36d60043`. Both match the size and
  digest [OpenModelDB](https://openmodeldb.info/) records for the official Google Drive files
  (checked 17 September 2026), so the mirrored bytes are the official checkpoints.
- Terms: [Apache-2.0](https://github.com/XPixelGroup/HAT/blob/main/LICENSE); the official
  repository publishes the pretrained models with no separate asset terms.

### SPAN NomosUni and RealPLKSR Denoise (`span_photo_x4`, `denoise_realplksr_1x`)

- Exact assets: `4xNomosUni_span_multijpg.pth` and `1xDeNoise_realplksr_otf.pth` from Philip
  Hofmann's own [models releases](https://github.com/Phhofm/models/releases) on GitHub.
- Sizes: `4,546,346` and `29,559,554` bytes.
- SHA-256: `3a9037c36de90e7825c030176c8e193dfd7897ef4b5df91b8bdc59ffb6ab65ca` and
  `f4774fbe13ceaa9df390343c2baaa980061458ef27292fa6aca6740d87608a8e`.
- Terms: CC BY 4.0. The NomosUni release states "License: CC BY 4.0". The Denoise release writes
  "CC-BY-0.4", and the author's
  [Hugging Face card](https://huggingface.co/Phips/1xDeNoise_realplksr_otf) for the same model
  declares `cc-by-4.0`, which LocalSR records.

### NAFNet SIDD and GoPro (`nafnet_sidd_width64`, `nafnet_gopro_deblur`)

- Official checkpoints: `NAFNet-SIDD-width64.pth` and `NAFNet-GoPro-width64.pth` from
  [megvii-research/NAFNet](https://github.com/megvii-research/NAFNet) (MIT), which links them on
  Google Drive and Baidu.
- SIDD route: the Hugging Face Space
  [`chuxiaojie/NAFNet`](https://huggingface.co/spaces/chuxiaojie/NAFNet) run by co-author Xiaojie
  Chu, pinned to commit `5964ed4955416df99210106b708e4a2df9e9eca0`; `464,154,961` bytes,
  SHA-256 `cd685efaae01f7c4e9951f2deab05780079c8eb1e49ed664b72f6db04dabb445`.
- GoPro route: the third-party Hugging Face mirror
  [`mikestealth/nafnet-models`](https://huggingface.co/mikestealth/nafnet-models), pinned to commit
  `9526c38b626f6e8ca0c02e4a282859ac84d240a2`; `271,778,961` bytes, SHA-256
  `329d3ab4077b8d6b7ff61de376e483714667960bf85be027bf4335cda701196f`. No official digest exists to
  compare against. The mirror's `NAFNet-REDS-width64.pth` is byte-identical to the co-author's copy,
  which suggests unchanged files, but that is supporting evidence, not proof. A direct copy from the
  official Drive folder should replace this route when one can be pinned.

### NomosWebPhoto and HFA2k (`realplksr_nomoswebphoto_x4`, `realplksr_hfa2k_anime_x4`)

Both are by Philip Hofmann (Phips / Phhofm), who declares them under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) on the
[NomosWebPhoto](https://huggingface.co/Phips/4xNomosWebPhoto_RealPLKSR) and
[HFA2k](https://huggingface.co/Phips/4xHFA2k_ludvae_realplksr_dysample) model cards; his
[models README](https://github.com/Phhofm/models#models) welcomes application downloads.
LocalSR downloads the author's unchanged checkpoint files with the same size and SHA-256
verification as every other model, credits the author and links the source and license in the
app. HFA2k's `.pth` hash matches the catalog, and all 340 NomosWebPhoto tensors match the
author's licensed conversion. CC BY 4.0 permits sharing and commercial use with attribution and
retained notices; it does not imply the author's endorsement of LocalSR.

### DAT2 BHI Real and RealWebPhoto v4 (`bhi_dat2_real_x4`, `realwebphoto_v4_dat2_x4`)

- Exact assets: `4xBHI_dat2_real.pth` and `4xRealWebPhoto_v4_dat2.pth` from Philip Hofmann's own
  [models releases](https://github.com/Phhofm/models/releases) on GitHub, both re-downloaded and
  hashed on 2026-11-08.
- Sizes: `140,196,334` and `140,299,118` bytes.
- SHA-256: `14d85e754935332e289d04ab9c29ca2ebaf33dcc8ead4a637d92fe3fbbaaa0b0` and
  `9b82f8f60826e59b9d2e56a6e6ad9a842b84d57a9ec5ed7377bf120032b2c4c9`.
- Terms: CC BY 4.0, declared on both GitHub releases and on the author's Hugging Face cards
  ([BHI Real](https://huggingface.co/Phips/4xBHI_dat2_real),
  [RealWebPhoto v4](https://huggingface.co/Phips/4xRealWebPhoto_v4_dat2)). The DAT architecture
  is Apache-2.0 ([zhengchen1999/DAT](https://github.com/zhengchen1999/DAT)).
- Training data, recorded because it differs between the two: RealWebPhoto v4 was trained on
  Nomos8k (stock photos). BHI Real was trained on the author's
  [BHI set](https://huggingface.co/datasets/Phips/BHI), filtered tiles from thirteen sources
  including FFHQ (whose dataset terms are CC BY-NC-SA 4.0, with images under mixed licences),
  ImageNet, DF2K, COCO and iNaturalist 2019. As for the other checkpoints in this catalogue, which
  include models trained on DF2K and ImageNet, LocalSR follows the license the author grants for
  the weights and lists the training data here.

Both are library models: no preset picks them on its own (`preset_eligible=False`), because
they take about ten times as long as Best. A model pinned to Best by the user is still used.

The two HAT face checkpoints stay outside this policy and require a verified manual import.
No restoration weights are bundled with the application.

## Frame interpolation

### RIFE 4.26 (`rife_4_26`)

- Model: Practical-RIFE v4.26 by hzwer ([hzwer/Practical-RIFE](https://github.com/hzwer/Practical-RIFE)),
  described in Huang et al., ECCV 2022. The author recommends 4.25 for most scenes;
  LocalSR selected 4.26 based on the synthetic comparison in [video support](video-support.md).
- Terms: the project is [MIT](https://github.com/hzwer/Practical-RIFE/blob/main/LICENSE), and its
  README states that linked trained models are under the same MIT license. The vendored code and
  MIT text are in `src/localsr/video_models/rife/NOTICE.md`.
- Official copy: `train_log/flownet.pkl` in the v4.26 archive linked by the README on Google Drive
  (file `1gViYvvQrtETBgU1w8axZSsr7YUuw31uy`).
- Download: `flownet_v4.26.pkl` from [HolyWu/vs-rife's model release](https://github.com/HolyWu/vs-rife/releases/tag/model).
  On 2026-11-09 its bytes matched both the official Drive archive and hzwer's
  [Hugging Face zip](https://huggingface.co/hzwer/RIFE); installation checks size and SHA-256.
- Size: `24,636,301` bytes; SHA-256: `45c7f74156704769dc9f85cfcaf8552e1e926f9399dcfa3a553dee88fac6f53f`.
- Load: tensors only (`torch.load(weights_only=True)`); training-only `teacher` tensors are
  ignored, and every network tensor must match.
- Training data: not stated by the README; the RIFE paper trains on Vimeo-90K, whose terms are
  not published on its [project page](http://toflow.csail.mit.edu/). LocalSR follows the
  license the author grants for the weights.
- Alternatives checked: v4.25 (`6615790efd627772917205db291f51cd392528a157ecbb2ecaeec3bff8eb6de2`)
  and v4.25 lite (`81cdba223fe72a120130cc8552e5d2ecac824259d406f0c15323b3decf96b8b1`),
  each matched to the official Drive archive. The unofficial "heavy" files were excluded.
  GMFSS was excluded because its forward-warping kernel is academic-use only.

## Image-editing models

The Edit task's bundles are listed in `src/localsr/core/edit_catalog.json` with the exact
upstream revision, byte size and SHA-256 of every component; [Image editing](image-editing.md)
describes the bundles and what they need.

- **Qwen Image Edit 2511** — [Qwen](https://huggingface.co/Qwen/Qwen-Image-Edit-2511),
  Apache-2.0, with GGUF diffusion quantizations by Unsloth, the matching Qwen2.5-VL text and
  vision encoders and the Qwen Image VAE.
- **FLUX.2 klein 4B** — [Black Forest Labs](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B),
  Apache-2.0, with GGUF diffusion quantizations by leejet, the Qwen3-4B text encoder (Qwen,
  Apache-2.0, GGUF by Unsloth) and the FLUX.2 autoencoder as published by Comfy-Org (Apache-2.0).
- **Qwen Image 2.1** is not offered: its Qwen Research License permits research and evaluation
  only.

Editing weights are optional downloads, verified like every other model and never included in
installers.
