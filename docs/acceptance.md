# Release acceptance

## Automated for v0.0.7-alpha

- Offline unit/integration suite on Linux, Windows x86-64, and macOS Intel CI environments.
- Slint compile and packaged GUI/worker IPC smoke checks.
- Real empty-cache download, SHA-256 validation, Spandrel load, and CPU inference for Quick and Best.
- PE/ELF/Mach-O parsing for every native member, exact main-executable architecture checks, backend
  provenance, per-archive checksums, metadata sidecars, and rejection of duplicate digests.
- Wheel/sdist inspection for SeedVR2 YAML, embeddings, NOTICE, and vendor license.
- Generated-fixture coverage for real-color photos, faces, anime-like line art, screenshot/text,
  large and transparent images, DNG handling, cancellation, low disk, and low memory.

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
result, and copied LocalSR diagnostics for each manual run.
