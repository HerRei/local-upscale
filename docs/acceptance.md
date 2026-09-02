# Release acceptance

## Automated for v0.0.10-alpha

- Offline unit/integration suite on Linux, Windows x86-64, and macOS Intel CI environments.
- Svelte, Rust, and worker protocol tests on Linux, Windows, and macOS CI environments.
- Real Tauri AppImage, NSIS, and DMG install/mount smoke checks that start the bundled worker.
- Real empty-cache download, SHA-256 validation, Spandrel load, and CPU inference for Quick and Best.
- PE/ELF/Mach-O parsing, exact installer/main-executable architecture checks, provenance,
  checksums, metadata, and rejection of duplicate installer digests.
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
- The signed release pipeline publishes only three installers plus `SHA256SUMS` and one consolidated
  release index; existing releases cannot be overwritten.

## Required before public beta

- Register the native Apple-Silicon runner used by the PyTorch 2.13 DMG pipeline, supply Developer
  ID/notarization credentials, and decide whether Intel support has a viable maintained runtime.
- Install and run on physical macOS ARM and Intel machines and validate Gatekeeper after production
  signing/notarization.
- Design and package the optional Windows NVIDIA/DirectML and Linux CUDA/ROCm/XPU engine packs,
  then run them and the CPU pack on matching physical systems.
- Repeat the image/media matrix above with real user files and inspect output quality, metadata,
  cancellation cleanup, thermal behavior, low-disk recovery, and memory-pressure recovery.
- Verify download URLs and the selected public feedback route from a signed-out browser.

Record hardware model, OS/driver versions, selected artifact digest, model, input dimensions/format,
result, and copied LocalSR diagnostics for each manual run. Copy
`docs/acceptance-record.example.json` for each platform/backend test and replace every `not-run`
result with `pass`, `fail`, or `not-applicable`; retain failures as evidence rather than erasing them.
