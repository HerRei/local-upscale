# Native release process

`v0.0.12 Mac mini Cross Alpha` (`.github/workflows/v0.0.12-cross-alpha.yml`) is a one-release
exception that accepts only the exact `v0.0.12-alpha` tag. The normal
`Signed Tauri Alpha Release` pipeline explicitly skips that tag and continues to fail closed for
later signed releases. The former Slint matrix remains manual-only and cannot publish a `v*` tag.

## v0.0.12-alpha artifact matrix

The canonical target registry is `ci/tauri-targets.json`. Both desktop workflows expand their
Windows/Linux matrices from it, and generated manifests must match it exactly. The legacy manual
workflow retains its existing nine targets, including Intel macOS CPU.

| Platform | Backend variants | Runtime |
|---|---|---|
| Apple Silicon macOS 12+ | MPS | cross-built Torch 2.2.2; static verification only |
| Windows 10/11 x86-64 | CPU, CUDA | Torch 2.13; CUDA uses an installer plus verified external payloads |
| Windows 10/11 x86-64 | DirectML | torch-directml 0.2.5.dev240914, Torch 2.4.1, torchvision 0.19.1 |
| Linux x86-64 | CPU, CUDA, Intel XPU, AMD ROCm | Separate Torch 2.13 distributions |

`requirements/locks/` contains the reviewed transitive Windows/Linux dependency locks and hashes.
Refresh them deliberately with `scripts/lock_backend_requirements.py` and uv 0.12.8; releases use
`backend_wheelhouse.py` to build/reuse verified wheelhouses and install offline. The special
universal2 Mac recipe remains separately pinned. Cached wheels live outside disposable scratch:
`/mnt/hdd/ci-scratch/wheelhouses` (cross-alpha Linux), `/ci-scratch/wheelhouses`
(signed Linux lane), or `C:\lsr-ci\wheelhouses`. Cargo build outputs are retained under
`cargo-target/tauri-*`; only bundle output is cleared between variants. Heavy jobs remain serialized.
Monitor disk reserves and remove obsolete wheelhouse keys only when no active build uses them.
The cross-alpha Linux job archives the exact checkout into its bounded HDD workspace and keeps
its venv, frozen worker, Cargo outputs, wheelhouses, and AppImages there. The 110 GiB system SSD
also holds the macOS guest disk and cannot accommodate the 100 GiB ROCm scratch budget. The job
requires `/mnt/hdd` to be mounted, checks both volumes, and retains the HDD reserve; it never falls
back to the SSD when that mount is missing. This trades build I/O speed for sufficient capacity.
The registry budgets peak working space per backend before dependency installation (15–100 GiB,
plus the protected disk reserve). ROCm's budget is largest because its wheel, expanded runtime,
frozen worker, AppDir, and smoke-test extraction can coexist. These are conservative estimates,
not measured guarantees; retain observed peak usage in the release handoff and tune from evidence.

Windows CUDA's small NSIS installer embeds its engine manifest. Adjacent 1,900 MiB payload parts
are checked by the Rust host before extraction into a staging directory and atomic promotion.
Missing or corrupted files abort installation. The installed frozen worker receives the same
identity/CPU-inference probe as the other variants. This does not change worker packaging to
portable Python and does not claim GPU execution on the packaging VM.

Oversized Linux AppImages are published as verified parts plus a generated shell helper that
reconstructs and verifies the original executable. `release-index.json` lists the exact files for
each logical distribution; public file counts therefore vary with payload size.

## Release gates

Linux and Windows install the actual package and start the bundled Rust host in headless smoke mode.
That host negotiates the production JSON-Lines protocol with the bundled Python worker. The Intel
Mac mini cannot execute an ARM64 package, so the macOS job recursively verifies the DMG/Mach-O tree
and records `runtime_tested=false`. No accessible physical ARM runner exists, so this release makes
no native-runtime claim. The builders produce private checksum, metadata, architecture, signing,
and smoke evidence.

The publishing job locates every manifest distribution, streams their SHA-256 digests,
rejects duplicate content, and requires:

- a valid ARM64 Mach-O tree inside the macOS DMG;
- a valid x86-64 PE Windows installer;
- a valid x86-64 ELF AppImage;
- explicit ad-hoc/unsigned warnings for macOS and Windows;
- a passing installed-package worker smoke on Linux and Windows; and
- an honest static-only cross-build report for macOS rather than a fabricated runtime pass.

Public files consist of the verified installers, necessary payload parts/helpers, `SHA256SUMS`,
and `release-index.json`. Per-target evidence remains in the release index; standalone internal
sidecars and matrix-selection files stay private. The verifier requires all eight target IDs,
distinct installers, matching source commits, installed backend evidence, and the complete payload
set. GitHub asset sizes are checked before upload. Publication downloads the draft again and
verifies every checksum before making it visible; published releases cannot be overwritten.

Builders send completed evidence to the private receiver with timestamped, nonce-bound HMAC. The
reusable `CI_ARTIFACT_TOKEN` is never transmitted, and the receiver rejects stale signatures and
replays. The LAN endpoint remains HTTP, so HMAC protects authentication and integrity rather than
confidentiality; release payloads are intended for publication after the final gate.

## Version synchronization

For v0.0.12-alpha, all of these must agree:

- tag: `v0.0.12-alpha`;
- Python, npm, Cargo, and Tauri version: `0.0.12-alpha`;
- legacy Inno metadata (kept reproducible): `0.0.12-alpha`;
- all eight versioned filenames in `ci/v0.0.12-cross-alpha-artifacts.json`;
- changelog, README, known limitations, acceptance notes, and release notes; and
- GitHub release title: `LocalSR v0.0.12-alpha`.

`scripts/check_release_version.py --tag v0.0.12-alpha` enforces this. Hyphenated tags are published
with `--prerelease`. The old Slint workflow is manual-only, so one tag cannot accidentally publish
both application architectures.

## Signing and notarization

The exact v0.0.12-alpha exception is not production signed. macOS receives Tauri's ad-hoc seal and
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

At the time v0.0.12-alpha was prepared, none of these production signing secrets were configured.
They are hard beta gates, not requirements for this explicitly testing-only alpha.

## macOS cross-build exception

The physical Mac mini is Intel (`Macmini6,2`). Its boot-managed macOS x64 guest prepares a genuine
universal2 Python analysis environment from paired Intel/ARM wheels, emits an ARM64-only PyInstaller
worker, and cross-compiles the Rust/Tauri host for `aarch64-apple-darwin`. Recursive verification
rejects any wrong Mach-O slice.

PyTorch 2.2.2 is the final version with the paired macOS wheels needed by that process. It is known
security debt and cannot be the beta runtime. A later signed build must use maintained native ARM
PyTorch on real Apple-Silicon hardware; Intel macOS remains an open product decision.

## Failure Recovery

1. The macOS cross-build intentionally runs first to expose universal2, wheel and Mach-O problems before the other expensive builds.
2. For a transient failure where the repository commit has not changed, use "Re-run failed jobs" on the existing workflow run.
3. The release preparation code will reuse verified platform artifacts from earlier attempts of that same run.
4. If fixing the failure requires a new commit/SHA, start a new workflow run and rebuild every platform.
5. Never reuse binaries from an older commit as products of a newer commit.

## Publishing v0.0.12-alpha

1. Merge the release commit to `main` only after normal CI and Tauri CI are green.
2. Wait for any CI/Preview runs triggered by integration to finish before starting the release.
   An optional manual cross-alpha run rehearses the complete matrix without publishing. The tag
   run performs the same complete build and verification gates before publication; a second full
   rehearsal is not required when using that path.
3. Create the annotated tag: `git tag -a v0.0.12-alpha -m "LocalSR v0.0.12-alpha"`.
4. Push the tag. All Mac-mini backend package jobs must finish before the draft release is created.
5. Confirm the release is titled `LocalSR v0.0.12-alpha`, marked prerelease, and includes all eight
   distributions and the exact public download set recorded in `release-index.json`.
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
