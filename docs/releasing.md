# Native release process

`Build & Release` (`.github/workflows/release.yml`) builds nine portable, backend-specific archives.
Model weights are downloaded on demand and are never included.

## v0.0.9-alpha artifact matrix

| Platform | Backend | Archive |
|---|---|---|
| Linux x86-64 | CPU | `LocalSR-Linux-CPU-x86_64.tar.gz` |
| Linux x86-64 | CUDA | `LocalSR-Linux-CUDA-x86_64.tar.gz` |
| Linux x86-64 | Intel XPU | `LocalSR-Linux-Intel-x86_64.tar.gz` |
| Linux x86-64 | AMD ROCm | `LocalSR-Linux-ROCm-x86_64.tar.gz` |
| Windows x86-64 | CPU | `LocalSR-Windows-CPU-x86_64.zip` |
| Windows x86-64 | DirectML | `LocalSR-Windows-DirectML-x86_64.zip` |
| Windows x86-64 | CUDA | `LocalSR-Windows-CUDA-x86_64.zip` |
| macOS 12+ Intel | CPU | `LocalSR-macOS-x86_64.tar.gz` |
| macOS 12+ Apple Silicon | MPS | `LocalSR-macOS-arm64.tar.gz` |

The macOS archives contain `LocalSR.app`. Other archives contain the `LocalSR` portable directory.
The workflow does not currently produce a DMG, AppImage, MSI, or Inno Setup executable.

## Release gates

Before native builds, the CPU job downloads Quick and Best into an empty temporary model cache,
verifies pinned size and SHA-256 values, loads both through Spandrel, and runs real CPU inference.

Every archive is accompanied by `.sha256`, `.metadata.json`, and `.architecture.txt`. The final gate:

- locates exactly one of every manifest artifact;
- re-hashes each archive and checks its sidecar and metadata;
- parses PE, ELF, or Mach-O headers for every native member;
- requires the exact architecture for the main executable and all host libraries;
- records backend-probe and frozen-worker smoke evidence; and
- rejects any two archives with the same SHA-256 digest.

GitHub assets may be split into chunks to stay below the per-file upload limit. The release index
records how to reassemble and verify them.

Artifact builders send completed archives to the private receiver with a timestamped, random-nonce
HMAC covering the method, path, length, run/attempt, platform, filename, and content digest. The
reusable `CI_ARTIFACT_TOKEN` is never sent on the wire, and the receiver rejects stale signatures
and nonce replays. The receiver and `scripts/artifact_auth.py` must be deployed together, host clocks
must remain synchronized, and the secret must be rotated if the receiver or a runner is compromised.
The LAN endpoint is still HTTP, so this protects authentication and integrity rather than download
confidentiality; release archives are intended to become public after the final verification gate.

## Version synchronization

For v0.0.9-alpha, all of these must agree:

- tag: `v0.0.9-alpha`;
- Python project/app version: `0.0.9-alpha`;
- macOS numeric `CFBundleShortVersionString=0.0.9`, `CFBundleVersion=9`, and exact custom
  `LocalSRReleaseVersion=0.0.9-alpha` (all derived from `pyproject.toml`);
- Inno Setup metadata: `0.0.9-alpha`;
- changelog and README release line; and
- GitHub release title: `LocalSR v0.0.9-alpha`.

`scripts/check_release_version.py --tag v0.0.9-alpha` enforces this. A hyphenated version tag is
created with `gh release create --prerelease`; reruns also correct the title/prerelease flag.

## Signing and notarization

Without credentials, the alpha workflow records an explicit `ad-hoc` macOS signing report in
artifact metadata. It does not claim Gatekeeper acceptance. Production signing requires all of:

- `MACOS_CERTIFICATE_P12_BASE64` — Developer ID Application certificate/key exported as `.p12`;
- `MACOS_CERTIFICATE_PASSWORD`;
- `MACOS_SIGNING_IDENTITY` — complete Developer ID Application identity;
- `MACOS_NOTARY_APPLE_ID`;
- `MACOS_NOTARY_PASSWORD` — app-specific Apple ID password; and
- `MACOS_TEAM_ID`.

With all values present, `scripts/sign_macos_app.sh` imports the certificate into an ephemeral
keychain, signs with hardened runtime, submits to Apple notarytool, staples the ticket, validates it,
and requires Gatekeeper acceptance before archiving. A partial credential set fails the build.

Windows Authenticode credentials are not configured and Windows archives remain unsigned. Add and
verify an Authenticode signing/timestamping stage before calling a Windows build public-beta ready.

## macOS runtime security limitation

The current universal cross-build pins PyTorch 2.2.2 because it is the last version that publishes
both Intel and Apple-Silicon macOS wheels. PyTorch 2.2.2 has open advisories, including a critical
`torch.load` arbitrary-code-execution issue fixed in 2.6.0. Consequently, the unsigned macOS
archives are private alpha evidence and are not public-beta candidates. Do not load untrusted
checkpoints.

Before public beta, build the Apple-Silicon artifact natively with a supported/current PyTorch,
re-scan the frozen bundle, and decide whether the Intel artifact can use a maintained runtime or
must be removed. The repository currently has only an Intel macOS self-hosted runner; registering a
native Apple-Silicon runner and changing artifact transfer are operational prerequisites.

The Windows DirectML archive has a separate legacy-runtime constraint: Microsoft's latest preview
`torch-directml` wheel pins PyTorch 2.4.1. LocalSR blocks unverified pickle/TorchScript checkpoints
before that runtime sees them, but a maintained DirectML build, a different Windows acceleration
backend, or exclusion from beta remains an owner decision.

## Beta readiness evidence

`scripts/check_beta_readiness.py` validates `ci/beta-readiness.json` in CI and release preflight.
The snapshot is included as a GitHub release asset and referenced by `release-index.json`. Normal
alpha builds require a truthful, structurally valid register; only an actual beta promotion should
use `--require-beta-ready`. Physical results should be copied from
`docs/acceptance-record.example.json` and retained with the tested artifact digest.

## Publishing v0.0.9-alpha

1. Run the offline suite, lint/format, Slint compile, package-data inspection, and live-model check.
2. Merge the release commit to `main` and require green CI.
3. Create the annotated tag: `git tag -a v0.0.9-alpha -m "LocalSR v0.0.9-alpha"`.
4. Push the tag. The workflow publishes only after all nine archives pass the final gate.
5. Confirm the GitHub release is titled `LocalSR v0.0.9-alpha`, marked prerelease, and contains the
   release index/checksums/metadata for all nine logical archives plus `beta-readiness.json`.
6. Complete the physical-machine items in `docs/acceptance.md` before promoting the build to public
   beta.
