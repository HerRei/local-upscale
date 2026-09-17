# LocalSR

**Upscale and restore photos and video on your own computer.**

LocalSR is a free desktop app for AI image and video enhancement. It runs the
models locally, shows each tile as it finishes and lets you compare the result
with the original while the queue keeps working. Your media never leaves your
machine.

[**Download**](https://herrei.github.io/localsr/download/) ·
[Website](https://herrei.github.io/localsr/) · [Documentation](docs/README.md) ·
[Models](docs/models/README.md) · [Changelog](CHANGELOG.md)

![LocalSR on macOS comparing a photograph of Earth with its 4× upscale](docs/assets/localsr-desktop.png)

*The beta on an M1 Pro. Photo: NASA, Apollo 17 ([capture details](docs/assets/README.md)).*

## What it does

- **Upscale** photos, illustrations, screenshots and anime by 2×, 3× or 4×.
- **Restore** before upscaling: remove noise, motion blur and JPEG artifacts,
  with an optional face-aware pass.
- **Video.** Every image model runs frame by frame with the source timing,
  rotation and audio preserved. Phone and camera recordings open directly on
  macOS. SeedVR2 3B adds temporal consistency, and HLG/PQ HDR can be kept
  instead of tone-mapped (both Labs).
- **Say what you want, not which network.** Pick Quick or Best, photo or
  illustration, and what to fix first. The plan card shows which model runs at
  each stage, its license, its size and whether it fits your hardware. Save the
  setup as a recipe.
- **A model library with provenance.** Checkpoints download on demand, mostly
  from their authors' releases and otherwise from pinned mirrors checked against
  the official files. Each is pinned by size and SHA-256 and shown with its
  license and source ([provenance](docs/model-licenses.md)). Your own
  `.safetensors` models work too.
- **A queue that keeps you informed.** Images, videos and whole folders; real
  tiles and frame progress; estimates measured from completed work; side-by-side
  comparison, including synchronised video playback.
- **Automation and benchmarks.** A `localsr` command for scripts and watch
  folders, and separate CPU and GPU benchmarks.

Inputs: JPEG, PNG, WebP, TIFF and DNG camera RAW; MP4/MOV, MKV/WebM, AVI,
MPEG/VOB, transport streams, WMV, FLV, 3GP and OGV. Outputs: PNG, JPEG, TIFF and
WebP; AV1, VP9 or lossless FFV1 video, and H.264/HEVC through macOS's own codecs
or an FFmpeg you install. [Metadata and colour](docs/metadata.md) ·
[Video](docs/video-support.md)

## Download

**v0.1.2-beta** runs on Apple Silicon Macs with macOS 14 or later:
[download the DMG](https://herrei.github.io/localsr/download/) (414 MB, signed and
notarized). The app checks for updates at launch and verifies each update's
signature before installing it.

Windows (CPU, DirectML, CUDA) and Linux (CPU, ROCm, CUDA) packages come out of
the same pipeline but are not published yet. [Platforms](docs/platforms.md)
explains why, and what has been tested on which hardware.

No model weights are bundled. The Quick model (4.5 MB) downloads on first use,
the others when you choose them.

## Hardware

| Platform | Engines | Tested on |
| --- | --- | --- |
| macOS 14+, Apple Silicon | MPS | M1 Pro: images, video, cancellation, recovery, updates |
| Windows 10/11, x86-64 | CPU, DirectML, CUDA | Windows 11 with an Intel UHD 620 (CPU and DirectML); CUDA untested |
| Linux, x86-64 | CPU, AMD ROCm, NVIDIA CUDA | Fedora 44 with a Radeon RX 9060 XT (CPU and ROCm); CUDA untested |

Start with a small image or a short clip. Large images and video can take hours,
and 16 GB of GPU memory does not guarantee that a SeedVR2 job fits.
[Known limitations](KNOWN_LIMITATIONS.md)

## Models

The catalog holds 18 verified checkpoints: SPAN, RealPLKSR, HAT-S, HAT-L,
Real-ESRGAN and SwinIR for upscaling; NAFNet, SCUNet, FBCNN and RealPLKSR for
noise, blur and JPEG repair; SeedVR2 3B (FP16 and FP8) for video. Quick uses
SPAN NomosUni and Best uses RealPLKSR NomosWebPhoto, both by Philip Hofmann
under CC BY 4.0. The two HAT face companions have to be imported by hand
because their checkpoint rights are unresolved.
[Model guide](docs/models/README.md) · [Licenses](docs/model-licenses.md)

## Privacy

There is no account, and the media you process never leaves your computer.
LocalSR makes two kinds of network request: the update check at launch and the
model downloads you start. The update check also sends a count — the version,
platform and channel, with no identifier — so the project can tell how many
installs are active; *Send an anonymous update-check count* in the Software
Update dialog turns it off. *Copy diagnostics* includes versions, hardware and
memory figures, never file names or media. The full description is on the
[privacy page](https://herrei.github.io/localsr/privacy/).

## How it works

```text
Svelte interface
    ↕ typed Tauri commands and events
Rust host · queue, settings, downloads, updates, file access
    ↕ JSON Lines over stdin/stdout
Python worker · PyTorch, Spandrel, PyAV, LibRaw, face detection, SeedVR2
```

The webview has no shell, filesystem or network API. The Rust host grants the
worker access to the files you chose, keeps the queue in SQLite and verifies
every download. Inference runs in its own process, so a GPU or memory failure
ends the job rather than the app, and the host restarts the worker.
[Desktop guide](desktop/README.md) · [Worker protocol](protocol/README.md) ·
[Architecture decisions](docs/adr/)

## Build from source

You need Python 3.11, Node.js, a stable Rust toolchain and
[Tauri's platform prerequisites](https://v2.tauri.app/start/prerequisites/).

```sh
./local-ci.sh setup              # development dependencies
cd desktop && npm run tauri -- dev
```

`./local-ci.sh` runs every check that works on a development machine. Packaging,
signing and the release pipeline are described in [Releasing](docs/releasing.md).
The `localsr` command line works without the desktop app:
[Automation](docs/automation.md).

## Feedback

Bugs and feature requests go to
[GitHub Issues](https://github.com/HerRei/local-upscale/issues). Include the
version, model, device and steps; *Copy diagnostics* supplies the hardware
details. Security reports: [SECURITY.md](SECURITY.md). Anything else:
[hermes.reisner@gmail.com](mailto:hermes.reisner@gmail.com).

## License

The application is [MIT licensed](LICENSE). Models keep their authors' licenses,
shown in the app before download. Bundled libraries and their terms are listed in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md); every release ships the
corresponding source for its LGPL components.
