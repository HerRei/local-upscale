# Testing

LocalSR has three layers of checks: the automated suite that runs on every
change, the checks a release build performs on its own packages, and manual
acceptance on real hardware.

## Automated checks

`./local-ci.sh` runs, in order: Ruff lint and formatting, workflow syntax
validation (actionlint), lock/version/catalog/architecture/readiness checks, the
Pyright boundary, the offline Python suite, Svelte checks, tests and build,
Rust formatting, Clippy (`-D warnings`) and tests, and wheel/sdist verification.
Dead code is caught by Vulture (Python), Knip (frontend) and the Rust
`dead_code` lint. GitHub Actions runs the same suite on Linux, Windows and macOS
for pushes to `main` and pull requests.

The Python suite covers the worker protocol, tiled inference geometry, image
metadata and colour handling, video timing (constant and variable frame rate,
trims, rotation, audio copying and conversion), HDR conversion and preservation,
cancellation and recovery through the actual worker loop, the model catalog and
download verification, the updater (signature, checksum, interrupted transfers,
disk space, rollback) and the packaging tools. It uses generated fixtures and
does not download models.

Real-model checks are separate: `python scripts/validate_live_models.py`
downloads every catalog checkpoint, verifies its hash, loads it through
Spandrel and runs a small inference.

## What a release build verifies

Every release job installs the package it just built and starts the bundled
host in headless mode, which must complete the JSON Lines handshake with the
bundled worker. The publisher then checks the Mach-O, PE or ELF architecture of
every native binary, the Developer ID signature and notarization ticket on
macOS, checksums and distinct digests across all files, and that the frozen
worker contains no codec outside the [media policy](licensing-media.md). An
empty-cache run downloads the Quick and Best models and processes an image
with each.

## Manual acceptance

Before a package is published it goes through these cases on the target
hardware, with the app installed the way a user would install it:

1. A corrupt video fails with a clear error, and the next job succeeds.
2. An odd-sized RGBA image with a non-ASCII filename exports correctly.
3. A trimmed video keeps its audio and timestamps.
4. Live tiles and the estimate appear during processing.
5. Cancelling a job removes its temporary files.
6. A job after a cancellation or failure runs normally.
7. Completed comparisons can be switched while another job runs; video
   comparisons play and seek.
8. The CPU and GPU benchmarks complete with stable timings.
9. An upgrade over the previous version keeps settings, recipes, models and the
   queue.

For each run, record the machine, operating system and driver versions, the
package hash, the model and the input dimensions. Attach the diagnostics from
*Copy diagnostics*, which contain hardware and version details but no file names
or media.

### Recorded results

- **macOS, M1 Pro 16 GB.** All cases with v0.1.1-beta, plus the in-app update
  from 0.1.0-beta.
- **Windows 11, i7-8550U, Intel UHD 620.** All cases with the CPU and DirectML
  test builds: HAT-S and SPAN images and short video, separate CPU and iGPU
  benchmarks, and upgrades through six package versions. The DirectML run
  executed on `privateuseone:0` with `aten::roll` falling back to the CPU.
- **Fedora 44, RX 9060 XT.** All cases with the CPU and ROCm workers, a
  five-minute 480×854 clip at 2× (9,000 frames in 99.5 minutes, audio intact),
  a six-frame 4K→8K export, recovery from a forced output-size failure, and
  short SeedVR2 3B FP8 exports (five 4K frames in 278 s at a 9.9 GiB peak).
- **Video formats.** Real fixtures for MPEG-4 Part 2/PCM AVI, MJPEG/ADPCM AVI,
  DivX/MP3, WMV2/WMA, MPEG-2/MP2, VOB MPEG-2/AC-3, H.264/AAC transport stream,
  FLV/MP3 and H.263/AAC 3GP decode with correct timestamps and soundtrack
  duration; a 352×288 interlaced anamorphic MPEG becomes 384×288 and matches
  an FFmpeg BWDIF reference. With the royalty-free runtime, the
  patent-encumbered codecs among these go through the user's FFmpeg. OGV is
  accepted by extension and has no real-file result yet.
- **Restoration guards.** Of eleven scanned documents that made NAFNet
  unstable, two are still rejected after bounded retries; no output is written
  for them.

## Reporting hardware results

Volunteer results on other GPUs are welcome. Open a
[bug report](https://github.com/HerRei/local-upscale/issues/new/choose) with the
device, driver, package hash, model, input size and the outcome, whether it
passed or failed. Please say which device the app reported as the processing
device, so a CPU fallback is not mistaken for a GPU result.
