# Video support and local acceptance

Standard video processes each frame and exports SDR. The beta also offers
HLG/PQ preservation with HAT as a Labs option. SeedVR2, de-flicker and video face
processing have their own Labs limits. Platform coverage is in [Platforms](platforms.md) and
the verified formats are listed in [Testing](testing.md).

## Media contract

- LocalSR includes only royalty-free or patent-expired media formats; see [media formats and licensing](licensing-media.md). Decode SDR and BT.2020 non-constant-luminance HLG/PQ through PyAV. Export AV1 (default, MP4 or MKV), VP9 (MP4 or MKV) or lossless FFV1 (MKV) as 8-bit YUV420P, or 10-bit YUV420P10LE for preserved HDR. On macOS, H.264, HEVC and AAC go through the system codecs (`localsr-media`, AVFoundation). Other formats LocalSR does not include (WMV, DivX, FLV, H.263), and every such format on Windows and Linux, use an FFmpeg the user installed, found automatically or selected under Advanced settings; otherwise they fail with an actionable message. High-bit-depth SDR preservation is not implemented.
- Preserve each frame's presentation timestamp and interval by default, including variable frame rate. Trims are inclusive frame indices; audio starts at the actual selected timestamp. Unknown/non-increasing timestamps fail explicitly.
- Normalize 90°, 180°, 270° rotation and orthogonal mirrors before inference, previews, and export. Camera MOV track translations are rebased to the rotated image bounds. Perspective, scaling and non-right-angle transforms still fail explicitly.
- Decodable audio the output container accepts is copied unchanged (MP4: MP3, Opus, FLAC, ALAC, AC-3; MKV: any decodable track). Other decodable audio is converted to 48 kHz Opus, preserving timing and channel layout. Copied audio has packet-level trim precision; converted audio has sample-level trimming plus Opus encoder padding. Unsupported subtitles and audio that can neither be copied nor decoded produce a warning and are omitted, unless a user-installed FFmpeg converts them.
- An explicit API/CLI FPS override changes speed by assigning evenly spaced timestamps and omits audio/subtitles. Desktop jobs send no override. They preserve source timing.
- The HDR control chooses **Preserve HLG/PQ · 10-bit · Labs** or **Convert to SDR · 8-bit** before Start; the video format setting picks AV1, VP9, FFV1 or (with a user-installed FFmpeg) HEVC. Existing preferences default to conversion. Direct worker/API jobs keep `hdr_mode="reject"` by default; opt into `"tone_map"` or `"preserve"`. The source is unchanged.
- Selecting SeedVR2 or another incompatible catalog model switches to SDR and disables the HDR preservation option. Custom image checkpoints retain that option; the worker checks that the loaded architecture is compatible with the HAT preservation adapter.
- Output creation is atomic. Cancellation checks extend through frame skipping and audio/subtitle remuxing; an existing destination survives failures or cancellation.

Packet durations are restored from the source presentation intervals after encoding, so variable frame rate and the last held frame survive AV1, VP9 and FFV1 export. H.264/HEVC exports written by a user-installed FFmpeg use its passthrough timing mode.

## Legacy recordings

LocalSR accepts AVI/DivX, MPEG/VOB, camcorder transport streams,
WMV/ASF, FLV/F4V, 3GP/3G2 and OGV, alongside MP4/MOV/M4V and MKV/WebM. Recordings whose video or audio codec LocalSR does not include (for example DivX, WMV, H.263 or AAC-only tracks that cannot be copied) require a user-installed FFmpeg.
It normalizes flagged interlacing and non-square pixels before enhancement,
converts legacy audio to Opus for MP4, and prepares labelled SDR VP9 playback copies
when the comparison player needs them. Originals and saved exports are preserved.
Comparison copies are limited to 1280 pixels on the longest edge, 30 minutes and 2 GiB of
temporary files, with a 6 GiB session cache that is cleared on exit. DVD menus, disc images and
encrypted media are out of scope.

## Linux playback

Linux builds stream completed video comparisons to WebKitGTK through a private
loopback HTTP listener. WebKitGTK could reject valid H.264
files opened through the custom asset URI scheme, even when the same files
decoded correctly outside the app. Ordinary HTTP byte ranges fix playback and
seeking, including returning to a completed video while another job runs.
Only the original and completed output authorized by the native queue receive
random, session-lifetime URLs. The listener binds to `127.0.0.1`, serves no
directory, streams with bounded buffers, and closes with the app. Processing and
playback remain local. Legacy playback conversion creates compatible copies before this transport.
AppImages ship only royalty-free GStreamer plugins, so H.264 sources and AV1 outputs that the
system cannot play fall back to VP9 playback copies. Windows and macOS keep their asset
transport.

## HDR conversion and import feedback

The existing enhancement models were trained on SDR RGB. In conversion mode, HDR import uses floating-point YUV-to-RGB decoding, the BT.2100 HLG or ST 2084 PQ inverse transfer, BT.2020-to-BT.709 gamut conversion and a fixed highlight-compression curve before final 8-bit quantization. HLG uses a 1000-nit reference display. The curve does not depend on frame histograms, so an exposure change is not introduced by a changing crop or neighbouring frame.

This is an SDR viewing/export conversion, not an HDR-preserving model or mastering workflow. Dolby Vision files with a supported HLG/PQ base layer use that layer; dynamic Dolby Vision metadata is not applied or carried into the SDR output. Other HDR colour primaries require an external SDR conversion. Aesthetic highlight/gamut choices are fixed, not a promise to match every player's tone mapping. This describes conversion mode; the optional preservation path below does not apply that curve to model/export pixels.

Opening media reports actual worker phases (opening, image/first-frame decode, HDR conversion and thumbnail preparation). The canvas animates and shows elapsed time while waiting, including large images and cloud-backed files. It does not invent completion percentages. Failed previews report the cause and offer Try Again; pending or failed sources cannot be started. Video metadata and its thumbnail share one first-frame decode.

## HLG/PQ preservation (HAT · Labs)

Preservation decodes limited-range BT.2020 YUV into float32 RGB using the explicit
BT.2020 matrix. The original HLG/PQ signal enters HAT in FP32. Tile inference,
face blending and optional output resizing stay floating-point; no 8-bit image
or SDR tone map is used in that processing path.

An experimental source constraint adapts SDR-trained HAT detail: for each
expanded input pixel, subtract the model block's mean in linear BT.2020, then add
its remaining detail to the source's linear RGB. Reduce detail uniformly across
channels when needed to remain in gamut. Each block retains the source's mean
scene light (HLG) or display light (PQ). This conservative constraint can introduce
block-boundary texture and is **not perceptual HDR validation or HDR training**.
The two face forks keep their existing SDR training/selection claims and rights.

Export quantizes once to 10-bit AV1, VP9 or FFV1 (or HEVC Main 10 through macOS's encoder or a user-installed
FFmpeg with `hvc1` in MP4), tags BT.2020 primaries / non-constant-luminance matrix / limited
range and the original HLG or PQ transfer. The worker checks for a 10-bit encoder before loading
the model. No fabricated
mastering-display, MaxCLL, or Dolby Vision metadata is added. Dolby Vision dynamic
metadata is omitted; only a supported HLG/PQ base layer is processed. Other colour
primaries/matrices are rejected. SeedVR2 and de-flicker do not support preservation.

All desktop thumbnails and live tiles are **SDR display previews**;
they do not prove HDR display playback. Full HDR mastering, perceptual model quality,
temporal stability and long-clip/platform acceptance remain unverified. FP32 HDR
requires substantially more memory than the SDR byte-buffer path.

Tests check >700 distinct encoded luma levels on a 1024-step ramp, reference
transfer values, source linear-light block means, float tile/face output, metadata,
VFR, trims and AAC. Local acceptance also uses the original rotated 4K HLG MOV and
bounded real MPS inference with both exact HAT face checkpoints, for HLG and PQ.

## Live video progress

Each actual model tile emits progress within the current frame. ETA starts after
the first completed tile; before that the UI says it is measuring. Estimates use
measured fractional frames and update as work proceeds, including hours/days.
Encoding, variable scene complexity and model warm-up can change the estimate.
The active square follows actual model tile boundaries and completed squares show
sampled real output, with a bounded off-thread JPEG encoder. Frame ownership keeps
late previews from painting into a newer frame. An activity indicator also covers
model preparation; it does not invent completed tiles.
The pending checkerboard and active outline use the same rounded canvas pixels as
the worker's output tile, including partial edge tiles. The decorative background
grid is hidden while rendering so it cannot be mistaken for model tile boundaries.
The current decoded source frame remains visible underneath a translucent grid;
completed HAT regions replace it with real model output. Source-frame JPEGs have
their own bounded encoder and frame ownership, so a late source image cannot erase
completed tiles or put an earlier frame beneath a newer frame's output.

## Optional processing

De-flicker is off by default. It blends toward the local temporal median only where neighboring RGB samples differ by at most 12 levels, at half strength. Motion and scene cuts with larger differences bypass filtering. It is deliberately conservative, not optical-flow restoration, and remains Labs. Representative low-contrast motion still needs visual acceptance.

SeedVR2 uses the same timed decoding and encoding contract. The worker applies trim bounds, preserves compatible audio on trims, and enforces output frame counts across context overlap. Unsupported image-engine tile, halo, precision, safe-memory, and de-flicker controls are hidden for this engine in the desktop UI.

The 3B FP16 and FP8 variants support NVIDIA CUDA and AMD ROCm through the
[vendored upstream implementation](https://github.com/numz/ComfyUI-SeedVR2_VideoUpscaler).
ROCm uses PyTorch's `cuda:0` device identifier internally; that identifier does not
require an NVIDIA GPU. FP8 reduces weight storage and download size, while working
memory still depends on the clip and output resolution. HDR preservation remains
disabled for both variants.

Packaged workers include Diffusers' dependency metadata as well as its modules; without the
`requests` distribution record, SeedVR2 failed to load in an early Linux package despite a
healthy worker handshake.

Local acceptance on an RX 9060 XT (16 GB, ROCm 7.2 / Torch 2.13) with
SeedVR2-3B FP8 and memory saving completed five frames from the original rotated
4K HLG MOV at **2160×3840 SDR** in 277.6 seconds. Peak PyTorch allocation was
9.93 GiB, minimum sampled free VRAM 1.33 GiB, and peak sampled worker RSS
10.44 GiB. The output decoded successfully and was visually inspected. The
installed frozen worker also completed eight source frames across two clips at
720×1280 SDR in 64.7 seconds. These are short functional checks, not a completed
four-minute export or a restoration-quality certification. Small VAE tiles can
leave visible grid seams; substantial speed and image-quality tradeoffs remain.

Visual acceptance also found corrupt colour conversion on ROCm 7.2/gfx1200:
tall `[N,3] @ [3,3]` GEMM results diverged after pixel row 524,288, creating
repeated image blocks. The decoded model output was intact before colour
correction. LocalSR uses equivalent per-channel matrix arithmetic on ROCm;
large-image tests compare it against CPU, and a LAB identity check on the AMD
machine reduced mean absolute error from 0.51 to below 0.000001. CUDA/MPS retain
their existing matrix path. This does not change model weights or manufacture
additional image detail.

Its **Output resolution** control either follows the chosen scale or sets the
shorter edge to 256, 512, 720, 1080, 1440 or 2160 pixels. The inspector explicitly
warns when this reduces the source dimensions and loses fine detail. A smaller requested output
downsamples a large input on CPU before buffering or GPU upload; the summary shows
the actual output dimensions. Existing settings keep their selected scale. The
stream holds one model window including context instead of 33 fresh frames, and
reuses models on CPU between clips so the VAE and diffusion weights do not both
remain resident on the GPU. MPS checkpoint dtype conversion also happens on CPU
to avoid retaining two full weight copies on the GPU during loading.
**Reduce GPU memory** is enabled by default, including for existing settings and recipes.
It uses five-frame windows and 128-pixel VAE tiles with 16-pixel overlap. On CUDA and
ROCm, all 32 diffusion blocks, I/O components and intermediate tensors offload to CPU,
using the vendored upstream BlockSwap and tensor-offloading controls. This trades
system RAM and transfer time for lower GPU residency. MPS retains five-frame windows
and 128-pixel tiles without block swapping because its GPU shares system memory.
Disabling the option on CUDA/ROCm restores nine-frame windows and 512-pixel tiles.
Smaller tiles and shorter windows can affect seams and temporal consistency; they
are not a guarantee that large outputs fit. Output resolution never changes silently.

The SeedVR2 inspector measures GPU allocated, reserved, peak allocated and free/total
memory, worker RSS and available system RAM every second during processing. The
current VAE/diffusion stage, output dimensions and clip limit accompany each sample.
Reserved memory includes allocated tensors and PyTorch's cache. Free VRAM also reflects
other applications and allocations outside the PyTorch allocator. These are separate
measurements, not additive estimates. MPS shows allocation without inventing a separate
VRAM pool; unavailable telemetry is labelled. OOM captures are retained before model
cleanup and included in Copy diagnostics. Cached GPU allocations are released
after video jobs, including failures, once exception frames and models are gone. A new job clears the previous sample.
Selecting FP8 loads that exact checkpoint even when the FP16 bundle is also installed.

These controls follow the [upstream memory guidance](https://github.com/numz/ComfyUI-SeedVR2_VideoUpscaler#-limitations).
LocalSR does not disable GPU safety limits or treat a model's weight size or nominal
16 GB baseline as a workload fit guarantee.

SeedVR2 reports real model verification, loading, frame reading, clip encoding,
enhancement, decoding and finishing stages. Its source preview advances with each
model window. During VAE encoding and decoding, the outline follows the actual
overlapping regions, clipped to the unpadded frame. During diffusion, the whole
frame pulses because the model processes the clip together. It shows enhanced
pixels after the clip finishes, without presenting intermediate latents as an
enhanced picture. ETA starts after the first completed clip.
Memory failures suggest reducing the output resolution or using tiled HAT-S;
the app does not disable MPS memory limits or silently reduce output resolution.
Large outputs and long-clip quality still require hardware acceptance.
Local functional acceptance on a 16 GB Apple-silicon Mac completed ten frames from
a rotated 4K HLG source at 256×454 SDR, through three streamed clips, with source
timing and stereo AAC retained. It took about 4½ minutes. This is a small-output
test, not evidence that a full 4K/8K job is practical or that the restoration is
perceptually faithful. Use the frame-by-frame engine when SeedVR2 exceeds the
available memory or time budget.

Video face processing remains Labs because reused masks are not motion tracking. Checkpoint distribution rights are a separate application gate.

## Visual local benchmark

After installing and verifying the Quick model in LocalSR, use a **new** output directory:

```sh
.venv/bin/python scripts/benchmark_video.py --device auto --output /tmp/localsr-video-review
open /tmp/localsr-video-review/index.html
```

The script does not download models or make network requests. FFmpeg constructs small synthetic fixtures; the real Quick model and production PyAV pipeline enhance them. The self-contained report works from disk and includes:

- Five source/output clip pairs: constant rate with audio, variable rate, portrait rotation, a variable-rate trim, and de-flicker.
- Synchronized playback, seeking, optional output audio, downloads, measured frame timing, and exact result JSON.
- A seven-frame moving-square strip from the actual de-flicker function.
- An explicitly identified temporal routing check using an identity substitute, not actual SeedVR2 inference.

This suite is a regression and visual-inspection aid, not a perceptual quality score or a hardware ranking. Passing generated fixtures does not finish installed-platform acceptance. Long clips, resource pressure, queue behaviour, real audio and subtitle combinations and webview playback are covered by the manual cases in [Testing](testing.md).

## Cancellation and memory controls

Processing settings lock from Start until processing or cancellation finishes. Media selection and diagnostics remain available. SeedVR2 checks cancellation between model modules even when live previews are disabled. If a video worker does not stop within eight seconds, the host resets that worker, removes its own partial-output directory and starts a fresh worker. Existing finished exports are preserved.

Reduce GPU memory is available for NVIDIA CUDA and AMD ROCm. Apple Metal and CPU use the smaller clip/tile plan automatically; Metal does not use the CUDA block-offload option. Intel backends do not expose an unimplemented offload switch. HAT’s Safe memory control remains a separate image-pipeline option.
