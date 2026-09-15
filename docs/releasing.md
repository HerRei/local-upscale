**Separate public beta candidate: v0.1.1-beta (provisional)**

The current beta work uses `ci/public-beta-release.json` and
`ci/public-beta-readiness.json`. Run `python scripts/check_public_beta.py` and
`python scripts/check_beta_readiness.py ci/public-beta-readiness.json` for this
candidate. A valid register does not mean the beta is ready: use
`--require-beta-ready` to enforce its remaining publication gates.

The one-release v0.0.12/v0.0.13 cross-alpha workflows, manifests and pinned
universal2 requirements have been removed; their published packages and release
notes remain the historical record. Retain package hashes and the exact installed
acceptance reports before reviewing any publication.
[Beta checklist](beta-release-checklist.md).

Current beta package builds are paused pending the user’s explicit .12-complete
build authorization. Use [the isolated Windows handoff](windows-beta-build-handoff.md)
and [new ONNX source runtime review](windows-inference-runtime-review.md); the
older runtime/caches below belong to the historical alpha process and must not
be reused or altered for beta preparation.

# Native release process

`Signed Tauri Release` (`.github/workflows/desktop-release.yml`) builds every registered target
and fails closed without production signing. The former Slint build has been retired from the
beta checkout.

## Artifact matrix

The canonical target registry is `ci/tauri-targets.json`. The release workflow expands its
Windows/Linux matrices from it, and `ci/tauri-release-artifacts.json` (written by
`scripts/release_targets.py --write-manifests`) must match it exactly.

| Platform | Backend variants | Runtime |
|---|---|---|
| Apple Silicon macOS | MPS | native Torch 2.13; requires an Apple-Silicon runner |
| Windows 10/11 x86-64 | CPU, CUDA | Torch 2.13; CUDA uses an installer plus verified external payloads |
| Windows 10/11 x86-64 | DirectML | torch-directml 0.2.5.dev240914, Torch 2.4.1, torchvision 0.19.1 |
| Linux x86-64 | CPU, CUDA, Intel XPU, AMD ROCm | Separate Torch 2.13 distributions |

`requirements/locks/` contains the reviewed transitive Windows/Linux dependency locks and hashes.
Refresh them deliberately with `scripts/lock_backend_requirements.py` and uv 0.12.8; releases use
`backend_wheelhouse.py` to build/reuse verified wheelhouses and install offline. Cached wheels
live outside disposable scratch: `/ci-scratch/wheelhouses` (Linux) or `C:\lsr-ci\wheelhouses`.
Cargo build outputs are retained under `cargo-target/tauri-*`; only bundle output is cleared
between variants. Heavy jobs remain serialized. Monitor disk reserves and remove obsolete
wheelhouse keys only when no active build uses them.
The registry budgets peak working space per backend before dependency installation (15–100 GiB,
plus the protected disk reserve). ROCm's budget is largest because its wheel, expanded runtime,
frozen worker, AppDir, and smoke-test extraction can coexist. These are conservative estimates,
not measured guarantees; retain observed peak usage in the release handoff and tune from evidence.

Windows CUDA's small NSIS installer embeds its engine manifest. Adjacent 1,900 MiB payload parts
are checked by the Rust host before extraction into a staging directory and atomic promotion.
Missing or corrupted files abort installation. The installed frozen worker receives the same
identity/CPU-inference probe as the other variants. This does not change worker packaging to
portable Python and does not claim GPU execution on the packaging VM.

Linux XPU packaging collects Intel SYCL/UR/oneMKL native libraries and device data from their
installed wheel records, including dynamically loaded adapters that ELF dependency scanning
cannot discover. It preserves distribution metadata and licenses. The frozen worker checks the
recorded inventory, Torch version, and each native library's x86-64 ELF header during the installed
backend probe. Publication requires that evidence in addition to CPU inference; it does not
substitute for execution on a physical Intel GPU.

Oversized Linux AppImages are published as verified parts plus a generated shell helper that
reconstructs and verifies the original executable. `release-index.json` lists the exact files for
each logical distribution; public file counts therefore vary with payload size.

## Release gates

Every platform installs the actual package and starts the bundled Rust host in headless smoke
mode. That host negotiates the production JSON-Lines protocol with the bundled Python worker. The
macOS job requires a registered native Apple-Silicon runner and fails closed on the Intel Mac mini.
The builders produce private checksum, metadata, architecture, signing, and smoke evidence.

The publishing job locates every manifest distribution, streams their SHA-256 digests,
rejects duplicate content, and requires:

- a valid ARM64 Mach-O tree inside the macOS DMG;
- a valid x86-64 PE Windows installer;
- a valid x86-64 ELF AppImage;
- Developer ID/notarization evidence for macOS and Authenticode evidence for Windows; and
- a passing installed-package worker smoke with verified backend identity on every target.

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

The tag, the Python, npm, Cargo and Tauri versions, every versioned filename in
`ci/tauri-release-artifacts.json`, the changelog, README and release notes, and the GitHub release
title (`LocalSR <tag>`) must agree. `scripts/check_release_version.py --tag <tag>` enforces this;
beta versions are delegated to `scripts/check_public_beta.py`. Hyphenated tags are published with
`--prerelease`. The retired Slint workflow is absent from the beta, so one tag cannot publish both
application architectures.

## Signing and notarization

The release pipeline fails closed and requires the following:

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

These production signing secrets are hard beta gates.

## Failure Recovery

1. For a transient failure where the repository commit has not changed, use "Re-run failed jobs" on the existing workflow run.
2. The release preparation code will reuse verified platform artifacts from earlier attempts of that same run.
3. If fixing the failure requires a new commit/SHA, start a new workflow run and rebuild every platform.
4. Never reuse binaries from an older commit as products of a newer commit.

## Headless macOS runner startup

The Mac mini's `macmini-macos-x64` runner starts automatically: the Linux host enables Docker at
boot, the Dockur guest uses `restart: always`, and the existing macOS login service starts the
runner. A full guest restart acceptance check confirmed that the runner returned online without
manual action.

The optional `macOS Runner Boot Maintenance` workflow can migrate the listener to a root-owned
pre-login LaunchDaemon after one-time administrator authorization. That additional hardening does
not replace the native Apple-Silicon runtime/signing gate for beta.
