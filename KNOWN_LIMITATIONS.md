# Known limitations — v0.0.8-alpha

- Downloads are portable alpha archives. There is no current DMG, AppImage, MSI, Windows Setup
  executable, or supported Homebrew formula.
- Production code-signing credentials are not configured. macOS builds are ad-hoc signed rather
  than Developer ID signed/notarized/stapled, and Windows executables are not Authenticode signed.
- The universal macOS cross-build currently bundles PyTorch 2.2.2 because it is the final release
  with matching Intel and Apple-Silicon wheels. That release has open security advisories,
  including a critical `torch.load` arbitrary-code-execution advisory fixed in PyTorch 2.6.0.
  Treat the macOS archives as private alpha evidence only, never load untrusted model files, and
  move the Apple-Silicon build to a native runner/current PyTorch before public beta. A supported
  Intel distribution strategy must be chosen separately because current PyTorch has no Intel macOS
  wheel.
- Video, SeedVR2, de-flicker, and multi-video batching are Labs / Experimental. Video output is MP4;
  audio remuxing is best-effort, and large SeedVR2 downloads and memory requirements are substantial.
- The HAT-S Face checkpoint is CC BY-NC-SA 4.0 and is restricted to non-commercial use.
- The Best and anime checkpoint release pages use the non-standard string `CC-BY-0.4`; their author
  should clarify the intended license before public-beta or commercial use.
- CI proves package contents, startup, worker IPC, model download/inference, and static backend
  provenance. It does not replace physical GPU/driver acceptance on every advertised device.
- DirectML, Intel XPU, ROCm, CUDA, and MPS availability depends on compatible hardware, drivers, and
  the matching backend archive.
- The repository and its Issues are private. General-public feedback intake is not available yet.
- Custom `.pth`/`.pt` models are trusted-code inputs and are not sandboxed. Only open checkpoints
  from publishers you trust; a checksum verifies identity, not safety.
- Model downloads require network access and enough free disk space; models are not bundled.

The [acceptance checklist](docs/acceptance.md) separates automated evidence from remaining physical
hardware testing. The release's `beta-readiness.json` records the same open gates in a form CI can
validate without pretending that product decisions or manual tests are complete.
