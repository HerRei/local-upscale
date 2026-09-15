# Releasing

Current release: **v0.1.2-beta** for macOS. Release notes live in
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

## macOS beta

`scripts/release_macos_beta.sh` builds the app from a committed tree (the
packaging step also compiles `localsr-media` with `swiftc` into the engine
directory), signs it with the Developer ID identity, notarizes and staples the app and the DMG,
packages the update archive and the engine payload, and writes `beta.json`,
`release.json` and `SHA256SUMS` under `build/release-<version>/`. It writes the
update archive without AppleDouble entries: the updater strips the bundle name
from every path, so a hidden `._LocalSR.app` entry becomes an empty path and the
install fails.

After the build:

1. Upload the DMG, the update archive, its signature and the engine payload to
   the download host.
2. Install the previous version on a test Mac and confirm that the update
   installs from the feed.
3. Add the entry to the website's `updates/beta.json` and publish the download
   page.

The signing identity (`Developer ID Application: Hermes Reisner`) and the
notarization profile `LocalSR-Z2TU844D84-notary` live in the login Keychain of
the release Mac and never in the repository. To renew the notarization
credentials, create an app-specific password and run:

```sh
xcrun notarytool store-credentials LocalSR-Z2TU844D84-notary \
  --apple-id YOUR_APPLE_ID --team-id Z2TU844D84
```

The tool prompts for the password. The updater key is handled the same way by
`scripts/beta_update_signing.py`; see [Updates](updates.md).

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
`scripts/lock_backend_requirements.py` and uv 0.12.8. Builders reuse verified
wheelhouses (`backend_wheelhouse.py`) from `/ci-scratch/wheelhouses` or
`C:\lsr-ci\wheelhouses` and install offline. Cargo output stays under
`cargo-target/tauri-*`; only bundle output is cleared between variants. The
registry budgets 15–100 GiB of scratch per backend; ROCm needs the most because
its wheel, expanded runtime, frozen worker, AppDir and smoke extraction can
coexist.

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

The Mac mini's `macmini-macos-x64` runner starts on its own: Docker starts at
boot, the guest restarts automatically and a login service starts the runner.
The `macOS Runner Boot Maintenance` workflow can move the listener to a
pre-login LaunchDaemon. The Windows runner scripts live in `infra/windows/`.
