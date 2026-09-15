# Known limitations

These notes describe the **v0.1.1-beta candidate**, which is not yet publicly
released. Older alpha packages have different dependencies and trust properties;
see their [release notes](docs/releases/).

## Hardware and processing time

Recorded beta workloads cover Apple Silicon/MPS, Linux CPU and RX 9060 XT/ROCm,
and Windows CPU and Intel UHD 620/DirectML. Other CUDA, XPU and DirectML devices
remain available targets with Labs coverage. A successful build or worker startup
does not establish physical GPU compatibility. See the [platform matrix](docs/beta-platform-matrix.md).

High-resolution video can take hours or days and may exceed available RAM, GPU
memory or disk space. A nominal 16 GB GPU is not a guarantee that SeedVR2 fits.
Five-minute 480p export has been exercised; a complete four-minute 4K export has
not. Try a short clip before committing to a long job.

## Media formats

LocalSR includes only royalty-free or patent-expired media formats to avoid
patent and copyleft licensing conflicts. H.264, HEVC, WMV, DivX and AAC-only
sources, which include most phone and camera videos, open only after the user
installs FFmpeg and selects it under Advanced settings → Video; adding such a
video shows a notice with the install command for the platform. That FFmpeg also
writes H.264/HEVC exports, from a temporary lossless copy that needs extra disk
space. The default export is AV1, which some older players and editors cannot
open. Windows packages stay blocked until the Windows LGPL media build exists,
and Windows CUDA packages until NVIDIA confirms cuDNN 9 DLL redistribution.
See [media formats and licensing](docs/licensing-media.md).

## Restoration quality

- HAT HDR preservation uses floating-point processing and 10-bit HLG/PQ export,
  but the models were trained on SDR. HDR perceptual quality and temporal stability
  remain unverified. Live previews are SDR display conversions.
- SeedVR2 exports SDR and requires a compatible engine. Its FP8 download size
  does not describe total working memory. De-flicker and video face processing
  are also Labs features.
- NAFNet can produce unstable results on some scanned documents. The current
  guard rejects two of eleven reproduced inputs after bounded retries and writes
  no output for those failures. Always inspect text, faces and fine detail.
- MOV compatibility depends on codecs, colour metadata, transforms and audio
  tracks. Compatible audio is retained; spatial audio and unsupported tracks may
  be omitted. Compressed audio trims have packet-level precision.

See [video support](docs/video-support.md) and [metadata handling](docs/metadata.md).

## Models and runtime dependencies

Both HAT face checkpoints require verified user imports because their independent
checkpoint rights remain unresolved. NomosWebPhoto/HFA2k use the author's declared
CC BY 4.0 terms with attribution. No restoration checkpoints are bundled.

The retained Windows MSIX uses torch-directml / Torch 2.4.1. The prepared source
replacement uses Torch 2.13.0 CPU and ONNX Runtime DirectML 1.24.4, but no package
containing it has been built. NAFNet SIDD photo comparisons still exceed the
fixed GPU numerical tolerance; no temporary CPU restriction is approved or
applied. SPAN can also diverge on some periodic high-contrast inputs; detected
unstable output is rejected before export. The Linux GTK dependency chain has a
recorded glib advisory. The native dependency/codec redistribution review and
final installed acceptance must finish before publication.
[Dependency review](docs/beta-dependency-review.md) · [Model licenses](docs/model-licenses.md).

Custom `.safetensors` models are accepted by default. The explicit override for
unverified `.pth`, `.pt` and `.ckpt` files treats them as executable code; it is
not a sandbox. Read the [security policy](SECURITY.md).

## Installation and updates

The macOS review candidate is signed, notarized and stapled. Native installed
upgrade/recovery checks remain incomplete. Windows WACK still reports a warning;
Microsoft Store certification is pending. MSIX upgrades preserve user data in
recorded tests, but uninstall can delete the profile; explicit backup restoration
has been verified.

Direct-update signature and download checks pass. Public feeds, native install/
restart/recovery and final package rebuilding remain release requirements.
The public download and issue-tracker destinations are still being prepared.
Current blockers are recorded in the [beta checklist](docs/beta-release-checklist.md).
