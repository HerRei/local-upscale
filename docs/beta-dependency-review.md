# Beta dependency and redistribution review

Updated 13 September 2026. **Review is not a clean security or redistribution
sign-off.** The retained binaries are review candidates. Their own library
versions and exact hashes are recorded in `build/beta-review/`.

## What the inspections established

| Component | Result | Consequence |
| --- | --- | --- |
| Frontend npm lock | Audit reported zero findings across 196 dependencies | Retain report; this does not cover Rust/Python/native codecs |
| Rust lock | No non-withdrawn vulnerability-list entries; glib 0.18.5 has the unsoundness advisory RUSTSEC-2024-0429; unmaintained dependency warnings also remain | GTK3/Tauri dependency work is separate from the application's tile fixes |
| Modern Python engine audit | Findings in setuptools 78.1.0 and wheel 0.45.1 | Beta source/build inputs and seven hashed backend locks now pin setuptools 83.0.0 / wheel 0.46.2. Current artifacts still contain the earlier versions; full worker rebuilds remain |
| Retained DirectML MSIX 1.0.7.0 | torch-directml requires Torch 2.4.1; the historical audit reports later Torch advisories | This accepted package is an earlier baseline and is not cleared for public distribution |
| Prepared Windows ONNX engine | Torch 2.13.0 CPU / ONNX Runtime DirectML 1.24.4; 56 locked distributions, pip check passes, dated OSV query has no matches | Real source-worker acceptance passes except the documented SIDD GPU comparison; frozen package and native-library review remain |
| Linux AppImages | All bundled ELF files inspected, including AMD device objects separately from x86_64 host code | No foreign host architecture found; corrected packages exclude the four conflicting host Wayland libraries |
| Multimedia | Actual FFmpeg libraries and configure strings inspected on macOS, Windows and Linux | Top-level PyAV/OpenCV licenses are insufficient to describe the whole binary bundle |

The Torch build suffix was removed **only for advisory lookup**. The old installed
MSIX remains unchanged; the current DirectML source lock now uses the replacement
versions documented below. A count of advisory matches does not mean all
are reachable through LocalSR. Conversely, catalog checksums do not fix Torch.
LocalSR blocks unverified pickle/TorchScript checkpoints by default, verifies
curated sizes and SHA-256 before load, and uses validated Safetensors for its
bundled SeedVR2 conditioning. The explicit unsafe-checkpoint override remains a
trust boundary, not a sandbox. No broad exploit-reachability proof is claimed.

[PyTorch checkpoint-loading advisory](https://github.com/pytorch/pytorch/security/advisories/GHSA-53q9-r3pm-6pq6)
· [glib advisory](https://rustsec.org/advisories/RUSTSEC-2024-0429.html).

## Multimedia evidence

- **PyAV 18.1.0:** its versioned build configuration points to upstream
  `pyav-ffmpeg` **8.1.2-1**. Actual bundled PyAV FFmpeg libraries enable both
  x264 and x265 and report LGPL version 3 or later.
- **Upstream patch:** the retained configure patch moves `libx264` and `libx265`
  out of FFmpeg's GPL list into its version-3 list. A reported LGPL string must
  therefore not be treated as a standalone grant for the encoder code.
- **macOS OpenCV:** its bundled FFmpeg 6.0 library explicitly reports GPL version
  3 or later. Its configure line includes x264/x265 and other components.
- **Linux OpenCV:** bundled FFmpeg 5.1.4 reports LGPL version 2.1 or later.
- **Linux multimedia framework:** Ubuntu's FFmpeg 6.1.1-3ubuntu5 explicitly reports
  GPL version 2 or later. The AppImage also bundles WebKitGTK, GTK and GStreamer.
- **Windows:** the inspected frozen worker's PyAV FFmpeg reports LGPL version 3
  or later with x264/x265 enabled; the configure string is retained.

The package inspections and upstream files are in
`*-multimedia-licenses.json` and `licenses/source-evidence.json`. Failed direct
loads of duplicated macOS library paths are recorded as inspection limitations;
the loadable original wheel paths supplied the runtime configuration. The actual
signed worker and video exports passed; those diagnostic duplicate-load failures
are not described as application failures.

Primary references:
[FFmpeg licensing and source checklist](https://ffmpeg.org/legal.html),
[PyAV v18.1.0 build pin](https://github.com/PyAV-Org/PyAV/blob/v18.1.0/scripts/ffmpeg-latest.json),
[upstream configure patch](https://github.com/PyAV-Org/pyav-ffmpeg/blob/main/patches/ffmpeg.patch),
[x264 COPYING](https://github.com/mirror/x264/blob/master/COPYING),
[x265 COPYING](https://github.com/videolan/x265/blob/master/COPYING).

## Approved distribution approach

On 13 September 2026 the user approved making LocalSR's release source, build
instructions and required exact dependency sources available with the beta,
retaining the application's MIT notice. Approval is conditional on source and
README cleanup and respecting upstream licenses. Final release publication still
follows review of the concrete packages.

This follows FFmpeg's published recommendation to provide exact sources and build
changes with downloads. It does not establish that the current codec/GPU
combination is compatible. Actual GPL components must be treated under their GPL
terms; the LGPL-only checklist cannot clear the existing x264/x265-enabled build.
Codec patents are separate from copyright/source-license compliance, as FFmpeg's
legal page explains.

Review the complete combination, including proprietary GPU runtimes. Rebuild
components where their current combination cannot be distributed under compatible
terms. An OpenCV build without FFmpeg, for example, could still serve the face
detector, with video handled by a separately reviewed codec/runtime arrangement.
Do not remove H.264/HEVC or other agreed features silently. A private source
repository and general upstream links are not a complete corresponding-source
package.

No unresolved restoration checkpoints are bundled. The separate Nomos/HFA policy
is approved on the basis of the author's CC BY 4.0 declarations and verified
provenance; it does not settle application/codec source licensing.

## Remaining implementation

- Carry the [implemented Windows ONNX runtime](windows-inference-runtime-review.md)
  into a new package after build authorization and the outstanding SIDD decision.
  The old plugin's collection notice applies to the retained MSIX; the replacement
  explicitly disables ONNX Runtime telemetry. Source process-connection snapshots
  are limited evidence, not a final-package network audit.
- Rebuild full workers with the prepared locks/notices; verify provider DLLs and
  licenses in the actual frozen output. No package build was started in this phase.
- Collect exact corresponding sources/build inputs for the chosen binary set,
  including native multimedia dependencies. The present upstream evidence is a
  review input, not a complete source-distribution archive for all dependencies.
- Rebuild once from the final source snapshot and repeat affected installed checks,
  signatures and notarization. Preserve the earlier accepted artifacts as evidence.

## Packaging-tool change scope

The earlier tool update changed only setuptools/wheel pins and their published
hashes in seven backend locks. The later Windows DirectML replacement additionally
changes its Torch/torchvision lock and introduces ONNX, ONNX Runtime DirectML and
ml_dtypes; that change is independent of the tool update. The application
build-system floor now matches setuptools 83.0.0. PyPI metadata confirms both
versions support Python 3.11 and the existing packaging dependency satisfies
wheel's requirement. The isolated LocalSR wheel build, pip dependency check and frozen setuptools/wheel
probe all pass. The first frozen probe omitted distribution metadata and reported
an unknown setuptools version; adding the metadata to that diagnostic fixed the
version assertion. These checks do not establish
that all frozen inference workers have been rebuilt. Accepted review artifacts
remain immutable and still expose their original dependency inventory.

The subsequent [Slint retirement](slint-retirement.md) removes that dependency
from the same seven current locks. All other pins and hashes are preserved by
that cleanup. Optional Qt Widgets and the Python inference engine remain.

The setuptools Unicode-manifest advisory page and audit database disagree about
the listed patched version. Its described build-time exclusion issue is not
claimed fixed merely because the database lists 83.0.0. The review-source archive
uses an explicit file allowlist and is inspected independently of MANIFEST.in.
[Setuptools advisory](https://github.com/pypa/setuptools/security/advisories/GHSA-h35f-9h28-mq5c)
· [wheel advisory](https://github.com/pypa/wheel/security/advisories/GHSA-8rrh-rw8j-w5fx).

## Source formatting tools

The README/source cleanup adds pinned development-only Prettier 3.9.6 and
prettier-plugin-svelte 4.1.1, both MIT licensed. The subsequent npm audit reports
zero findings across 198 dependencies in the current lock. This is a separate
snapshot from the earlier 196-dependency report; it does not change the retained
native dependency findings. Runtime npm versions were not upgraded in this pass.

## Exact source preparation, 13 September

The PyAV FFmpeg build tag `8.1.2-1` resolves to
`a71bf9279f7a4659154b68ba6783e89be460bcd5`. Its build script, package definitions,
configure patch and source/hash table are retained without executing upstream
scripts. FFmpeg 8.1.2 and x265 4.2 source archives match the build's SHA-256 pins.
The x265 archive came from VideoLAN's distribution mirror after the original
endpoint failed DNS. The pinned x264 commit was retrieved from its GitHub mirror
with a separately recorded archive hash; its gzip container is not represented
as matching the original bzip2 archive hash. All 270 x264 archive files and executable modes were checked against the pinned
Git commit/tree, including the commit object hash. The complete codec dependency
closure and its match to every final binary still need verification before
distribution.

The remaining 17 archives named by that upstream build table were also fetched
once and verified against their SHA-256 pins (67,232,781 bytes). Together with
FFmpeg, x264 and x265, all twenty named source inputs are retained. This covers
that versioned codec build table, not every native component in every platform
package. Match the final binary inventory and retain the required complete
corresponding-source/build/notice material before distribution.

Evidence: `build/beta-review/rollout-20260913/license-evidence/`, including
`additional-source-downloads.json` and `x264-source-tree-verification.json`.

NVIDIA's SDK license permits redistribution only under its specified conditions
and redistributable lists. Inspect the final CUDA file inventory against those
lists and the exact component versions; neither an MIT app notice nor an upstream
wheel's existence clears the combined bundle. [NVIDIA SDK terms](https://docs.nvidia.com/cuda/eula/index.html).

Recommendation: retain the approved source-publication approach, complete the
codec/component compatibility review before publishing binaries, and rebuild any
incompatible codec arrangement while preserving H.264/HEVC functionality. A
separate codec process or OS codec route would need its own design/license review
and acceptance; neither is silently selected here. If the bundle cannot be
cleared from the published terms, obtain qualified licensing advice before its
public release. No paid codec license, model-policy change or scope reduction is
approved by this document.

## Legacy-video addition (13 September)

No runtime dependency or lockfile was added. The existing PyAV/FFmpeg runtime
provides decoding, BWDIF and H.264/AAC. A codec-only entry mode of the same worker
keeps playback conversion separate from inference; this does not resolve the
outstanding codec licensing review. FFmpeg is used by tests to generate fixtures.
Final bundles still need decoder/filter and corresponding-source verification.
