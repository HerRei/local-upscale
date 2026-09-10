# Video support and local acceptance

Standard video is the frame-by-frame SDR path. The isolated `codex/hdr-preservation` preview adds optional HLG/PQ preservation with HAT (Labs); this is not a v0.0.12 installer announcement. SeedVR2, de-flicker, and video face processing retain individual Labs labels. The application remains alpha; installed-platform acceptance is still a blocking release gate.

## Media contract

- Decode SDR and BT.2020 non-constant-luminance HLG/PQ through PyAV. SDR output is H.264 / 8-bit YUV420P; preserved HDR is HEVC Main 10 / YUV420P10LE, in MP4 or MKV. High-bit-depth SDR preservation is not implemented.
- Preserve each frame's presentation timestamp and interval by default, including variable frame rate. Trims are inclusive frame indices; audio starts at the actual selected timestamp. Unknown/non-increasing timestamps fail explicitly.
- Normalize 90°, 180°, 270° rotation and orthogonal mirrors before inference, previews, and export. Camera MOV track translations are rebased to the rotated image bounds. Perspective, scaling and non-right-angle transforms still fail explicitly.
- Copy compatible audio and subtitle streams. Compressed audio trims have packet-level precision, not sample-level precision. Unsupported subtitles produce a warning; unknown additional audio (for example an Apple spatial-audio track) is omitted with a visible import warning when a supported standard track exists. An unsupported sole audio track produces an actionable import error; other incompatible audio produces a remux error. There is no automatic audio transcoding.
- An explicit API/CLI FPS override changes speed by assigning evenly spaced timestamps and omits audio/subtitles. Desktop jobs send no override. They preserve source timing.
- The HDR control chooses **Preserve HLG/PQ · 10-bit HEVC · Labs** or **Convert to SDR · 8-bit H.264** before Start. Existing preferences default to conversion. Direct worker/API jobs keep `hdr_mode="reject"` by default; opt into `"tone_map"` or `"preserve"`. The source is unchanged.
- Selecting SeedVR2 or another incompatible catalog model switches to SDR and disables the HDR preservation option. Custom image checkpoints retain that option; the worker checks that the loaded architecture is compatible with the HAT preservation adapter.
- Output creation is atomic. Cancellation checks extend through frame skipping and audio/subtitle remuxing; an existing destination survives failures or cancellation.

The H.264 encoder disables B-frame reordering to keep packet durations consistent with variable presentation intervals and the last held frame. This trades some compression efficiency for predictable timing.

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

Export quantizes once to 10-bit HEVC, tags BT.2020 primaries / non-constant-luminance
matrix / limited range and the original HLG or PQ transfer, and uses `hvc1` in MP4.
The worker checks for a Main 10 encoder before loading the model. No fabricated
mastering-display, MaxCLL, or Dolby Vision metadata is added. Dolby Vision dynamic
metadata is omitted; only a supported HLG/PQ base layer is processed. Other colour
primaries/matrices are rejected. SeedVR2 and de-flicker do not support preservation.

All desktop thumbnails and live tiles remain explicitly **SDR display previews**;
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

## Optional processing

De-flicker is off by default. It blends toward the local temporal median only where neighboring RGB samples differ by at most 12 levels, at half strength. Motion and scene cuts with larger differences bypass filtering. It is deliberately conservative, not optical-flow restoration, and remains Labs. Representative low-contrast motion still needs visual acceptance.

SeedVR2 uses the same timed decoding and encoding contract. The worker applies trim bounds, preserves compatible audio on trims, and enforces output frame counts across context overlap. Unsupported image-engine tile, halo, precision, safe-memory, and de-flicker controls are hidden for this engine in the desktop UI.

Its **Output resolution** control either follows the chosen scale or sets the
shorter edge to 256, 512, 720, 1080, 1440 or 2160 pixels. A smaller requested output
downsamples a large input on CPU before buffering or GPU upload; the summary shows
the actual output dimensions. Existing settings keep their selected scale. The
stream holds one model window including context instead of 33 fresh frames, and
reuses models on CPU between clips so the VAE and diffusion weights do not both
remain resident on the GPU. MPS checkpoint dtype conversion also happens on CPU
to avoid retaining two full weight copies on the GPU during loading.
On MPS, the adapter uses five-frame windows and 128-pixel VAE tiles with 16-pixel
overlap. These reduce memory use, at the cost of more work and possible tile/window
boundary effects; they are not a guarantee that large outputs fit.

SeedVR2 reports real model verification, loading, frame reading, clip encoding,
enhancement, decoding and finishing stages. Its animation indicates activity;
it does not invent HAT tile output. ETA starts after the first completed clip.
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

This suite is a regression and visual-inspection aid, not a perceptual quality score or a hardware ranking. Passing generated fixtures does not finish installed-platform acceptance. Use the video cases in `acceptance-record.example.json` for long clips, resource pressure, queue behavior, real audio/subtitle combinations, and system webview playback. Record those separately for each supported installer/backend.

## Release gate schema

`ci/beta-readiness.json` uses schema 2. Every gate has an explicit `blocking` boolean and a `scope`. A manual pass requires an evidence reference. `standard-video-acceptance` remains blocking and pending; optional temporal and processing Labs gates are nonblocking. Signing, runtime support, licensing, public downloads, and other existing application requirements remain independent blockers.
