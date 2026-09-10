# LocalSR

[![CI](https://github.com/HerRei/local-upscale/actions/workflows/ci.yml/badge.svg)](https://github.com/HerRei/local-upscale/actions/workflows/ci.yml)

LocalSR is a private, local-first desktop application for image restoration and super-resolution.
Media and downloaded model weights stay on the computer; the app has no cloud processing or
analytics. The current release candidate is **v0.0.12-alpha**.

## Features

- Upscale photos, screenshots, anime, and illustrations at 2×, 3×, or 4×.
- Denoise and deblur images at their original dimensions.
- Process JPEG, PNG, WebP, TIFF, and DNG camera RAW input with ICC-aware output.
- Queue images and videos together with per-item models and run one accelerator-safe job at a time.
- Use Quick Start or Best Quality recipes, or choose a model and hardware settings manually.
- Download curated models on demand with pinned sizes and SHA-256 verification.
- Cancel cooperatively, estimate memory/disk/time, and preview completed tiles.
- Upscale SDR video locally with source timing, rotation, and compatible audio preserved.
  SeedVR2, de-flicker, and video face processing have individual **Labs** labels.
  See the [video support contract](docs/video-support.md) for acceptance boundaries.
- Copy a privacy-filtered diagnostic summary from Advanced settings. Paths, media names, and image
  contents are omitted.

LocalSR does not include generative fill, outpainting, cloud inference, telemetry, or bundled model
weights. Custom `.safetensors` checkpoints are accepted by default. Unverified pickle/TorchScript
`.pth`, `.pt`, and `.ckpt` files are blocked unless the user explicitly sets
`LOCALSR_ALLOW_UNVERIFIED_CHECKPOINTS=1`; enabling that override treats the checkpoint as code.

## Install the alpha

The Mac-mini cross-release pipeline publishes the [v0.0.12-alpha
prerelease](https://github.com/HerRei/local-upscale/releases/tag/v0.0.12-alpha) only after its
testing-only platform gates pass. Choose the distribution for your OS and backend:

| Platform | Backend choices | Download |
|---|---|---|
| Apple Silicon macOS 12+ | MPS | `LocalSR-v0.0.12-alpha-macOS-arm64.dmg` |
| Windows 10/11 x86-64 | CPU, DirectML, CUDA | The corresponding `Windows-CPU`, `Windows-DirectML`, or `Windows-CUDA` installer |
| Linux x86-64 | CPU, CUDA, Intel XPU, AMD ROCm | The corresponding `Linux-CPU`, `Linux-CUDA`, `Linux-Intel`, or `Linux-ROCm` AppImage |

For **Windows CUDA**, download its `.exe` and every matching `.engine.tar.gz.part-*` file
into the same folder, then run the installer. It verifies and installs the existing inference
engine automatically; no Python or pip setup is required. Keep the payload files for offline
engine installation.

Large Linux AppImages are supplied as `.part-*` files with a matching `.restore.sh` helper.
Download that complete set, run `bash <matching-file>.restore.sh`, then launch the reconstructed
AppImage. The helper checks each part and the complete AppImage before making it executable.

`SHA256SUMS` covers all public installers and payload files. `release-index.json` records each
backend's exact download set, checksums, build commit, and validation evidence. This alpha is
ad-hoc sealed on macOS and unsigned on Windows; production signing and physical GPU acceptance
remain pending. Published releases remain immutable.

Because the repository is private, testers still need repository access. Choosing a public download
location remains a beta decision.

## Platform and advanced downloads

The v0.0.12-alpha candidate uses the Tauri/Svelte host. It coexists with the former
Slint app under a distinct bundle identifier and state directory, so installing it does not replace
an older LocalSR installation:

| Platform | Architecture/backend | Artifact |
|---|---|---|
| macOS 12+ | Apple Silicon / MPS | ad-hoc, Intel→ARM cross-built `.dmg` |
| Windows 10/11 | x86-64 / CPU, CUDA, DirectML | unsigned NSIS; CUDA uses adjacent payloads |
| Linux x86-64 | CPU, CUDA, XPU, ROCm | `.AppImage` or verified parts plus restore helper |

Linux and Windows installers are installed in CI and start their bundled worker. The Intel Mac mini
cannot execute ARM64, so the DMG receives recursive ARM64 Mach-O and package inspection only. No
accessible physical Apple-Silicon runner supplied native runtime acceptance. This limitation and
all checksum, provenance, signing, and smoke evidence are recorded in `release-index.json`.

The release index embeds an intentionally honest machine-readable snapshot of completed and
unresolved beta gates. It prevents packaging success from being confused with physical-device,
signing, licensing, or public-access acceptance.

Production Apple Developer ID/Authenticode credentials are not configured yet. They remain hard
beta gates rather than blocking this explicitly unsigned alpha. See [release documentation](docs/releasing.md)
and [known limitations](KNOWN_LIMITATIONS.md).

Homebrew is intentionally not advertised for this alpha: the old formula used placeholder hashes
and inconsistent tap names. It should return only after signed release assets have stable URLs and
real checksums.

## Curated models

| Model | Primary use | Approx. download | License |
|---|---|---:|---|
| SPAN 4x NomosUni — Quick | fast photo upscaling | 4.5 MB | CC-BY-4.0 |
| RealPLKSR 4x NomosWebPhoto — Best | high-quality photo upscaling | 29.7 MB | upstream says `CC-BY-0.4`; clarify |
| RealPLKSR 4x HFA2k — Anime | anime and line art | 29.7 MB | upstream says `CC-BY-0.4`; clarify |
| HAT-S ×4 | general high quality | 81 MB | Apache-2.0 |
| HAT-L ×4 ImageNet | large/high-cost model | 166 MB | Apache-2.0 |
| Real-ESRGAN ×2 | native compact 2× upscaling | 67 MB | BSD-3-Clause |
| FBCNN Color ×1 | JPEG artifact restoration | 288 MB | Apache-2.0 |
| HAT-S ×4 Face | optional user-supplied face restoration | 40 MB | checkpoint rights unresolved |
| HAT-L ×4 Face | optional user-supplied face restoration (large) | 166 MB | checkpoint rights unresolved |
| RealPLKSR Denoise ×1 | fast photo denoising | 30 MB | CC-BY-4.0 |
| NAFNet SIDD Width64 ×1 | camera-noise removal | 464 MB | MIT |
| NAFNet GoPro Deblur ×1 | motion deblurring | 272 MB | MIT |

Quick Start currently selects SPAN NomosUni. Best Quality selects RealPLKSR NomosWebPhoto. A
release preflight downloads both into a genuinely empty cache, validates their embedded checksums,
loads them through Spandrel, and runs real CPU inference.

Model cards: [HAT-S Face](docs/models/hat-s-face.md) · [HAT-L Face](docs/models/hat-l-face.md).
The completed two-model desktop integration is on `codex/hat-face-models` for a later
release; it does not alter the ongoing v0.0.12-alpha build. In Tauri, select stock
HAT-S or HAT-L and enable its corresponding face-aware companion after importing
an exact, verified checkpoint. A local face detector is required.

The HAT-S Face implementation remains available for an exact user-supplied compatible checkpoint,
but the current asset's independent training-data, redistribution, and use terms could not be
verified. LocalSR does not automatically download it or make a commercial-use claim. The same
policy applies to the larger HAT-L Face checkpoint, a private L1-only face fine-tune blended back
toward the stock ImageNet HAT-L weights (it restores degraded faces better than stock HAT-L while
staying close on clean images; use stock HAT-L for general work). Face masks
come from the separately MIT-licensed YuNet 2023mar detector, downloaded on first face-aware use
with a pinned size and SHA-256. See the
[checkpoint evidence and policy](docs/model-licenses.md).

The upstream [Best release](https://github.com/Phhofm/models/releases/tag/4xNomosWebPhoto_RealPLKSR)
and [anime release](https://github.com/Phhofm/models/releases/tag/4xHFA2k_ludvae_realplksr_dysample)
spell their license `CC-BY-0.4`, which is not a standard Creative Commons identifier. LocalSR labels
their commercial terms unclear instead of assuming the author meant CC BY 4.0. Obtain clarification
before treating those checkpoints as commercially licensed.

SeedVR2 model bundles are multi-gigabyte optional Labs downloads. Their code attribution, license,
configuration YAML, and embeddings are included in wheels, source distributions, and standalone
bundles; the model weights remain external.

## Install from source

Python 3.11 is required. The interactive installer selects an appropriate PyTorch backend:

```bash
python3 install.py
```

Or create an environment directly:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[video,face]"
python -m localsr
```

On Windows, activate with `.venv\Scripts\activate` instead. GPU support requires a compatible
driver and the matching PyTorch build. AMD ROCm uses PyTorch's CUDA-shaped API internally but is
shown as ROCm in the app.

## Desktop integration and CLI

LocalSR supports native file/folder dialogs, completion notifications, file ingestion, and local
recipes. Registration is explicit:

```bash
localsr --install-integrations
localsr --uninstall-integrations
localsr photo.png --preset quick --auto-start
localsr video.mp4 --recipe "My Video Recipe" --auto-start
```

The native installers register the normal application entry. Optional Finder, Explorer, Nautilus,
KDE, and desktop actions are installed explicitly from LocalSR's native menu.

The Python entry point also exposes production-path noninteractive processing, a restart-safe watch
folder, and the versioned benchmark:

```bash
localsr process photo.png clip.mkv --output ./enhanced --model span_photo_x4 --json
localsr watch ./incoming --output ./enhanced --stable-seconds 2 --json
localsr benchmark --device auto --json
```

Automation never downloads a model implicitly or overwrites input/existing output files. See the
[automation contract](docs/automation.md), [benchmark definition](docs/benchmark.md), and exact
[metadata/color behavior](docs/metadata.md).

## Development and tests

```bash
./local-ci.sh setup
./local-ci.sh
```

The local pipeline checks Python, Svelte, Rust, workflow syntax, repository policy,
and Python package contents. It reuses installed dependencies and stops at the
first failure. See [development checks and code boundaries](docs/development.md)
for prerequisites, individual commands, and the initial Python type-checking scope.

The normal test suite is offline and deterministic. `validate_live_models.py` is deliberately a
networked acceptance check and downloads the real Quick and Best checkpoints. Release builds also
smoke-test the frozen worker and verify every archive against [the artifact manifest](ci/release-artifacts.json).

The Tauri/Svelte host lives under [`desktop/`](desktop/README.md). Rust owns native authority,
durable queueing, model installation, and worker recovery; the isolated Python worker retains broad
PyTorch/Spandrel/video compatibility. The legacy Slint build remains manually reproducible and its
installed app is not overwritten.

## Feedback and diagnostics

Repository members can use [GitHub Issues](https://github.com/HerRei/local-upscale/issues). Include
the result of **Advanced settings → Copy Diagnostics**, the source format/dimensions, selected model,
and reproduction steps. Do not attach private source media unless you intend to share it.

The repository is currently private, so this is not yet an accessible intake route for the general
public. Choosing and opening a public feedback channel remains a public-beta decision; it is tracked
as a release gate rather than being represented as solved.

## Licensing

LocalSR's Tauri/Rust/Svelte application harness is distributed under the [MIT License](LICENSE).
PyTorch, Spandrel, the retained legacy Slint host, vendored SeedVR2 code, and externally downloaded
checkpoints retain their own licenses. Model files are not relicensed or bundled merely because the
app offers a verified download. See [third-party notices](THIRD_PARTY_NOTICES.md) before
redistributing a build.
