# Native release process

`Signed Tauri Alpha Release` (`.github/workflows/desktop-release.yml`) is the tag-triggered release
pipeline. The former Slint archive matrix remains available as the manual-only `Legacy Slint Build`
workflow; it cannot publish a `v*` tag and is not overwritten.

## v0.0.10-alpha artifact matrix

| Platform | Bundled runtime | User-facing installer |
|---|---|---|
| Apple Silicon macOS 12+ | native ARM64 PyTorch 2.13 / MPS | `LocalSR-v0.0.10-alpha-macOS-arm64.dmg` |
| Windows 10/11 x86-64 | maintained PyTorch 2.13 / CPU | `LocalSR-v0.0.10-alpha-Windows-x86_64.exe` |
| Linux x86-64 | maintained PyTorch 2.13 / CPU | `LocalSR-v0.0.10-alpha-Linux-x86_64.AppImage` |

The first Tauri alpha deliberately publishes one uncomplicated installer per supported operating
system. GPU-specific Windows and Linux engine packs remain a beta task; the UI and worker protocol
already preserve CUDA, ROCm, XPU, DirectML, MPS, and CPU identifiers, but this release does not claim
physical acceptance for packs it does not ship.

## Release gates

Every builder installs or mounts the actual package and starts the bundled Rust host in headless
smoke mode. That host negotiates the production JSON-Lines protocol with the bundled Python worker.
The builders then produce private checksum, metadata, architecture, signing, and smoke evidence.

The publishing job locates exactly the three manifest installers, streams their SHA-256 digests,
rejects duplicate content, and requires:

- a valid ARM64 Mach-O tree inside the macOS DMG;
- a valid x86-64 PE Windows installer;
- a valid x86-64 ELF AppImage;
- Developer ID, notarization, stapling, and Gatekeeper evidence for macOS;
- timestamped Authenticode evidence for Windows; and
- a passing installed-package worker smoke report on every platform.

Only five files are public: the three installers, `SHA256SUMS`, and `release-index.json`. The index
embeds the private provenance/signing/architecture/smoke evidence so users are not faced with dozens
of sidecars. The workflow downloads the draft release again and verifies `SHA256SUMS` before making
it visible. An existing release is immutable: reruns refuse to use `--clobber` and require a version
bump instead.

Builders send completed evidence to the private receiver with timestamped, nonce-bound HMAC. The
reusable `CI_ARTIFACT_TOKEN` is never transmitted, and the receiver rejects stale signatures and
replays. The LAN endpoint remains HTTP, so HMAC protects authentication and integrity rather than
confidentiality; release payloads are intended for publication after the final gate.

## Version synchronization

For v0.0.10-alpha, all of these must agree:

- tag: `v0.0.10-alpha`;
- Python, npm, Cargo, and Tauri version: `0.0.10-alpha`;
- legacy Inno metadata (kept reproducible): `0.0.10-alpha`;
- the three versioned filenames in `ci/tauri-release-artifacts.json`;
- changelog, README, known limitations, acceptance notes, and release notes; and
- GitHub release title: `LocalSR v0.0.10-alpha`.

`scripts/check_release_version.py --tag v0.0.10-alpha` enforces this. Hyphenated tags are published
with `--prerelease`. The old Slint workflow is manual-only, so one tag cannot accidentally publish
both application architectures.

## Signing and notarization

The pipeline fails closed. It will not substitute ad-hoc or unsigned public downloads.

macOS requires all of:

- `MACOS_CERTIFICATE_P12_BASE64`;
- `MACOS_CERTIFICATE_PASSWORD`;
- `MACOS_SIGNING_IDENTITY` (Developer ID Application);
- `MACOS_NOTARY_APPLE_ID`;
- `MACOS_NOTARY_PASSWORD` (app-specific Apple ID password); and
- `MACOS_TEAM_ID`.

Tauri signs the app, submits it to Apple, and staples the ticket. The release verifier recursively
checks the signature, Developer ID authority and Team ID, validates the app and DMG tickets, and
requires Gatekeeper acceptance.

Windows requires:

- `WINDOWS_CERTIFICATE_PFX_BASE64`;
- `WINDOWS_CERTIFICATE_PASSWORD`;
- `WINDOWS_CERTIFICATE_THUMBPRINT`; and
- `WINDOWS_TIMESTAMP_URL` from the certificate provider.

The certificate is imported into the runner user's temporary certificate store, Tauri signs the
host and NSIS installer, and the release verifier requires a valid matching signer and timestamp.
The certificate and bounded scratch directory are removed after the job.

At the time the v0.0.10-alpha candidate was prepared, none of these production signing secrets were
configured. Do not tag or claim the release exists until they are supplied.

## Native macOS runtime

The new macOS package refuses the former PyTorch 2.2.2 universal cross-build. It must run on a
genuinely native `[self-hosted, macOS, ARM64]` runner and verifies both `uname`/Python architecture
and the installed PyTorch 2.13 line. Intel macOS remains an open support decision and is not
advertised by this alpha candidate.

The repository currently has no registered online ARM64 Actions runner. Register the Mac mini's
native ARM64 runner and install its boot service before tagging. Do not relabel an x86-64/Rosetta
runner as ARM64.

## Publishing v0.0.10-alpha

1. Supply the Apple and Windows signing secrets and register the native ARM64 macOS runner.
2. Merge the release commit to `main` only after normal CI and Tauri CI are green.
3. Create the annotated tag: `git tag -a v0.0.10-alpha -m "LocalSR v0.0.10-alpha"`.
4. Push the tag. All three signed package jobs must finish before the draft release is created.
5. Confirm the release is titled `LocalSR v0.0.10-alpha`, marked prerelease, and has exactly five
   assets: three installers, `SHA256SUMS`, and `release-index.json`.
6. Complete the physical-machine items in `docs/acceptance.md` before promoting this alpha to beta.

## Headless legacy macOS runner startup

The manual legacy workflow can still use the boot-managed `macmini-macos-x64` runner. Its
LaunchDaemon starts at the login window and waits for `/Volumes/CISCRATCH`. This does not satisfy the
new native ARM64 package requirement; the Mac mini needs a separately registered native ARM64
runner identity and matching boot service for the signed Tauri release.
