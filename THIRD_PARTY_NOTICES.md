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
- **Media runtime policy** — LocalSR bundles only royalty-free or patent-expired media formats
  and LGPL-2.1-or-later or permissively licensed media code. The exact allowlist is
  `packaging/ffmpeg/codec-policy.json`; release builds fail if a bundled component is outside it.
  H.264, HEVC, AAC, VC-1/WMV, MPEG-4 Part 2 and similar patent-licensed formats are not
  implemented by any LocalSR binary. Users may select an FFmpeg they installed themselves; that
  separate program is not distributed, bundled, downloaded or linked by LocalSR.
- **PyAV / FFmpeg** — PyAV is BSD-3-Clause. This software uses libraries from the FFmpeg project
  under the LGPLv2.1; LocalSR does not own FFmpeg. The bundled FFmpeg 8.1.2 is built by
  `packaging/ffmpeg/build_lgpl_media.py` without `--enable-gpl`, `--enable-nonfree` or
  `--enable-version3`, as shared libraries that can be replaced. It includes dav1d (BSD-2-Clause),
  SVT-AV1 (BSD-3-Clause-Clear with the AOMedia Patent License 1.0), libvpx (BSD-3-Clause with the
  WebM additional IP rights grant) and Opus (BSD-3-Clause with its royalty-free patent licenses).
  The exact source archives, configure line, patches (none) and rebuild/relinking instructions
  are published with every download as corresponding source.
- **OpenCV runtime** — OpenCV is Apache-2.0 and is used only by the optional CPU face detector and
  SeedVR2's drawing helpers. It is built from the pinned opencv-python-headless source by
  `scripts/build_macos_face_runtime.py` with FFmpeg, GStreamer, camera and window-system backends
  disabled, so it contains no video codecs. Windows packages never include OpenCV's FFmpeg plugin.
- **Linux AppImage media playback** — WebKitGTK plays media through GStreamer. Only the plugins
  listed in `scripts/build_tauri_preview.py` (`LINUX_GSTREAMER_PLUGIN_ALLOWLIST`) are distributed;
  the GStreamer libav, x264, openh264 and AAC plugins and their host FFmpeg libraries are removed.
- **RAW images** — rawpy is MIT; the bundled LibRaw 0.22.1 is provided under
  LGPL-2.1-or-later. The macOS runtime also uses libjpeg-turbo, JasPer and
  LittleCMS under their retained upstream terms. The optional LibRaw GPL
  demosaic packs are disabled. Matching source and the upstream macOS build
  recipe accompany the .13 macOS source bundle.
- **Beta distribution status** — Corresponding source, build instructions and license texts for
  each binary are assembled by `scripts/build_source_bundle.py` from that binary's actual file
  inventory and published next to it. Older alpha packages had a different media runtime; their
  notices remain in their release notes. See `docs/licensing-media.md`.
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
