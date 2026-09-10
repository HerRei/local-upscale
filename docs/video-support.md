# Video support and local acceptance

Standard video is the frame-by-frame SDR path. SeedVR2, de-flicker, and video face processing retain individual Labs labels. The application remains alpha; installed-platform acceptance is still a blocking release gate.

## Media contract

- Decode supported SDR inputs and BT.2020 HLG/PQ HDR inputs through PyAV in the Next Preview desktop. Encode H.264, 8-bit YUV420P into MP4 or MKV. SDR sources with greater bit depth are reduced to 8-bit; this is not a high-bit-depth preservation workflow.
- Preserve each frame's presentation timestamp and interval by default, including variable frame rate. Trims are inclusive frame indices; audio starts at the actual selected timestamp. Unknown/non-increasing timestamps fail explicitly.
- Normalize 90°, 180°, 270° rotation and orthogonal mirrors before inference, previews, and export. Camera MOV track translations are rebased to the rotated image bounds. Perspective, scaling and non-right-angle transforms still fail explicitly.
- Copy compatible audio and subtitle streams. Compressed audio trims have packet-level precision, not sample-level precision. Unsupported subtitles produce a warning; unknown additional audio (for example an Apple spatial-audio track) is omitted with a visible import warning when a supported standard track exists. An unsupported sole audio track produces an actionable import error; other incompatible audio produces a remux error. There is no automatic audio transcoding.
- An explicit API/CLI FPS override changes speed by assigning evenly spaced timestamps and omits audio/subtitles. Desktop jobs send no override. They preserve source timing.
- The Next Preview desktop explicitly converts BT.2020 HLG/PQ HDR to SDR before enhancement and labels the conversion before Start. Its output is 8-bit BT.709 SDR, with matching colour tags. The source file is unchanged. Direct worker/API jobs retain `hdr_mode="reject"` by default; callers must opt in with `hdr_mode="tone_map"`.
- Output creation is atomic. Cancellation checks extend through frame skipping and audio/subtitle remuxing; an existing destination survives failures or cancellation.

The H.264 encoder disables B-frame reordering to keep packet durations consistent with variable presentation intervals and the last held frame. This trades some compression efficiency for predictable timing.

## HDR conversion and import feedback

The existing enhancement models operate on SDR RGB. HDR import uses floating-point YUV-to-RGB decoding, the BT.2100 HLG or ST 2084 PQ inverse transfer, BT.2020-to-BT.709 gamut conversion and a fixed highlight-compression curve before final 8-bit quantization. HLG uses a 1000-nit reference display. The curve does not depend on frame histograms, so an exposure change is not introduced by a changing crop or neighbouring frame.

This is an SDR viewing/export conversion, not an HDR-preserving model or mastering workflow. Dolby Vision files with a supported HLG/PQ base layer use that layer; dynamic Dolby Vision metadata is not applied or carried into the SDR output. Other HDR colour primaries require an external SDR conversion. Aesthetic highlight/gamut choices are fixed, not a promise to match every player's tone mapping. HDR10/HLG output and 10-bit preservation remain unsupported.

Opening media reports actual worker phases (opening, image/first-frame decode, HDR conversion and thumbnail preparation). The canvas animates and shows elapsed time while waiting, including large images and cloud-backed files. It does not invent completion percentages. Failed previews report the cause and offer Try Again; pending or failed sources cannot be started. Video metadata and its thumbnail share one first-frame decode.

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
