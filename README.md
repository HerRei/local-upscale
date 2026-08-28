# LocalSR

[![CI](https://github.com/HerRei/local-upscale/actions/workflows/ci.yml/badge.svg)](https://github.com/HerRei/local-upscale/actions/workflows/ci.yml)

LocalSR is a private, local-first desktop application for image restoration and super-resolution.
Media and downloaded model weights stay on the computer; the app has no cloud processing or
analytics. The current release line is **v0.0.8-alpha**.

## Features

- Upscale photos, screenshots, anime, and illustrations at 2×, 3×, or 4×.
- Denoise and deblur images at their original dimensions.
- Process JPEG, PNG, WebP, TIFF, and DNG camera RAW input with ICC-aware output.
- Queue images and run one accelerator-safe job at a time.
- Use Quick Start or Best Quality recipes, or choose a model and hardware settings manually.
- Download curated models on demand with pinned sizes and SHA-256 verification.
- Cancel cooperatively, estimate memory/disk/time, and preview completed tiles.
- Run experimental video upscaling locally. Video and SeedVR2 are explicitly **Labs / Experimental**
  during alpha and beta.
- Copy a privacy-filtered diagnostic summary from Advanced settings. Paths, media names, and image
  contents are omitted.

LocalSR does not include generative fill, outpainting, cloud inference, telemetry, or bundled model
weights. Custom pickle-based `.pth` and `.pt` checkpoints can execute code; load only checkpoints
from sources you trust.

## Platform and release artifacts

The v0.0.8-alpha release workflow produces portable archives, not DMGs, AppImages, MSIs, or Windows
Setup installers:

| Platform | Architecture/backend | Artifact |
|---|---|---|
| macOS 12+ | Apple Silicon / MPS | `LocalSR-macOS-arm64.tar.gz` |
| macOS 12+ | Intel / CPU | `LocalSR-macOS-x86_64.tar.gz` |
| Windows 10/11 | x86-64 / CPU | `LocalSR-Windows-CPU-x86_64.zip` |
| Windows 10/11 | x86-64 / DirectML | `LocalSR-Windows-DirectML-x86_64.zip` |
| Windows 10/11 | x86-64 / NVIDIA CUDA | `LocalSR-Windows-CUDA-x86_64.zip` |
| Linux x86-64 | CPU, CUDA, Intel XPU, or ROCm | one backend-specific `.tar.gz` |

Every archive has a checksum, provenance metadata, and a native-binary architecture report. The
release gate parses PE, ELF, and Mach-O headers and rejects duplicate archive digests. Alpha
suffixes are published as GitHub prereleases.

The release also publishes `beta-readiness.json`, an intentionally honest machine-readable snapshot
of completed and unresolved beta gates. It prevents packaging success from being confused with
physical-device, signing, licensing, or public-access acceptance.

Production Apple Developer ID/Authenticode credentials are not configured yet. The v0.0.8-alpha
downloads are therefore not suitable for a public beta and may trigger operating-system trust
warnings. The macOS pipeline is ready to sign, notarize, staple, and Gatekeeper-check the `.app`
when the documented secrets are supplied. See [release documentation](docs/releasing.md) and
[known limitations](KNOWN_LIMITATIONS.md).

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
| HAT-S ×4 Face | face restoration | 40 MB | CC BY-NC-SA 4.0 |
| RealPLKSR Denoise ×1 | fast photo denoising | 30 MB | CC-BY-4.0 |
| NAFNet SIDD Width64 ×1 | camera-noise removal | 464 MB | MIT |
| NAFNet GoPro Deblur ×1 | motion deblurring | 272 MB | MIT |

Quick Start currently selects SPAN NomosUni. Best Quality selects RealPLKSR NomosWebPhoto. A
release preflight downloads both into a genuinely empty cache, validates their embedded checksums,
loads them through Spandrel, and runs real CPU inference.

The HAT-S Face checkpoint is **non-commercial only** under CC BY-NC-SA 4.0. The UI labels this
restriction. It is not silently included in Quick or Best and is not licensed for a commercial
workflow merely because the LocalSR application source is MIT.

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
python -m pip install -e ".[video]"
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

The portable alpha archives do not run an installer automatically. Desktop integration claims in
older documentation referred to planned installer formats and should not be read as current release
artifacts.

## Development and tests

```bash
python -m pip install -e ".[dev,video,package]"
python -m pytest -q
ruff check src tests scripts smoke_test_gui.py
ruff format --check src tests scripts smoke_test_gui.py
python -m localsr.ui.slint_check
python scripts/validate_live_models.py
```

The normal test suite is offline and deterministic. `validate_live_models.py` is deliberately a
networked acceptance check and downloads the real Quick and Best checkpoints. Release builds also
smoke-test the frozen worker and verify every archive against [the artifact manifest](ci/release-artifacts.json).

## Feedback and diagnostics

Repository members can use [GitHub Issues](https://github.com/HerRei/local-upscale/issues). Include
the result of **Advanced settings → Copy Diagnostics**, the source format/dimensions, selected model,
and reproduction steps. Do not attach private source media unless you intend to share it.

The repository is currently private, so this is not yet an accessible intake route for the general
public. Choosing and opening a public feedback channel remains a public-beta decision; it is tracked
as a release gate rather than being represented as solved.

## Licensing

LocalSR source is distributed under the [MIT License](LICENSE). Official UI builds use Slint under
its royalty-free/community option and display Slint's official `AboutSlint` attribution widget in
Advanced settings. Slint, PyTorch, Spandrel, vendored SeedVR2 code, and downloaded checkpoints retain
their own licenses. See [third-party notices](THIRD_PARTY_NOTICES.md) before redistributing a build.
