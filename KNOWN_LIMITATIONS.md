# Known limitations

These apply to v0.1.1-beta. Please read them before starting a long job.

## Platforms

- Only the Apple Silicon macOS build is published. Windows and Linux builds exist
  in the release pipeline but are held back: Windows until the royalty-free media
  runtime builds there, Linux until the 5–6 GB GPU packages have a download host.
  See [Platforms](docs/platforms.md).
- NVIDIA and Intel XPU GPUs have not been tested on real hardware. Those engines
  are labelled Labs in the app.
- A working launch does not prove GPU compatibility. Some operators fall back to
  the CPU (for example `aten::roll` on DirectML), and drivers vary.
- On macOS, opening a file *with* LocalSR while the app is not running can crash
  it during launch. Start LocalSR first, then open or drop the file. Fixed in
  the next build.

## Media formats

- H.264, HEVC, WMV, DivX and AAC-only sources — which includes most phone and
  camera videos — need an FFmpeg you install yourself and select under
  *Advanced settings → Video*. LocalSR shows the install command when you add
  such a file. The reasons are in [Media formats and licensing](docs/licensing-media.md).
- The default video export is AV1. Some older players and editors cannot open it;
  VP9 and lossless FFV1 are built in, and H.264/HEVC export goes through your
  FFmpeg from a temporary lossless copy, which needs extra disk space.
- Interlacing is deinterlaced only when the file is tagged; untagged interlacing
  and inverse telecine are not detected. DVD menus, disc images and encrypted
  media are out of scope.
- A `.mov` extension says little about compatibility: the codec, colour metadata,
  transforms and audio tracks all matter. Spatial audio and unsupported tracks
  are dropped with a warning.

## Time and memory

- Video is not real time. A five-minute 480p clip upscaled 2× with SPAN took
  about 100 minutes on a Radeon RX 9060 XT. High-resolution video can take hours
  or days, and a complete multi-minute 4K export has not been exercised yet.
- Memory use depends on the model, the output size and the clip. 16 GB of GPU
  memory does not guarantee that a SeedVR2 job fits; the FP8 download is smaller,
  but the working memory is not. Try a short clip at a modest output size first.
- Estimates are measured from completed tiles and frames, so they appear only
  after the first results and change with the scene.

## Restoration quality

- AI restoration invents detail. Inspect text, faces and fine structure, and keep
  your originals; LocalSR never overwrites a source file.
- HAT HDR preservation is experimental: the models were trained on SDR, so HDR
  perceptual quality and temporal stability are unverified. SeedVR2 exports SDR.
- NAFNet can produce unstable results on some scanned documents, and SPAN on
  some periodic high-contrast patterns. LocalSR detects this and refuses to
  write the output rather than saving a broken file; in the reproduction set,
  two of eleven scans are rejected.
- The two HAT face companions must be imported manually because their
  checkpoint rights are unresolved. Video face processing reuses masks across
  frames and is not motion tracking.

## Models

- No weights are bundled. Each model downloads from its author's release when
  you choose it, so the first use of a model needs a network connection.
- Custom `.safetensors` checkpoints are accepted. Pickle-based `.pth`, `.pt` and
  `.ckpt` files can run code when loaded and are refused unless you set
  `LOCALSR_ALLOW_UNVERIFIED_CHECKPOINTS=1`; see [SECURITY.md](SECURITY.md).

## Installation and updates

- macOS updates are downloaded and verified in the app. Settings, recipes and
  the queue are backed up before an update is installed.
- Direct Windows installers for the beta will not be Authenticode signed, so
  SmartScreen will warn. The Microsoft Store edition, when it ships, is signed by
  the Store.
- Uninstalling the Store edition can delete its data folder, including settings
  and recipes. Back it up first; downloaded models live in the shared
  `LocalSR/models` folder and survive.
