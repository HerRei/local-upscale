# Third-party notices

LocalSR application source is MIT licensed. Dependencies and optional model downloads retain their
own terms; this file is a distribution notice, not a replacement for their license texts.

- **Desktop host** — Tauri 2 is MIT/Apache-2.0 and Svelte is MIT. Transitive
  components retain their upstream permissive or file-level terms (including Apache, BSD, ISC,
  MIT, MPL, Unicode, and Zlib terms), while their versions are locked by Cargo and npm. macOS uses WKWebView and Windows uses WebView2. The Linux AppImages bundle
  WebKitGTK/GTK/GStreamer libraries from Ubuntu 24.04; these include LGPL and other
  licenses and require the matching notices and applicable source/relinking materials.
- **PyTorch / TorchVision** — BSD-style licenses from the PyTorch project.
- **NVIDIA CUDA packages** — contain the NVIDIA libraries PyTorch's CUDA 12.6 build uses (CUDA
  Runtime, cuBLAS, cuFFT, cuRAND, cuSOLVER, cuSPARSE, cuSPARSELt, NVRTC, nvJitLink, CUPTI, NVTX
  and cuDNN). They are NVIDIA's software, included as the portions its
  [CUDA Toolkit EULA](https://docs.nvidia.com/cuda/eula/index.html) and
  [cuDNN license](https://docs.nvidia.com/deeplearning/cudnn/latest/reference/eula.html) identify
  as distributable, and remain under those terms: they are for use by LocalSR only, stay in
  object form and may not be separated for other use or reverse engineered. NCCL is
  BSD-3-Clause. cuFile and NVSHMEM are removed, and no NVIDIA driver is included.
  `packaging/nvidia/redistributables.json` records the basis for each library.
- **AMD ROCm package** — contains the ROCm runtime and math libraries PyTorch's ROCm 7.2 build
  uses (HIP, the HSA runtime, rocBLAS, hipBLAS/hipBLASLt, MIOpen, rocFFT, rocRAND, rocSOLVER,
  rocSPARSE, RCCL, AOTriton, aqlprofile and the ROCm profiling and SMI libraries), published by
  AMD under MIT, BSD, University of Illinois/NCSA and Apache-2.0-with-LLVM-exception terms in
  [ROCm's repositories](https://github.com/ROCm/rocm-systems); the MIT notice they share is
  reproduced below. Its libnuma (LGPL-2.1) and libelf (elfutils, LGPL-3.0-or-later or
  GPL-2.0-or-later) are Ubuntu 24.04's own builds, and the ROCm source bundle carries their
  Ubuntu source packages. libdrm is MIT.
- **Windows DirectML engine** — ONNX is Apache-2.0; ONNX Runtime is MIT and ships its own
  third-party notices. The DirectML build uses ONNX 1.22.0, ONNX Runtime DirectML 1.24.4 and
  Torch 2.13.0 CPU for loading and conversion; `ml_dtypes` is Apache-2.0. The provider DLLs
  keep their shipped terms, and the license and notice files are included in the frozen worker.
- **macOS system codecs** — `localsr-media` links Apple's AVFoundation, CoreMedia, CoreVideo and
  VideoToolbox frameworks, which are part of macOS and not distributed by LocalSR. It adds no
  third-party code to the package.
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
  recipe accompany the macOS source bundle.
- **Corresponding source** — `scripts/build_source_bundle.py` assembles the source, build
  instructions and license texts for each binary from that binary's file inventory, and the
  bundle is published next to the download. Alpha packages before 0.1.0 used a different media
  runtime, described in their release notes. See `docs/licensing-media.md`.
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
  not imply the author's endorsement of LocalSR. See `docs/model-licenses.md` for the
  exact checkpoint provenance.

Review upstream license files and current terms before redistributing LocalSR or using outputs in a
commercial workflow.

## AMD ROCm MIT notice

Copyright (C) Advanced Micro Devices, Inc.

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
associated documentation files (the "Software"), to deal in the Software without restriction,
including without limitation the rights to use, copy, modify, merge, publish, distribute,
sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or
substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT
NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES
OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
