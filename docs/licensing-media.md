# Media formats and licensing

LocalSR prefers staying clear of patent and copyleft conflicts over supporting
every video format out of the box. This page describes what each download
contains, what users can add themselves, and what a release must publish.

## What LocalSR distributes

Every LocalSR binary contains only media code for **royalty-free or
patent-expired formats**, built from LGPL-2.1-or-later or permissively licensed
sources. The single source of truth is
[`packaging/ffmpeg/codec-policy.json`](../packaging/ffmpeg/codec-policy.json).

| Area | Included | Deliberately not included |
| --- | --- | --- |
| Video decoders | AV1 (dav1d), VP8, VP9, Theora, MPEG-1, MPEG-2, Motion JPEG, FFV1, raw, PNG | H.264, HEVC, VC-1/WMV, MPEG-4 Part 2 (DivX/Xvid), H.263, ProRes, DNxHD, VVC |
| Video encoders | AV1 (SVT-AV1), VP9 (libvpx), FFV1 (lossless) | x264, x265, OpenH264, any H.264/HEVC encoder |
| Audio decoders | Opus, Vorbis, FLAC, ALAC, MP3, MP2, AC-3, PCM, IMA/MS ADPCM | AAC, WMA, AMR, E-AC-3, DTS, TrueHD |
| Audio encoders | Opus, FLAC, PCM | AAC |
| Subtitles | SubRip, ASS/SSA, WebVTT, MP4 timed text | — |
| Containers | MP4/MOV, MKV/WebM, AVI, MPEG-PS/TS, Ogg, WAV, FLAC, MP3, ASF (container only) | — |

Decodable audio that the output container accepts is **copied unchanged** (for
example Opus, FLAC, MP3 or AC-3 into MP4). Other decodable audio is converted to
Opus. AAC and other audio LocalSR cannot decode cannot be copied either (PyAV
needs a decoder to copy a stream), so it is omitted with a warning unless a
user-installed FFmpeg converts it.

The FFmpeg libraries are built by
[`packaging/ffmpeg/build_lgpl_media.py`](../packaging/ffmpeg/build_lgpl_media.py)
with `--disable-everything --disable-autodetect` and an explicit allowlist,
without `--enable-gpl`, `--enable-nonfree` or `--enable-version3`, as replaceable
shared libraries. OpenCV is built without FFmpeg. Linux AppImages keep only the
GStreamer plugins in `LINUX_GSTREAMER_PLUGIN_ALLOWLIST`.

## System codecs on macOS

macOS ships H.264, HEVC and AAC codecs under Apple's licences, behind
AVFoundation. LocalSR uses them through `localsr-media`, a small helper built
from [`packaging/macos/media-helper/main.swift`](../packaging/macos/media-helper/main.swift)
that links only Apple frameworks (AVFoundation, CoreMedia, CoreVideo,
VideoToolbox). It decodes into the same lossless FFV1/FLAC intermediate the
FFmpeg route uses and encodes exports with VideoToolbox, exchanging raw frames
with the worker over pipes. LocalSR's binaries still contain no code for these
formats, and the helper adds no third-party code to the package. It writes
H.264/HEVC into MP4 or MOV; MKV output and formats macOS cannot read (WMV,
DivX, FLV, H.263) still go through a user-installed FFmpeg. Windows has
equivalent codecs in Media Foundation; a helper for them is planned.

## What users can add themselves

Most phone and camera videos use H.264 or HEVC. On macOS they open through the
system codecs. Elsewhere, or for formats macOS cannot read, a user can install
FFmpeg (for example `brew install ffmpeg`, `winget install Gyan.FFmpeg` or their
Linux package manager). An FFmpeg on the PATH or in the usual locations is used
automatically; selecting one under **Advanced settings → Video → External
FFmpeg** picks a specific build. The notice that appears when a file needs it
shows the command for the actual distribution and can run it in a terminal.

- LocalSR never downloads, bundles or links that program. It runs the selected
  executable as a separate process.
- Sources LocalSR cannot decode are first converted by that program into a
  temporary lossless FFV1/FLAC file. This needs extra disk space.
- H.264/HEVC exports are encoded by that program from a temporary lossless FFV1
  master written by LocalSR.
- Without a selected FFmpeg, such videos show an actionable message instead of a
  preview, and H.264/HEVC export cannot be started. In both cases the desktop app
  opens a notice with the platform's install command and a link to ffmpeg.org;
  finding or choosing the program from that notice re-inspects the videos.

## Usability consequences

- H.264/HEVC videos need a user-installed FFmpeg on Windows and Linux; macOS
  uses its own codecs.
- The default export is AV1 in MP4. AV1 encodes more slowly than x264 and some
  older players and editors cannot open it; VP9 and lossless FFV1 (MKV) are
  alternatives.
- Comparison playback copies are VP9/Opus WebM.
- HDR preservation exports 10-bit AV1, VP9 or FFV1, or HEVC Main 10 through the
  user's FFmpeg (libx265).
- Linux AppImages cannot play H.264 in the comparison player without conversion.

## Release gates

Release builds fail closed when:

1. the Python environment being frozen has a codec outside the policy
   (`scripts/verify_codec_allowlist.py --python-env`), for example PyPI's PyAV
   wheels with x264/x265;
2. the frozen worker or an AppImage AppDir contains forbidden files or a GPL/
   nonfree FFmpeg build (`verify_codec_allowlist.py --tree`);
3. a CUDA package contains an NVIDIA library without a verified redistribution
   basis (`scripts/verify_nvidia_redistributables.py`, policy in
   `packaging/nvidia/redistributables.json`). cuFile and NVSHMEM are excluded;
   cuDNN 9 Windows DLLs block Windows CUDA packages until NVIDIA confirms their
   distribution in writing.

`packaging/ffmpeg/install_media_runtime.py` builds and installs the compliant
PyAV and OpenCV wheels in one step; the Linux and macOS release jobs run it
before freezing the worker.

On Windows the same recipe builds the codec libraries and FFmpeg with MSYS2's
MinGW-w64 UCRT64 toolchain and PyAV with MSVC (`.github/workflows/windows-installers.yml`).
`--private-preview-media` remains only for private, never-distributed previews.

## Withheld packages

- **Intel XPU (Linux)** — Intel's oneAPI runtime license asks the distributor to
  indemnify Intel. XPU support stays in the source; `ci/tauri-targets.json` lists
  it under `withheld_targets`.
- **CUDA (Linux and Windows)** — held until NVIDIA's redistribution terms are
  settled (questions 4 and 5 below) and a package has run on NVIDIA hardware.
- **AMD ROCm (Linux)** — PyTorch's ROCm build bundles libnuma and elfutils (LGPL)
  built outside Ubuntu, whose exact corresponding sources are not identified yet,
  and AMD's closed-source `libhsa-amd-aqlprofile64`, whose redistribution terms are
  unconfirmed.
- **DirectML (Windows)** — not a licensing hold: its GPU results still miss the
  accuracy tolerance, so it stays a test build.

Published packages: macOS (Apple Silicon), the Linux CPU AppImage and the Windows
CPU installer.

## What each download must publish

`scripts/build_source_bundle.py` assembles, from the binary's actual file
inventory: LocalSR's source at the built commit, the media runtime's exact
source archives, configure line and relinking instructions, the OpenCV source
and recipe, the LibRaw and GCC runtime sources that wheels bundle
(`packaging/extra-sources.json`), Ubuntu source packages for every library an
AppImage bundles from the build host (`--apt-sources`), bundled license texts,
`THIRD_PARTY_NOTICES.md`, the codec policy and a SHA-256 manifest. Host it on the
same server as the binary.

The About window and the Update dialog state: “This software uses libraries from
the FFmpeg project under the LGPLv2.1.”

## Legal assessment

No lawyer was consulted. This is the maintainer's assessment for a free,
non-commercial project, recorded on 17 September 2026; it is not legal advice.
Where a question stays open, the affected packages are withheld rather than
shipped on a guess.

1. **Reading MP4/Matroska without decoding H.264/HEVC/AAC.** Codec patents claim
   encoding and decoding methods, not parsing the container around the streams.
   Negligible risk; the demuxers stay.
2. **Running an FFmpeg the user installed.** LocalSR does not distribute those
   codecs and calls a general-purpose tool the user chose, as Audacity has done
   for years to stay clear of codec patents. Very low risk.
3. **MPEG-2 video and AC-3 audio.** The patents have expired worldwide (the
   United States in 2017 and 2018, the last other countries shortly after);
   Fedora, whose legal review is conservative, has shipped both since. Settled.
4. **NVIDIA's reverse-engineering terms next to LGPL FFmpeg.** The LGPL limits
   the terms on the work that uses FFmpeg, which is LocalSR (MIT) and PyAV (BSD);
   NVIDIA's terms bind only NVIDIA's separately licensed libraries. The same
   reasoning covers Intel's MKL, which PyTorch links into its x86 CPU builds under
   a licence that also forbids reverse engineering. Assessed as low risk; CUDA
   packages are still withheld until they have run on NVIDIA hardware and question
   5 is settled.
5. **cuDNN 9 Windows DLLs.** NVIDIA's license allows distributing cuDNN inside an
   application, and PyTorch's Windows CUDA wheels ship the same DLLs. Left open
   with question 4: Windows CUDA is withheld.
6. **Operating system codecs.** Apple and Microsoft license H.264, HEVC and AAC
   for use through their public APIs, which every video application on those
   systems relies on. Settled.
