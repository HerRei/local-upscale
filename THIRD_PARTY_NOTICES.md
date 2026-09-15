# Third-party notices

LocalSR application source is MIT licensed. Dependencies and optional model downloads retain their
own terms; this file is a distribution notice, not a replacement for their license texts.

- **LocalSR Next Preview host** — Tauri 2 is MIT/Apache-2.0 and Svelte is MIT. Transitive
  components retain their upstream permissive or file-level terms (including Apache, BSD, ISC,
  MIT, MPL, Unicode, and Zlib terms), while their versions are locked by Cargo and npm. macOS uses WKWebView and Windows uses WebView2. The Linux AppImages bundle
  WebKitGTK/GTK/GStreamer libraries from Ubuntu 24.04; these include LGPL and other
  licenses and require the matching notices and applicable source/relinking materials.
- **PyTorch / TorchVision** — BSD-style licenses from the PyTorch project.
- **Windows ONNX path (prepared source)** — ONNX is Apache-2.0; ONNX Runtime is
  MIT and includes its own third-party notices. The prepared DirectML lock uses
  ONNX 1.22.0, ONNX Runtime DirectML 1.24.4 and maintained Torch 2.13.0 CPU for
  loading/conversion. DirectML/provider DLLs retain their shipped terms.
  `ml_dtypes` is Apache-2.0. The complete license/notice files and exact provider
  binaries must be retained in the frozen package. The older installed MSIX
  still uses torch-directml; these source changes do not describe its contents.
  See `docs/windows-inference-runtime-review.md` for numerical and adoption gates.
- **Spandrel** — MIT license.
- **OpenCV headless runtime** — OpenCV application code is Apache-2.0 and is used by
  the optional CPU face detector. Its binary wheels also contain third-party code.
  The upstream macOS 4.10 wheel includes a GPLv3-or-later FFmpeg 6.0 build;
  the Linux wheel includes an LGPLv2.1-or-later FFmpeg 5.1.4. The separately
  prepared macOS .13 alpha uses our source-built OpenCV without FFmpeg,
  camera SDKs or window-system backends. Its pinned source, small Python
  typing patch and build recipe accompany that download. Video processing
  remains in PyAV. This does not describe older installers.
- **PyAV / FFmpeg** — PyAV is BSD-3-Clause. The inspected PyAV 18.1.0 wheels use
  the upstream 8.1.2-1 FFmpeg build, with x264/x265 enabled. Its reported LGPLv3
  string does not by itself settle the encoder licensing: the upstream build
  patches FFmpeg's configure license lists. x264/x265 and other codec components
  retain their own terms. The macOS .13 packaged inference worker is conveyed
  under GPL-3.0-or-later for this combination; LocalSR's own files retain their
  MIT copyright/license notices. Corresponding source, codec build scripts,
  patches and license texts accompany the download. Linux's bundled GStreamer stack additionally contains
  Ubuntu FFmpeg 6.1.1, which reports GPLv2-or-later.
- **RAW images** — rawpy is MIT; the bundled LibRaw 0.22.1 is provided under
  LGPL-2.1-or-later. The macOS runtime also uses libjpeg-turbo, JasPer and
  LittleCMS under their retained upstream terms. The optional LibRaw GPL
  demosaic packs are disabled. Matching source and the upstream macOS build
  recipe accompany the .13 macOS source bundle.
- **Beta distribution status** — Exact library configurations, hashes and
  upstream license/build evidence are retained in `build/beta-review/licenses`
  and the multimedia inspection reports. Corresponding source, build instructions
  and the chosen distribution terms must accompany public delivery. The current
  review artifacts predate this expanded notice; their redistribution review is
  unfinished. See `docs/beta-dependency-review.md`.
- **YuNet 2023mar face detector** — MIT, copyright Shiqi Yu; downloaded on demand from the
  OpenCV Zoo with an exact size and SHA-256 rather than bundled in the application.
- **SeedVR2 video integration** — vendored adapter code is covered by the included
  `src/localsr/video_models/seedvr2/NOTICE.md` and `vendor/LICENSE`; model bundles remain external.
- **Curated checkpoints** — each catalog entry shows its author, source, and license before
  download. CC-BY checkpoints require attribution. The official Real-ESRGAN ×2 project/asset is
  recorded as BSD-3-Clause and the official FBCNN project/model-zoo asset as Apache-2.0. The HAT-S
  Face and HAT-L Face checkpoints' separate weight/training-data rights could not be verified,
  so LocalSR does not auto-download or commercially recommend them. Exact evidence and
  digests are in `docs/model-licenses.md`.
- **RealPLKSR 4x NomosWebPhoto / HFA2k** — models by **Philip Hofmann (Phips / Phhofm)**,
  declared by the author under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
  Sources: [NomosWebPhoto](https://huggingface.co/Phips/4xNomosWebPhoto_RealPLKSR) and
  [HFA2k](https://huggingface.co/Phips/4xHFA2k_ludvae_realplksr_dysample).
  LocalSR downloads the author's checkpoint files unchanged from their catalog-pinned
  publisher assets; no model weights are included in installers. Sharing and commercial
  use are permitted under the license, with attribution and retained notices. This does
  not imply the author's endorsement of LocalSR. See `docs/beta-model-license-choices.md`
  for the explicit declarations and exact checkpoint provenance.

Review upstream license files and current terms before redistributing LocalSR or using outputs in a
commercial workflow.
