# Video support and local acceptance

Standard video is the frame-by-frame SDR path. SeedVR2, de-flicker, and video face processing retain individual Labs labels. The application remains alpha; installed-platform acceptance is still a blocking release gate.

## Media contract

- Decode supported SDR inputs through PyAV. Encode H.264, 8-bit YUV420P into MP4 or MKV. SDR sources with greater bit depth are reduced to 8-bit; this is not a high-bit-depth preservation workflow.
- Preserve each frame's presentation timestamp and interval by default, including variable frame rate. Trims are inclusive frame indices; audio starts at the actual selected timestamp. Unknown/non-increasing timestamps fail explicitly.
- Normalize 90°, 180°, 270° rotation and orthogonal mirrors before inference, previews, and export. Other display transforms fail explicitly instead of silently changing framing.
- Copy compatible audio and subtitle streams. Compressed audio trims have packet-level precision, not sample-level precision. Unsupported subtitles produce a warning; incompatible audio produces an actionable remux error. There is no automatic audio transcoding.
- An explicit API/CLI FPS override changes speed by assigning evenly spaced timestamps and omits audio/subtitles. Desktop jobs send no override. They preserve source timing.
- PQ/HLG HDR inputs require an SDR conversion first. LocalSR rejects them rather than silently treating HDR samples as SDR.
- Output creation is atomic. Cancellation checks extend through frame skipping and audio/subtitle remuxing; an existing destination survives failures or cancellation.

The H.264 encoder disables B-frame reordering to keep packet durations consistent with variable presentation intervals and the last held frame. This trades some compression efficiency for predictable timing.

## Optional processing

De-flicker is off by default. It blends toward the local temporal median only where neighboring RGB samples differ by at most 12 levels, at half strength. Motion and scene cuts with larger differences bypass filtering. It is deliberately conservative, not optical-flow restoration, and remains Labs. Representative low-contrast motion still needs visual acceptance.

SeedVR2 uses the same timed decoding and encoding contract. The worker applies trim bounds, preserves compatible audio on trims, and enforces output frame counts across context overlap. Its actual model inference, memory behavior, and long-clip continuity still require hardware acceptance. Unsupported image-engine tile, halo, precision, safe-memory, and de-flicker controls are hidden for this engine in the desktop UI.

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
