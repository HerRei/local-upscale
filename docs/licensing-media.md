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
| Containers | MP4/MOV, MKV/WebM, AVI, MPEG-PS/TS, Ogg, WAV, FLAC, MP3, ASF (container only) | — |

Audio in a format the output container accepts is **copied unchanged**, without
being decoded or re-encoded (for example AAC audio into MP4 or MKV). Other audio
is converted to Opus.

The FFmpeg libraries are built by
[`packaging/ffmpeg/build_lgpl_media.py`](../packaging/ffmpeg/build_lgpl_media.py)
with `--disable-everything --disable-autodetect` and an explicit allowlist,
without `--enable-gpl`, `--enable-nonfree` or `--enable-version3`, as replaceable
shared libraries. OpenCV is built without FFmpeg. Linux AppImages keep only the
GStreamer plugins in `LINUX_GSTREAMER_PLUGIN_ALLOWLIST`.

## What users can add themselves

Most phone and camera videos use H.264 or HEVC. To open them, or to export
H.264/HEVC, a user can install FFmpeg (for example `brew install ffmpeg`,
`winget install ffmpeg` or their Linux package manager) and select it under
**Advanced settings → Video → External FFmpeg**.

- LocalSR never downloads, bundles or links that program. It runs the selected
  executable as a separate process.
- Sources LocalSR cannot decode are first converted by that program into a
  temporary lossless FFV1/FLAC file. This needs extra disk space.
- H.264/HEVC exports are encoded by that program from a temporary lossless FFV1
  master written by LocalSR.
- Without a selected FFmpeg, such videos show an actionable message instead of a
  preview, and H.264/HEVC export cannot be started.

## Usability consequences

- H.264/HEVC videos need a user-installed FFmpeg on every platform.
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

The Windows LGPL media build is not implemented yet, so Windows packages cannot
pass gate 1 and stay blocked until it is.

## What each download must publish

`scripts/build_source_bundle.py` assembles, from the binary's actual file
inventory: LocalSR's source at the built commit, the media runtime's exact
source archives, configure line and relinking instructions, the OpenCV source
and recipe, Ubuntu source packages for bundled LGPL host libraries
(`--apt-sources` on the Linux build host), bundled license texts,
`THIRD_PARTY_NOTICES.md`, the codec policy and a SHA-256 manifest. Host it on the
same server as the binary.

The About window and the Update dialog state: “This software uses libraries from
the FFmpeg project under the LGPLv2.1.”

## Open legal questions

This is engineering risk reduction, not legal advice. Before a public release,
ask a Swiss IP lawyer (for example through IGE IP-Info or the Basel bar
association's legal information desk):

1. Does copying H.264/HEVC-adjacent audio such as AAC into a container without
   decoding it carry patent risk in Switzerland, the EU or the US?
2. Does offering a setting that runs a user-installed FFmpeg create indirect
   infringement exposure?
3. Are the MPEG-2 video and AC-3 audio patents fully expired in all download
   regions (the policy includes them)?
4. Can NVIDIA's reverse-engineering restrictions on its libraries coexist with the
   LGPL's relinking and debugging permissions in one CUDA package?
5. Written confirmation from NVIDIA for cuDNN 9 Windows DLLs.
