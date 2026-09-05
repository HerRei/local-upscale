# Known limitations — v0.0.12-alpha

- The desktop matrix includes all eight backend variants, but v0.0.12-alpha is a
  testing-only unsigned exception: macOS is ad-hoc sealed and Windows lacks Authenticode. Production
  signing, notarization, stapling, and trust validation remain mandatory before beta.
- The Apple-Silicon DMG is Intel→ARM cross-built on the Mac mini with PyTorch 2.2.2, the last line
  with paired Intel/ARM wheels. It is known security debt and the paired Labs video runtime also
  carries Diffusers 0.35.2 advisories. LocalSR never enables Diffusers remote custom pipelines,
  but this package may not be promoted to beta. A native maintained ARM runtime and the long-term
  Intel-support decision remain open.
- The v0.0.12 matrix packages Windows CPU/CUDA/DirectML, Linux CPU/CUDA/Intel XPU/AMD ROCm,
  and Apple-Silicon MPS. Each download selects one backend. Installed-worker checks establish
  runtime identity and CPU inference; they do not establish GPU performance or driver compatibility.
  Physical acceptance remains required for each GPU backend.
- There is no supported Homebrew formula. Add one only after signed assets have stable public URLs
  and real checksums.
- The Windows DirectML archive is constrained by Microsoft's preview `torch-directml` package to
  PyTorch 2.4.1, which also has checkpoint-loading advisories. LocalSR's default checkpoint policy
  prevents unverified pickle/TorchScript models from reaching that runtime, but DirectML remains an
  alpha-only backend until a maintained runtime or replacement backend is selected.
- Standard video supports SDR input and H.264 output in MP4/MKV, preserving source presentation
  timing and normalizing right-angle rotation and mirrors. HDR PQ/HLG requires prior SDR conversion.
  Source audio/subtitles are copied when compatible; trims have compressed-packet precision.
  SeedVR2, de-flicker, and video face processing remain individually Labs. Installed-platform
  playback, longer clips, codec combinations, and resource pressure still require recorded acceptance.
- The HAT-S Face code path supports fidelity-controlled blending, but the current checkpoint's
  independent weight/training-data rights are unresolved. It is excluded from trusted automatic
  downloads and commercial recommendations; only a hash-matching user-supplied copy is accepted.
  The separate MIT-licensed YuNet detector is downloaded and hash-verified on first use.
- The Best and anime checkpoint release pages use the non-standard string `CC-BY-0.4`; their author
  should clarify the intended license before public-beta or commercial use.
- CI proves package contents, startup, worker IPC, model download/inference, and static backend
  provenance. It does not replace physical GPU/driver acceptance on every advertised device.
- GPU distributions require compatible hardware and drivers. Windows CUDA uses adjacent verified
  engine payloads; large Linux AppImages use verified parts. Packaging success is not physical GPU acceptance.
- Tauri's current Linux WebKit/GTK3 stack transitively pins `glib` 0.18, which has the
  `GHSA-wrw7-89jp-8q8g` iterator-soundness advisory. The patched `glib` 0.20 line requires the
  upstream GTK4 migration rather than a safe lockfile-only update; this remains a beta blocker.
- The repository and its Issues are private. General-public feedback intake is not available yet.
- Custom `.safetensors` models are accepted by default. Unverified `.pth`, `.pt`, and `.ckpt`
  models are blocked unless `LOCALSR_ALLOW_UNVERIFIED_CHECKPOINTS=1` is explicitly set; that escape
  hatch is not a sandbox and should be treated as permission to execute the model publisher's code.
- Model downloads require network access and enough free disk space; models are not bundled.

The [acceptance checklist](docs/acceptance.md) separates automated evidence from remaining physical
hardware testing. The release index embeds the versioned beta-readiness register in a form CI can
validate without pretending that product decisions or manual tests are complete.
