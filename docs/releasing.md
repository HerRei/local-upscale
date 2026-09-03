# Native release process

`v0.0.11 Mac mini Cross Alpha` (`.github/workflows/v0.0.11-cross-alpha.yml`) is a one-release
exception that accepts only the exact `v0.0.11-alpha` tag. The normal
`Signed Tauri Alpha Release` pipeline explicitly skips that tag and continues to fail closed for
later signed releases. The former Slint matrix remains manual-only and cannot publish a `v*` tag.

## v0.0.11-alpha artifact matrix

| Platform | Bundled runtime | User-facing installer |
|---|---|---|
| Apple Silicon macOS 12+ | cross-built ARM64 PyTorch 2.2.2 / MPS | `LocalSR-v0.0.11-alpha-macOS-arm64.dmg` |
| Windows 10/11 x86-64 | maintained PyTorch 2.13 / CPU | `LocalSR-v0.0.11-alpha-Windows-x86_64.exe` |
| Linux x86-64 | maintained PyTorch 2.13 / CPU | `LocalSR-v0.0.11-alpha-Linux-x86_64.AppImage` |

The first Tauri alpha deliberately publishes one uncomplicated installer per supported operating
system. GPU-specific Windows and Linux engine packs remain a beta task; the UI and worker protocol
already preserve CUDA, ROCm, XPU, DirectML, MPS, and CPU identifiers, but this release does not claim
physical acceptance for packs it does not ship.

## Release gates

Linux and Windows install the actual package and start the bundled Rust host in headless smoke mode.
That host negotiates the production JSON-Lines protocol with the bundled Python worker. The Intel
Mac mini cannot execute an ARM64 package, so the macOS job recursively verifies the DMG/Mach-O tree
and records `runtime_tested=false`. No accessible physical ARM runner exists, so this release makes
no native-runtime claim. The builders produce private checksum, metadata, architecture, signing,
and smoke evidence.

The publishing job locates exactly the three manifest installers, streams their SHA-256 digests,
rejects duplicate content, and requires:

- a valid ARM64 Mach-O tree inside the macOS DMG;
- a valid x86-64 PE Windows installer;
- a valid x86-64 ELF AppImage;
- explicit ad-hoc/unsigned warnings for macOS and Windows;
- a passing installed-package worker smoke on Linux and Windows; and
- an honest static-only cross-build report for macOS rather than a fabricated runtime pass.

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

For v0.0.11-alpha, all of these must agree:

- tag: `v0.0.11-alpha`;
- Python, npm, Cargo, and Tauri version: `0.0.11-alpha`;
- legacy Inno metadata (kept reproducible): `0.0.11-alpha`;
- the three versioned filenames in `ci/tauri-cross-alpha-artifacts.json`;
- changelog, README, known limitations, acceptance notes, and release notes; and
- GitHub release title: `LocalSR v0.0.11-alpha`.

`scripts/check_release_version.py --tag v0.0.11-alpha` enforces this. Hyphenated tags are published
with `--prerelease`. The old Slint workflow is manual-only, so one tag cannot accidentally publish
both application architectures.

## Signing and notarization

The exact v0.0.11-alpha exception is not production signed. macOS receives Tauri's ad-hoc seal and
Windows has no Authenticode signature; Gatekeeper and SmartScreen may warn or reject them. This is
recorded in each metadata sidecar and the public release index. It is not permitted for any later
version or beta. The normal release pipeline continues to fail closed and requires the following:

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

At the time v0.0.11-alpha was prepared, none of these production signing secrets were configured.
They are hard beta gates, not requirements for this explicitly testing-only alpha.

## macOS cross-build exception

The physical Mac mini is Intel (`Macmini6,2`). Its boot-managed macOS x64 guest prepares a genuine
universal2 Python analysis environment from paired Intel/ARM wheels, emits an ARM64-only PyInstaller
worker, and cross-compiles the Rust/Tauri host for `aarch64-apple-darwin`. Recursive verification
rejects any wrong Mach-O slice.

PyTorch 2.2.2 is the final version with the paired macOS wheels needed by that process. It is known
security debt and cannot be the beta runtime. A later signed build must use maintained native ARM
PyTorch on real Apple-Silicon hardware; Intel macOS remains an open product decision.

## Publishing v0.0.11-alpha

1. Merge the release commit to `main` only after normal CI and Tauri CI are green.
2. Run `v0.0.11 Mac mini Cross Alpha` manually on that exact `main` commit and require all three
   build jobs to pass without publishing.
3. Create the annotated tag: `git tag -a v0.0.11-alpha -m "LocalSR v0.0.11-alpha"`.
4. Push the tag. All three Mac-mini package jobs must finish before the draft release is created.
5. Confirm the release is titled `LocalSR v0.0.11-alpha`, marked prerelease, and has exactly five
   assets: three installers, `SHA256SUMS`, and `release-index.json`.
6. Complete the physical-machine items in `docs/acceptance.md` before promoting this alpha to beta.

## Headless macOS runner startup

Both the manual legacy workflow and the one-release cross-alpha workflow use
`macmini-macos-x64`. The Linux host enables Docker at boot, the Dockur guest uses
`restart: always`, and the existing macOS login service starts the runner automatically. A full
guest restart acceptance check confirmed that the runner returned online without manual action.

The optional `macOS Runner Boot Maintenance` workflow can migrate the listener to a root-owned
pre-login LaunchDaemon after one-time administrator authorization. That additional hardening is not
required for the proven automatic cross-alpha restart path and does not replace the native
Apple-Silicon runtime/signing gate for beta.
