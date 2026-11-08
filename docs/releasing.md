# Releasing

Current release: **v0.3.0-beta**; release candidate: **v0.3.1-beta** for macOS, Windows and Linux. Release notes live in
[`releases/`](releases/), one file per tag; the release workflow uses that file
as the GitHub release body.

## Versions

One version string appears in `pyproject.toml`, `src/localsr/__init__.py`,
`desktop/package.json` and `package-lock.json`, `desktop/src-tauri/Cargo.toml`
and `Cargo.lock`, `desktop/src-tauri/tauri.conf.json`, `uv.lock`, the changelog
heading, the README, this page and `docs/releases/v<version>.md`.
`python scripts/check_release_version.py --tag v<version>` fails when any of them
disagree. For beta versions it also validates `ci/public-beta-release.json` and
the readiness register; `python scripts/check_beta_readiness.py
--require-beta-ready` reports the gates that are still open. Tags with a hyphen
are published as prereleases.

## Release train

Every 14 days `release-train.yml` opens a `Version X` PR made by
`scripts/bump_version.py` ([dependency-updates.md](dependency-updates.md) has the
rules). `bump_version.py X.Y.Z-beta` (or `--next`) does the same by hand. After the
packages are live, `bump_version.py --mark-published` clears the candidate markers.

Before building a candidate on the Mac:

1. Check out the merged commit exactly (`git switch --detach <sha>`); the Windows and
   Linux workflows get a tag or branch at that same commit (`--ref`). Check the built
   source commit and version before attaching anything.
2. Bring the release venv up to the merged dependencies:
   `uv pip install --python .venv/bin/python -e '.[video,face,package,macos-release]'
   --upgrade-package <each runtime package merged since the last release>`. Never
   upgrade `av` or `opencv*` this way: they are the licensing-clean builds from
   `packaging/ffmpeg/install_media_runtime.py`. When their pinned versions change,
   rerun that script. Either way `verify_codec_allowlist.py` must pass.
3. `release_macos_beta.sh` writes `mac-environment.txt` (the venv's `uv pip freeze`)
   next to the build. Diff it against the previous release's copy and put the diff
   in the release PR.

## macOS beta

`scripts/release_macos_beta.sh` builds the app from a committed tree (the
packaging step also compiles `localsr-media` with `swiftc` into the engine
directory), signs it with the Developer ID identity, notarizes and staples the app and the DMG,
packages the update archive and the engine payload, assembles the corresponding
source, and writes `beta.json`, `release.json` and `SHA256SUMS` under
`build/release-<version>/`. It writes the update archive without AppleDouble
entries: the updater strips the bundle name from every path, so a hidden
`._LocalSR.app` entry becomes an empty path and the install fails.

The disk image opens as one Finder window with LocalSR on the left, the
Applications shortcut on the right and an arrow between them.
`scripts/make_macos_dmg.py` builds it with dmgbuild (the `macos-release` extra:
`uv pip install --python .venv/bin/python 'dmgbuild>=1.6.7,<2'`); the layout
constants live there, and `packaging/macos/installer/build_background.py`
renders the 1x and 2x backgrounds from them. Re-render and commit the
backgrounds after changing the layout.

The script needs the media runtime's corresponding source in this checkout —
`build/lgpl-media/{dist,opencv}/corresponding-source` and the tarballs in
`build/extra-sources/` — and refuses to start without them, because they are
only used by the last step. A fresh worktree gets them by building the runtime
(`packaging/ffmpeg/install_media_runtime.py`) or by copying `build/lgpl-media`
and `build/extra-sources` from a worktree that already has them.

`LOCALSR_FEED_NOTES` sets the text the update dialog shows for this release;
without it the entry points at the release-notes page.

Never edit the script while it runs: bash reads it incrementally, so a changed
file resumes at the wrong offset.

After the build:

Public installers, source bundles and updater files use
`https://github.com/HerRei/local-upscale/releases/download/v<version>/`.

1. Upload the DMG, the update archive and its signature, the engine payload and
   its parts, the source bundle, the `.sha256` files and `SHA256SUMS` to the
   **draft** GitHub release. Publish only after verifying its exact files.
2. Download the draft release's files and verify them with
   `shasum -a 256 -c SHA256SUMS`.
3. Install the previous version on a test Mac and confirm that the update
   downloads, installs and relaunches on the new version.
4. Add the entry to the website's `updates/beta.json`, update `release.json` and
   run `localsr/tools/sync_release.py` and `validate_site.py`, then publish.
   GitHub Pages caches the feed for ten minutes.

The release requires a Developer ID signing identity and notarization credentials.
The updater signing key is managed by `scripts/beta_update_signing.py`; see
[Updates](updates.md).

## Signed release workflow

`.github/workflows/desktop-release.yml` builds every target in
`ci/tauri-targets.json` and fails without production signing.

### Targets

| Platform | Engines | Runtime |
| --- | --- | --- |
| macOS, Apple Silicon | MPS | Torch 2.13, native ARM64 runner |
| Windows 10/11, x86-64 | CPU, CUDA, DirectML | Torch 2.13; CUDA ships as an installer plus verified external engine payloads; DirectML through ONNX Runtime |
| Linux, x86-64 | CPU, CUDA, AMD ROCm | separate Torch 2.13 distributions |

Intel XPU is listed under `withheld_targets`; see
[media formats and licensing](licensing-media.md).

`ci/tauri-release-artifacts.json`, written by `scripts/release_targets.py
--write-manifests`, must match the registry exactly. `requirements/locks/` holds
the reviewed Windows and Linux dependency locks with hashes; refresh them with
`scripts/lock_backend_requirements.py` and uv 0.12.8. Builders install offline
from verified wheelhouses prepared by `backend_wheelhouse.py`.

Windows CUDA's installer embeds its engine manifest; the Rust host verifies the
adjacent 1,900 MiB payload parts before extracting them into a staging directory
and promoting them atomically. Oversized Linux AppImages are published as
verified parts plus a shell helper that reconstructs the executable.
`release-index.json` lists the exact files of each distribution.

### Gates

Every platform installs the package it built and starts the bundled host in
headless smoke mode, which must complete the worker handshake. The publishing
job streams the SHA-256 of every distribution, rejects duplicate content and
requires a valid ARM64 Mach-O tree in the DMG, an x86-64 PE installer, an
x86-64 ELF AppImage, Developer ID and notarization evidence for macOS,
Authenticode evidence for Windows and a passing installed smoke test with a
verified backend identity on every target. It downloads the draft release again
and verifies every checksum before making it visible; published releases are
never overwritten.

Public files are the installers, payload parts and helpers, `SHA256SUMS` and
`release-index.json`. Builders send their evidence to the private receiver with
timestamped, nonce-bound HMAC; the token itself is never transmitted.

### Signing secrets

macOS: `MACOS_CERTIFICATE_P12_BASE64`, `MACOS_CERTIFICATE_PASSWORD`,
`MACOS_SIGNING_IDENTITY`, `MACOS_NOTARY_APPLE_ID`, `MACOS_NOTARY_PASSWORD`,
`MACOS_TEAM_ID`. Tauri signs, submits and staples; the verifier checks the
signature chain, the Team ID, both tickets and Gatekeeper acceptance.

Windows: `WINDOWS_CERTIFICATE_PFX_BASE64`, `WINDOWS_CERTIFICATE_PASSWORD`,
`WINDOWS_CERTIFICATE_THUMBPRINT`, `WINDOWS_TIMESTAMP_URL`. The certificate is
imported into the runner user's temporary store, Tauri signs the host and the
NSIS installer, and the verifier requires a matching signer and timestamp. The
beta's direct Windows installers are unsigned until a certificate is bought;
the Microsoft Store package is signed by the Store.

### Failure recovery

- Transient failure, same commit: re-run the failed jobs. Verified platform
  artifacts from earlier attempts of the same run are reused.
- Fix requires a new commit: start a new run and rebuild every platform.
- Never present binaries from an older commit as products of a newer one.

### Runners

`linux-appimages.yml`, `windows-installers.yml`, PR CI and Preview use
GitHub-hosted runners; see [ADR 0008](adr/0008-hosted-ci.md).

`desktop-release.yml` remains manual-only with self-hosted Linux/Windows legs
and hosted macOS. It requires provisioned runners and is a candidate for a
separate cleanup, together with the manual maintenance workflows and scripts.
