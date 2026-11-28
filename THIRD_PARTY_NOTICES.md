# Third-party notices

LocalSR application source is MIT licensed. Dependencies and optional model downloads retain their
own terms; this file is a distribution notice, not a replacement for their license texts.

- **Mobile app** — the iPhone and Android app in `mobile/` keeps its own notices: `mobile/Models/NOTICE.txt`
  for its two bundled models and `mobile/tools/NOTICE-third-party.txt` for its dependencies.
- **Desktop host** — Tauri 2 is MIT/Apache-2.0 and Svelte is MIT. Transitive
  components retain their upstream permissive or file-level terms (including Apache, BSD, ISC,
  MIT, MPL, Unicode, and Zlib terms), while their versions are locked by Cargo and npm. macOS uses WKWebView and Windows uses WebView2. The Linux AppImages bundle
  WebKitGTK/GTK/GStreamer libraries from Ubuntu 22.04; these include LGPL and other
  licenses and require the matching notices and applicable source/relinking materials.
- **IBM Plex Sans** — Copyright © 2017 IBM Corp., with Reserved Font Name "Plex", under the
  SIL Open Font License 1.1. The interface bundles the unmodified variable font from IBM's
  `@ibm/plex-sans-variable` 0.2.0 package; the license text is in
  `desktop/src/assets/fonts/OFL.txt`.
- **PyTorch / TorchVision** — BSD-style licenses from the PyTorch project.
- **Core ML conversion (macOS)** — coremltools is BSD-3-Clause (Apple Inc.). The macOS worker
  uses it to convert supported checkpoints into Core ML programs on the user's Mac; Core ML
  itself is part of macOS and not distributed by LocalSR. Its Python dependencies in the
  worker are protobuf (BSD-3-Clause), attrs and cattrs (MIT) and pyaml (WTFPL), and their
  license files are included in the frozen worker. The converted programs run through
  PyObjC (pyobjc-core, pyobjc-framework-Cocoa and pyobjc-framework-CoreML, MIT).
- **NVIDIA CUDA packages** — contain the NVIDIA libraries PyTorch's CUDA 13.0 build uses (CUDA
  Runtime, cuBLAS, cuFFT, cuRAND, cuSOLVER, cuSPARSE, cuSPARSELt, NVRTC, nvJitLink, CUPTI, NVTX,
  cuFile, nvJPEG and cuDNN). They are NVIDIA's software, included as the portions its
  [CUDA Toolkit EULA](https://docs.nvidia.com/cuda/eula/index.html) and
  [cuDNN license](https://docs.nvidia.com/deeplearning/cudnn/latest/reference/eula.html) identify
  as distributable, and remain under those terms: they are for use by LocalSR only, stay in
  object form and may not be separated for other use or reverse engineered. NCCL is
  BSD-3-Clause and NVSHMEM is Apache-2.0 ([NVIDIA/nvshmem](https://github.com/NVIDIA/nvshmem));
  their license texts are in the source bundle. No NVIDIA driver is included.
  `packaging/nvidia/redistributables.json` records the basis for each library.
- **AMD ROCm package** — contains the ROCm runtime and math libraries PyTorch's ROCm 7.2 build
  uses (HIP, the HSA runtime, rocBLAS, hipBLAS/hipBLASLt, MIOpen, rocFFT, rocRAND, rocSOLVER,
  rocSPARSE, RCCL, AOTriton, aqlprofile and the ROCm profiling and SMI libraries), published by
  AMD under MIT, BSD, University of Illinois/NCSA and Apache-2.0-with-LLVM-exception terms in
  [ROCm's repositories](https://github.com/ROCm/rocm-systems); the MIT notice they share is
  reproduced below. Its libnuma (LGPL-2.1) and libelf (elfutils, LGPL-3.0-or-later or
  GPL-2.0-or-later) are Ubuntu 22.04's own builds, and the ROCm source bundle carries their
  Ubuntu source packages. libdrm is MIT.
- **Windows DirectML engine** — ONNX is Apache-2.0; ONNX Runtime is MIT and ships its own
  third-party notices. The DirectML build uses ONNX 1.22.0, ONNX Runtime DirectML 1.24.4 and
  Torch 2.13.0 CPU for loading and conversion; `ml_dtypes` is Apache-2.0. The provider DLLs
  keep their shipped terms, and the license and notice files are included in the frozen worker.
- **Intel GPUs through OpenVINO** (Windows DirectML and Linux CPU editions) —
  `onnxruntime-ep-openvino` 1.8.1 is MIT and carries its own OpenVINO runtime (Apache-2.0),
  oneTBB (Apache-2.0) and hwloc 2.9.3 (BSD-3-Clause, reproduced below). Its
  `third-party-programs.txt`, listing ONNX Runtime and nlohmann/json (MIT) and OpenVINO, ONNX,
  flatbuffers, opencv_contrib and a TensorFlow allocator (Apache-2.0), is included in the frozen
  worker. The Linux CPU edition also ships ONNX 1.22.0 (Apache-2.0) and ONNX Runtime 1.24.4 (MIT)
  for the model conversion. The Linux CPU edition bundles Ubuntu 22.04's OpenCL ICD loader
  `libOpenCL.so.1` (ocl-icd 2.2.14, BSD-2-Clause, reproduced below), which finds the system's
  GPU driver; the driver itself comes from the system and is not distributed.
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
- **FlashVSR video integration (Labs)**: an MLX port of the FlashVSR v1.1 Tiny pipeline
  (Apache-2.0, OpenImagingLab/FlashVSR, built on DiffSynth-Studio and Wan2.1, both Apache-2.0),
  with the TCDecoder from TAEHV (MIT, copyright 2025 Ollin Boer Bohan) and attention code
  adapted from MLX (MIT, copyright 2023 Apple Inc.). The license texts and the list of changes
  are in `src/localsr/video_models/flashvsr/NOTICE.md`. The FlashVSR v1.1 weights (Apache-2.0)
  are an optional, checksum-pinned download and are not included in installers.
- **MLX** (Apple silicon packages only): the `mlx` and `mlx-metal` wheels, MIT, copyright 2023
  Apple Inc.; they run FlashVSR on the GPU.
- **RIFE frame interpolation** — the IFNet network code from Practical-RIFE (MIT, copyright
  2021 hzwer), with the license text in `src/localsr/video_models/rife/NOTICE.md`. The RIFE 4.26
  checkpoint (MIT, per the Practical-RIFE README) is an optional, checksum-pinned download and is
  not included in installers.
- **DDColor photo colorization**: the DDColor inference network from piddnad/DDColor (Apache-2.0,
  with ConvNeXt under Meta Platforms, Inc. copyright and DETR positional encoding under Facebook,
  Inc. copyright), with the license text and modifications listed in
  `src/localsr/image_models/ddcolor/NOTICE.md`. The DDColor checkpoint (`ddcolor_modelscope`, Apache-2.0) is an optional, checksum-pinned download and is not included
  in installers. Source: https://github.com/piddnad/DDColor.
- **NERVE architecture**: the NERVE network by Philip Hofmann (`Phhofm/traiNNer-redux` at
  `9ecb9adf` and `huggingface.co/Phips/NERVE`, Apache-2.0) and its Spandrel loader from
  `chaiNNer-org/spandrel#333` (`Phhofm/spandrel`, MIT, Copyright (c) 2024 The ChaiNNer
  Organization) at commit `3429b70e`. Both licence texts and LocalSR's modifications are in
  `src/localsr/image_models/nerve/NOTICE.md`. No NERVE weights are included; users load their
  own checkpoints.
- **HEART architecture**: the HEART network by Philip Hofmann (`Phhofm/traiNNer-redux` at
  `cb3f6f25` and `huggingface.co/Phips/HEART`, Apache-2.0, incorporating `XPixelGroup/HAT` under
  Apache-2.0, `2minkyulee/i-LN` under Apache-2.0, `Rift73/DRFT` under MIT, and `dslisleedh/SST`
  under MIT) and its Spandrel loader from `chaiNNer-org/spandrel#334` (`Phhofm/spandrel`, MIT,
  Copyright (c) 2024 The ChaiNNer Organization) at commit `2778b9f8`. All licence texts and
  LocalSR's modifications are in `src/localsr/image_models/heart/NOTICE.md`. No HEART weights
  are included; users load their own checkpoints.
- **PULSE architecture and LocalSR ×4 models**: `pulse_arch.py` and the optional
  `4xLocalSRPhoto_PULSE-64x8` / `4xLocalSRPhoto_PULSE-96x12` weights (`HerRei/localsr-models` release `v1`, CC BY 4.0; attribution: "LocalSR models by Hermes Reisner (https://github.com/HerRei/localsr-models), CC BY 4.0")
  follow Philip Hofmann's PULSE layout specification (`huggingface.co/Phips/PULSE`, MIT) as an
  independent reimplementation trained on `Phips/lucid-cc0-v2-hc` (CC0 photos from pxhere).
  Attribution and MIT text are in `src/localsr/image_models/pulse/NOTICE.md`; weights are optional
  checksum-pinned downloads and are not included in installers.
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

- **Native image-editing runtime** — stable-diffusion.cpp and GGML (MIT), pinned to
  `3f8527a46c54ecf4cb4ed6003da8e8982283c73c`. Its retained licenses, third-party
  notices and source revision are bundled under `engine/_internal/edit/`.
  Source: https://github.com/leejet/stable-diffusion.cpp.
- **Qwen Image Edit 2511** — Qwen (Apache-2.0), with GGUF diffusion quantizations
  by Unsloth, matching Qwen2.5-VL text/vision encoders and the Qwen Image VAE.
  Source: https://huggingface.co/Qwen/Qwen-Image-Edit-2511. Its optional 4-step
  Lightning LoRA is by LightX2V (Apache-2.0).
  Source: https://huggingface.co/lightx2v/Qwen-Image-Edit-2511-Lightning.
- **FLUX.2 klein 4B** — Black Forest Labs (Apache-2.0), with GGUF diffusion
  quantizations by leejet, the Qwen3-4B text encoder (Qwen, Apache-2.0, GGUF by
  Unsloth) and the FLUX.2 autoencoder as published by Comfy-Org (Apache-2.0).
  Qwen Image 2.1 is not offered: its Qwen Research License permits research and
  evaluation only.
  Source: https://huggingface.co/black-forest-labs/FLUX.2-klein-4B.
- **Mage-Flow-Edit-Turbo** - Microsoft (MIT, via `https://github.com/microsoft/Mage`),
  using the public Comfy-Org repack (`https://huggingface.co/Comfy-Org/Mage-Flow`, MIT)
  and johnsor Q6_K GGUF (`https://huggingface.co/johnsor/Mage-Flow-Edit-Turbo-GGUF`, MIT),
  with Qwen3-VL-4B-Instruct text and vision encoders (Qwen, Apache-2.0,
  `https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct-GGUF`). The original Microsoft
  Hugging Face repositories are not publicly accessible.
  Editing weights are separate, optional, checksum-pinned downloads and are never
  included in installers. Exact revisions and component digests are in
  `src/localsr/core/edit_catalog.json`.

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

## hwloc BSD notice

Copyright © 2004-2006 The Trustees of Indiana University and Indiana University Research and Technology Corporation.  All rights reserved.
Copyright © 2004-2005 The University of Tennessee and The University of Tennessee Research Foundation.  All rights reserved.
Copyright © 2004-2005 High Performance Computing Center Stuttgart, University of Stuttgart.  All rights reserved.
Copyright © 2004-2005 The Regents of the University of California. All rights reserved.
Copyright © 2009      CNRS
Copyright © 2009-2016 Inria.  All rights reserved.
Copyright © 2009-2015 Université Bordeaux
Copyright © 2009-2015 Cisco Systems, Inc.  All rights reserved.
Copyright © 2009-2012 Oracle and/or its affiliates.  All rights reserved.
Copyright © 2010      IBM
Copyright © 2010      Jirka Hladky
Copyright © 2012      Aleksej Saushev, The NetBSD Foundation
Copyright © 2012      Blue Brain Project, EPFL. All rights reserved.
Copyright © 2013-2014 University of Wisconsin-La Crosse. All rights reserved.
Copyright © 2015      Research Organization for Information Science and Technology (RIST). All rights reserved.
Copyright © 2015-2016 Intel, Inc.  All rights reserved.
See COPYING in top-level directory.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions
are met:
1. Redistributions of source code must retain the above copyright
   notice, this list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright
   notice, this list of conditions and the following disclaimer in the
   documentation and/or other materials provided with the distribution.
3. The name of the author may not be used to endorse or promote products
   derived from this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE AUTHOR ``AS IS'' AND ANY EXPRESS OR
IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES
OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED.
IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT,
INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT
NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF
THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

## ocl-icd BSD notice

From `ocl_icd_loader.c` of ocl-icd 2.2.14:

Copyright (c) 2012-2020, Brice Videau <bvideau@anl.gov>
Copyright (c) 2012-2020, Vincent Danjean <Vincent.Danjean@ens-lyon.org>
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
