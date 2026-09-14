# Legacy-video beta addition

13 September 2026. Implemented in the isolated beta source checkout. Package
builds remain paused until explicit authorization. This Mac's GUI, the active
`.12` release and its hosts/runners were not used or changed.

## Behavior

- Imports and desktop associations cover MP4/MOV/M4V, MKV/WebM, AVI/DivX,
  MPEG (`.mpg`, `.mpeg`, `.mpe`, `.vob`), transport streams (`.ts`, `.mts`, `.m2ts`),
  WMV/ASF, FLV/F4V, 3GP/3G2 and OGV. The container's codec still needs a decoder.
- Tagged interlaced footage uses BWDIF at one output frame per source frame.
  Non-square pixels are normalized to the display aspect ratio before enhancement.
  Untagged interlacing and inverse telecine are not automatically detected.
- MP4 copies AAC/MP3 and converts other decodable soundtracks to 48 kHz AAC.
  This fixes PCM/ADPCM AVI and WMA/WMV export failures. Converted audio is trimmed
  at sample boundaries; AAC padding and existing compressed-packet trim limits
  remain. MKV retains compatible audio. Unsupported subtitle/extra-audio notices
  and the explicit error for an undecodable sole audio track remain.
- Completed comparisons automatically create a compatible local H.264/AAC copy
  for legacy containers. MP4/MOV native decoder failures trigger one fallback
  attempt. The copy is labelled SDR and limited to 1280 pixels on the longest
  edge. The model reads the source; original files and full-resolution saved
  exports are unchanged. Use the saved export for full-resolution assessment.
- The separate codec helper reports frames and elapsed time, with cancellation
  and selection ownership. Returning to a source reuses the session cache. It
  uses two video decoder/encoder threads and never imports the AI runtime.
- Conversion is limited to 30 minutes and 2 GiB of temporary files, with a 6 GiB
  session-cache budget. Failed/cancelled work is removed; normal shutdown clears
  session copies. An abrupt OS/process crash may leave the owned playback folder
  under `LocalSR/next/work`. Cleanup never deletes source media or saved exports.

DVD menus, disc-image navigation, encrypted media and arbitrary damaged codecs
are outside this importer. Individual decodable VOB recordings are supported.

## Verification

Evidence is in `build/beta-review/legacy-video-20260913/`.

The final Python suite passed **611 tests, with 5 skips**: ONNX absent in this
read-only local environment, Windows PowerShell, ROCm hardware, an optional real
Spandrel subprocess fixture, and the unavailable Theora fixture encoder. Previous
native Windows ONNX evidence is preserved. Frontend checks passed **113 tests**;
Svelte reports no errors/warnings and the Vite frontend build passes. Rust's
source suite passed **71 tests, 1 ignored**; after adding disk/time-limit and
cleanup cases, all **6 targeted converter tests** passed. Those two added tests
bring the tested unique Rust cases to 73; the logs distinguish the runs.


- Real fixtures: MPEG-4 Part 2/PCM AVI, MJPEG/ADPCM AVI, DivX-compatible MPEG-4/MP3,
  WMV2/WMA, MPEG-2/MP2, VOB MPEG-2/AC3, H.264/AAC transport stream, FLV/MP3 and
  H.263/AAC 3GP. Check first-frame previews, all decoded output frames, picture
  error, timestamps, soundtrack duration and unchanged source hashes.
- Interlaced anamorphic MPEG: 352×288 at SAR 12:11 becomes 384×288; all 30 frame
  timestamps and trim indices match. Pixels match an independent FFmpeg BWDIF/
  bicubic reference within the stated test tolerance.
- Source-worker conversion: 60 seconds of 720×480 AVI, 1,500 frames, with PCM
  audio. Full output decode, increasing timestamps and AAC duration pass.
  `long-conversion.json` records final duration, time and process-tree memory.
  Synthetic media is removed after the run.
- Real SPAN worker: 12 AVI frames on CPU and 12 WMV frames on MPS, 96×64
  → 384×256, with AAC, real live tiles/frames, measured ETA and unchanged sources.
  The source was a reduced-range synthetic pattern. The initial saturated
  pattern triggered SPAN's existing instability guard; it was rejected before
  export. That failure is retained in `real-worker-saturated-pattern.log` and
  is not hidden by the passing compatibility tests. No guard was weakened.
- Playback helper subprocess: reject invalid AVI, preserve an existing output,
  then convert valid AVI. Blocking Torch import confirms codec-only dispatch.
- Frontend tests cover A→B→A switching, stale progress/completion rejection,
  cancellation, unmount cleanup and a single native-codec fallback attempt.
- Native Rust source tests cover request-specific cancellation, actual owned
  process termination, source-cache invalidation and conversion routing.
- OGV fixture generation is skipped because the local FFmpeg lacks libtheora.
  Fetching a small upstream sample was unavailable due to local DNS resolution.
  OGV is accepted by extension but has no new real-file acceptance result here.

These are source/headless tests. No new installed app, GPU inference result,
Windows WACK result or package compatibility claim follows from them. Earlier
MSIX installation/upgrade evidence and archived review material remain intact.

## Required after the next authorized build

On the authorized macOS, native Intel Windows and DDP Linux test installations:
open AVI/WMV/MPEG via picker, folder and file association; export with audio;
play and seek the comparison; switch between completed results while another
job runs; cancel conversion, retry and restart; verify helper exit/cache cleanup.
Include an OGV fixture and check available decoders/filters in each frozen worker.
Keep the native Windows PC for acceptance only and leave this Mac's GUI alone.

No runtime dependency or lockfile was added. Existing codec redistribution and
corresponding-source review still applies. The separate process is an operational
choice, not a licensing exemption. Primary references: [FFmpeg formats](https://ffmpeg.org/ffmpeg-formats.html),
[BWDIF](https://ffmpeg.org/ffmpeg-filters.html#bwdif),
[PyAV timing](https://pyav.org/docs/stable/api/time.html).
