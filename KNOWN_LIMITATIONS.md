# Known limitations

These apply to v0.6.3-beta. Please read them before starting a long job.

## Platforms

- Published packages: macOS on Apple Silicon (v0.6.3-beta); Windows CPU, DirectML
  and NVIDIA CUDA installers and Linux CPU, AMD ROCm and NVIDIA CUDA AppImages
  (v0.6.3-beta; image editing is tested only on the Mac). The CUDA and
  DirectML editions are **untested**: CUDA has never run on an NVIDIA GPU (the app
  labels it Labs), and
  DirectML's NAFNet denoising results still miss the accuracy tolerance. Intel XPU
  packages are held back; [Platforms](docs/platforms.md) explains why.
- The CUDA edition needs an NVIDIA RTX 20 series or newer and driver 580 or
  newer. On Windows, older NVIDIA cards can use DirectML.
- The CUDA and ROCm editions are several gigabytes and come in parts that must be
  downloaded into one folder (see the download page).
- The macOS build is the most tested. The Windows and Linux packages pass the
  codec policy and an automated smoke test of the installed app, but this release
  has not been run through the manual desktop checks on those systems.
- Every direct download updates itself; the Microsoft Store edition, when it ships,
  updates through the Store. An update downloads the package and its inference
  engine, so a Linux CUDA or ROCm update is about twice the AppImage's size. The
  Windows installers are not code-signed, so SmartScreen warns before one runs; the
  Microsoft Store edition is signed by the Store.
- A working launch does not prove GPU compatibility. Some operators fall back to
  the CPU (for example `aten::roll` on DirectML), and drivers vary.
- On Apple Silicon, the first job with a Neural Engine model (Quick photo, Real-ESRGAN,
  JPEG cleanup, Best photo) and tile size spends up to about a minute preparing it,
  using up to about 4 GB of memory while it does.
  Its results differ from the GPU path by FP16 rounding, and only the M1 Pro has
  been measured; newer Macs choose between the Neural Engine and the GPU on their
  first tile.

## Media formats

- On macOS, H.264, HEVC and AAC — most phone and camera videos — open through
  the system's own codecs. WMV, DivX, FLV and H.263 still need an FFmpeg you
  install yourself, and on Windows and Linux so does every patent-licensed
  format. An FFmpeg on your PATH or in Homebrew is used automatically; LocalSR
  shows the install command when a file needs one. The reasons are in
  [Media formats and licensing](docs/licensing-media.md).
- HEIC/HEIF and JPEG XL photos open on macOS only, where the system decodes
  them; Windows and Linux would need an HEVC decoder LocalSR does not ship.
  Animated GIFs and HEIC bursts use their first image, and camera RAW files
  other than DNG lose their EXIF date and author.
- The default video format, Automatic, writes HEVC with the Mac's hardware encoder
  for SDR MP4 exports and AV1 everywhere else. Some older players and editors cannot
  open AV1; VP9 and lossless FFV1 are built in. Choosing H.264/HEVC explicitly uses
  macOS's encoders (MP4 only) or your FFmpeg, from a temporary lossless copy that
  needs extra disk space, except whole clips on macOS, which stream straight in.
- The built-in video decoder opens local files only; it does not fetch network playlist segments.
- On Linux, comparing videos with different frame rates can show one side a frame
  early after a frame step or seek; playback and same-rate comparisons stay in step.
- Interlacing is deinterlaced only when the file is tagged (or a field order is set
  by hand in the Scan row); untagged interlacing and inverse telecine are not
  detected. On macOS, a clip the system decoder opens carries no field flags, so
  Double rate there assumes top field first: set the order by hand if motion jumps
  back and forth. DVD menus, disc images and encrypted media are out of scope.
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
- A source image, RAW photo or video frame may have at most 178,956,970 pixels
  (about 179 megapixels). The limit also applies to the RAW sensor size and video decoding. Larger inputs are refused before pixel conversion. The output has 16
  times as many pixels at ×4, so a source far below that limit can already be too large
  for the memory of the computer.
- Frame interpolation adds processing and memory costs. Interpolating before the
  upscale requires upscaling more frames; after the upscale runs interpolation at
  the larger output size. 1080p and 4K Mac performance and memory use have not
  yet been validated.
- Slow motion is saved without sound and subtitles. A player that ignores frame times and
  trusts the header plays it right; an editor that assumes a constant rate on a
  variable-rate source may not. The viewer takes a result at least 1.5 times as long as its
  original for slow motion, so a result whose original was replaced on disk by a shorter file
  opens in Enhanced only.
- Estimates are measured from completed tiles and frames, so they appear only
  after the first results and change with the scene.

## Restoration quality

- AI restoration invents detail. Inspect text, faces and fine structure, and keep
  your originals; LocalSR never overwrites a source file.
- Interpolated motion can contain artifacts, especially around occlusions, fine
  detail and cuts. Cut and repeated-frame detection are heuristics and can miss.
  Interpolation is unavailable with SeedVR2 and HDR preservation.
- HAT HDR preservation is experimental: the models were trained on SDR, so HDR
  perceptual quality and temporal stability are unverified. SeedVR2 exports SDR.
- NAFNet can produce unstable results on some scanned documents, and SPAN on
  some periodic high-contrast patterns. LocalSR detects this and refuses to
  write the output rather than saving a broken file; in the reproduction set,
  two of eleven scans are rejected.
- The two HAT face companions must be imported manually because their
  checkpoint rights are unresolved. Video face processing reuses masks across
  frames and is not motion tracking.
- **Colour** (DDColor) works on photos and black-and-white videos; a video that
  already has colour skips it. Colour is refused on a video whose HDR is preserved.
  On DirectML and OpenVINO devices DDColor runs on the CPU, and on Apple Silicon
  with Neural Engine enabled it runs on the Apple GPU via PyTorch.
- Colour needs a 912 MB model download. Colours are a guess; skies can come out pink.

## Image editing

- Edit is experimental and tested only on the Mac. CUDA/ROCm packages also
  include editing support, but it has not been validated on those GPUs. Two
  configurations are validated at 512 px on the 16 GB M1 Pro: FLUX.2 klein 4B
  Q4_0 (about two minutes per edit with other apps open, without swapping) and
  Mage-Flow-Edit-Turbo Q6_K (about 96 s on the CPU backend because Metal
  produces blank white output for its diffusion transformer). FLUX.2 klein Q8_0,
  the Qwen bundles and larger edit sizes have not run on real hardware yet; their
  memory profiles are estimates. Up to v0.1.5-beta the memory guard misread free
  memory on Macs and refused edits that fit.
- A 16 GB Mac can use FLUX.2 klein 4B or Mage-Flow-Edit-Turbo; Qwen Image Edit 2511 needs 24 GB or more.
  Edits are limited to 512, 768 or 1024 px on the longest side, one image at a
  time, and the result is a new PNG.
- The editing models are separate downloads of 5 to 26 GB. Qwen Image 2.1 is
  not offered for download because its license allows research and evaluation
  only, though you can supply your own local files for Qwen Image Edit 2511,
  Qwen Image 2.1, FLUX.2 klein, or Mage-Flow-Edit-Turbo under **Use your own edit model…**.

## Models

- No weights are bundled. Each model downloads from its author's release when
  you choose it, so the first use of a model needs a network connection. By
  default, models are stored in `LocalSR/models`; you can move the model folder
  to another drive under **Settings → Storage** (engine and temporary work files stay
  in the app folder).
- Custom `.safetensors` checkpoints are accepted. Pickle-based `.pth`, `.pt` and
  `.ckpt` files can run code when loaded and are refused unless you tick
  *Allow this pickle-based checkpoint* in the app (on the command line, set
  `LOCALSR_ALLOW_UNVERIFIED_CHECKPOINTS=1`), only for files whose publisher you trust.

## Installation and updates

- Updates are downloaded and verified in the app on macOS, Windows and Linux.
  Settings, recipes and the queue are backed up before an update is installed.
- An AppImage replaces itself only when it runs as an AppImage from a folder you
  can write to; an AppImage under `/opt` owned by root has to be updated by hand.
- The Windows installer cannot roll back an update that fails halfway, for example
  on a full disk. Run the full installer from the website; settings, recipes, the
  queue and models are kept.
- Uninstalling the Store edition can delete its data folder, including settings
  and recipes. Back it up first; downloaded models live in the shared
  `LocalSR/models` folder and survive.

- Stopping an H.264 or HEVC export that uses an FFV1 master discards the unfinished output. The streamed macOS system encoder can keep a partial file.
