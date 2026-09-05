# Local video pipeline and visual benchmark handoff

Prepared 2026-09-05 for the agent integrating version 0.0.12.

## Working-tree scope

All implementation changes are local in `/Users/hermesheiniger/LocalSR`, based on
`e4dfa2870708ac8c864b04079ae756b70d5e9873`. No commit, push, tag, release, installer publication,
repository visibility change, or version bump was performed. Version metadata remains
`0.0.11-alpha`; coordinate the 0.0.12 bump with the release work.

`scripts/publish_tauri_release.py` was already modified when this task began. Its change is not
part of this implementation and was left untouched. The existing untracked `.venv_build/`,
`PROJECT.md`, root scratch Python/JSON/text files, `scratch/`, screenshots and runner scripts
also predate this work. Do not stage the entire working tree indiscriminately.

New files belonging to this change:

- `desktop/src/BenchmarkStudio.svelte`
- `desktop/src/BenchmarkStudio.test.ts`
- `scripts/benchmark_video.py`
- `scripts/video_benchmark/index.html`
- `scripts/video_benchmark/report.css`
- `scripts/video_benchmark/report.js`
- `tests/test_video_timing.py`
- `docs/video-support.md`
- `docs/VIDEO_PIPELINE_HANDOFF.md`

Existing-file edits cover video I/O, standard and temporal worker paths, benchmark preview
capture/protocol/storage, desktop and legacy labels, acceptance policy, documentation, and their
regression tests. Review `git diff` for the complete patch.

## Resulting behavior

- Exact source frame timing survives both video engines, including VFR and selected clips.
  Source presentation intervals travel with the pixels. Desktop payloads no longer pass the
  probed average FPS as an implicit speed override.
- Right-angle rotation and mirrors are baked into decoded pixels, so inference, preview dimensions,
  and output orientation agree. Tests compare transforms against FFmpeg's autorotation.
- Temporal trim requests select the intended frames, retain compatible audio, and enforce frame
  counts across 33-frame chunks plus overlap. Identity-engine tests isolate worker routing from
  real SeedVR2 inference.
- De-flicker uses a half-strength median blend only in low-difference regions. Moving high-contrast
  detail and cuts bypass filtering. The feature remains Labs and off by default.
- PQ/HLG HDR fails with an SDR-conversion message. SDR output remains 8-bit. Incompatible subtitles
  produce a warning, and cancellation is checked during remux and skipped-frame decoding.
- Standard video is labeled separately from SeedVR2, de-flicker, and video face processing.
  Unsupported image-engine controls are hidden for SeedVR2 in the desktop UI.
- The system benchmark shows actual source/enhanced warm-up captures for each scene and device,
  with scene selection, comparison reveal, and detail zoom. Captures are bounded to 640 pixels,
  persisted with the local result, and generated outside timed iterations. The v2 workload and
  score formula are unchanged. Cancellation clears unfinished captures; older results still load.
- The standalone video benchmark generates an original motion study, runs the actual verified
  Quick model locally, and produces an offline report with videos, timing diagrams, measurements,
  motion strips, and clip/JSON downloads. It makes no quality-score or installed-runtime claim.

## Local evidence

Generated artifacts are outside the repository:

`/Users/hermesheiniger/LocalSR-video-audit/implementation-benchmark/`

Open `index.html` directly in a browser. The report embeds its result data, so no web server is
needed. A server that lacks byte-range video seeking (such as the simple Python server used during
initial QA) cannot reliably seek these MP4s; the report surfaces that playback limitation. Disk
playback and seeking were verified successfully.

Evidence files include `results.json`, `browser-checks.json`, five actual source/enhanced clip
pairs, `motion-source.png`, `motion-output.png`, `report-desktop.png`, `report-mobile.png`,
`app-benchmark-empty.png`, `app-benchmark-renders.png`, and `app-renders.json`.

The real model run used checksum-verified `span_photo_x4` on MPS, Torch 2.13.0, PyAV 18.1.0:

| Case | Frames | Largest frame timestamp shift | Outcome |
| --- | ---: | ---: | --- |
| Constant rate + audio | 24 | 0 ms | Pass |
| Variable rate + audio | 10 | 0 ms | Pass |
| 90° orientation + audio | 24 | 0 ms | Pass |
| VFR trim, frames 2–6 + audio | 5 | 0 ms | Pass |
| Conservative de-flicker | 24 | 0 ms | Pass |

The moving-square probe retains all seven squares. The temporal worker probe returns exactly
three selected frames and audio. Real SeedVR2 inference was **not run**. Real MPS warm-up renders
were captured for all three unchanged v2 benchmark scenes, including the 3072×2048 tiled scene;
this was not a complete multi-device timed benchmark run and no hardware score was published.

Browser QA used a local headless Chromium because the in-app browser connection was unavailable.
The app screenshot replays worker envelopes with the actual captured MPS renders; it is a frontend
integration check, not a claim that an installed Tauri binary was exercised. Report playback,
trim-offset seeking, scene navigation, comparison reveal, detail controls, and a 390-pixel layout
passed without page errors or horizontal overflow. `browser-checks.json` records the playback
and seeking times.

## Checks completed

- Full Python suite: **483 passed, 2 skipped**, 67.34 seconds. One existing upstream Torch
  `meshgrid` warning appeared.
- Frontend suite: **51 passed**, 8 files.
- Rust library suite: **41 passed**; includes desktop video payload timing regression assertions.
- Svelte/TypeScript check: **0 errors, 0 warnings**. Production frontend build passed.
- Ruff checks on changed Python files and `git diff --check` passed.
- Schema-2 readiness validation passed; it correctly reports **10 blocking gates** and **2 optional
  experimental/pending gates**. `beta_ready` remains false.

Relevant commands, from the repository root:

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
.venv/bin/ruff check src tests scripts
.venv/bin/python scripts/check_beta_readiness.py
.venv/bin/python scripts/benchmark_video.py --device auto --output /tmp/localsr-video-review
```

From `desktop/`:

```sh
npm test
npm run build
cargo test --manifest-path src-tauri/Cargo.toml --lib
cargo fmt --manifest-path src-tauri/Cargo.toml -- --check
```

On this machine, Rust is installed but absent from the default shell path. Use
`/Users/hermesheiniger/.rustup/toolchains/stable-aarch64-apple-darwin/bin` in `PATH` when needed.
The benchmark requires FFmpeg with display-transform fixture options and an already verified
Quick model. It refuses to download weights or overwrite an existing report directory.

## Release review boundaries

`ci/beta-readiness.json` now uses schema **2** with required `blocking` and `scope` fields.
Optional Labs features no longer block an otherwise-qualified beta solely by remaining Labs.
`manual-pass` requires an evidence reference. Standard video still has a blocking
`standard-video-acceptance` gate; signing, runtime, licensing, public download, and physical-platform
requirements were not waived.

Complete the explicit video acceptance cases on the shipped runtimes before claiming that coverage:
long clips, memory/disk pressure, queue behavior, codec/audio/subtitle combinations, and native
webview playback. Audio trims use compressed packet precision. HDR tone mapping, arbitrary display
transforms, real SeedVR2 resource acceptance, and optical-flow de-flicker are outside this patch.

The showcase repository was not modified during this local-only implementation. If the release agent
updates the public showcase for 0.0.12, align its wording with `docs/video-support.md`, keeping the
overall alpha status and per-feature Labs labels accurate.
