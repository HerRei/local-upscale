# Known limitations — v0.0.10-alpha

- The next host has one uncomplicated installer per operating system, but v0.0.10-alpha is a
  testing-only unsigned exception: macOS is ad-hoc sealed and Windows lacks Authenticode. Production
  signing, notarization, stapling, and trust validation remain mandatory before beta.
- The Apple-Silicon DMG is Intel→ARM cross-built on the Mac mini with PyTorch 2.2.2, the last line
  with paired Intel/ARM wheels. It is known security debt and may not be promoted to beta. A native
  maintained ARM runtime and the long-term Intel-support decision remain open.
- The first Tauri alpha bundles CPU inference on Windows/Linux and MPS on Apple Silicon. CUDA,
  DirectML, Intel XPU, and ROCm remain implemented in the worker contract but need downloadable
  engine packs and physical acceptance before the new installer can advertise them.
- There is no supported Homebrew formula. Add one only after signed assets have stable public URLs
  and real checksums.
- The Windows DirectML archive is constrained by Microsoft's preview `torch-directml` package to
  PyTorch 2.4.1, which also has checkpoint-loading advisories. LocalSR's default checkpoint policy
  prevents unverified pickle/TorchScript models from reaching that runtime, but DirectML remains an
  alpha-only backend until a maintained runtime or replacement backend is selected.
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
- Custom `.safetensors` models are accepted by default. Unverified `.pth`, `.pt`, and `.ckpt`
  models are blocked unless `LOCALSR_ALLOW_UNVERIFIED_CHECKPOINTS=1` is explicitly set; that escape
  hatch is not a sandbox and should be treated as permission to execute the model publisher's code.
- Model downloads require network access and enough free disk space; models are not bundled.

The [acceptance checklist](docs/acceptance.md) separates automated evidence from remaining physical
hardware testing. The release index embeds the versioned beta-readiness register in a form CI can
validate without pretending that product decisions or manual tests are complete.
