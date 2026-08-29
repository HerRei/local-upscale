# Release acceptance

## Automated for v0.0.9-alpha

- Offline unit/integration suite on Linux, Windows x86-64, and macOS Intel CI environments.
- Slint compile and packaged GUI/worker IPC smoke checks.
- Real empty-cache download, SHA-256 validation, Spandrel load, and CPU inference for Quick and Best.
- PE/ELF/Mach-O parsing for every native member, exact main-executable architecture checks, backend
  provenance, per-archive checksums, metadata sidecars, and rejection of duplicate digests.
- Wheel/sdist inspection for SeedVR2 YAML, embeddings, NOTICE, and vendor license.
- Generated-fixture coverage for real-color photos, faces, anime-like line art, screenshot/text,
  large and transparent images, DNG handling, cancellation, low disk, and low memory.
- CI validation of `ci/beta-readiness.json`, which must match the application version and cannot
  claim beta readiness while any credential, decision, external clarification, manual, or Labs
  gate remains unresolved.
- Catalog checkpoints and installed video bundles are revalidated by SHA-256 rather than file size;
  unverified pickle/TorchScript checkpoints are blocked before Spandrel or PyTorch sees them.
- Release workflows use an immutable checkout commit, persist no checkout credentials, grant write
  permission only to the publishing job, and authenticate LAN artifact uploads with replay-bounded
  HMAC without transmitting the reusable secret.
- Both portable installer scripts refuse installation when the matching release checksum is absent
  or invalid.

## Required before public beta

- Eliminate the macOS PyTorch 2.2.2 security debt: build Apple Silicon natively with a supported,
  current PyTorch release, and either establish a supported Intel runtime or stop advertising the
  Intel archive. Re-run the dependency/security scan on the resulting app bundles.
- Install and run on physical macOS ARM and Intel machines and validate Gatekeeper after production
  signing/notarization.
- Run representative jobs on Windows NVIDIA, AMD/Intel DirectML, and CPU systems.
- Run each advertised Linux CUDA, ROCm, Intel XPU, and CPU archive on matching physical hardware.
- Repeat the image/media matrix above with real user files and inspect output quality, metadata,
  cancellation cleanup, thermal behavior, low-disk recovery, and memory-pressure recovery.
- Verify download URLs and the selected public feedback route from a signed-out browser.

Record hardware model, OS/driver versions, selected artifact digest, model, input dimensions/format,
result, and copied LocalSR diagnostics for each manual run. Copy
`docs/acceptance-record.example.json` for each platform/backend test and replace every `not-run`
result with `pass`, `fail`, or `not-applicable`; retain failures as evidence rather than erasing them.
