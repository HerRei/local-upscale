# ADR 0006: bounded live-preview transport

Status: accepted for `v0.0.11-alpha`

## Context

LocalSR already sent sampled JPEG thumbnails as Base64 in JSON Lines from the
Python worker through the Rust host to Tauri events. The payloads were small,
but image/video inference performed JPEG encoding synchronously and the Rust
host discarded some work only after the worker had paid that cost.

## Options evaluated

1. **Sampled JPEG/Base64 JSON events.** Already portable through the frozen
   worker and Tauri 2 event bridge. Payload expansion is roughly one third over
   JPEG bytes, but bounded 320-pixel frames at 2 fps remain small.
2. **Metadata event plus app-owned temporary ring files.** Avoids Base64 but
   adds filesystem I/O, lifecycle races, asset-protocol authorization, and
   crash cleanup. On Windows it also risks antivirus/locking latency.
3. **Binary Tauri channel/IPC.** Removes Base64 expansion but needs a second
   worker-to-host binary framing protocol and platform-specific integration;
   Tauri channels do not remove the Python stdout boundary by themselves.

## Decision

Keep the compatible JSON/Base64 carrier, but move JPEG work to a daemon
encoder with a capacity-one queue. Submission is sampled to 2 fps, images are
bounded to 320 pixels and JPEG quality 68, and a newer frame atomically evicts
the one pending stale frame. Every packet carries a job ID and monotonically
increasing sequence. The UI ignores stale jobs/sequences. Progress uses
separate events. Disablement bypasses copying, encoding and thread creation.
Completion/cancellation closes the encoder and discards pending work; encoding
warnings never fail the main job. No preview files are created.

## Measurement

Run `PYTHONPATH=src python scripts/measure_live_preview_transport.py --device
<device> --runs 5` on the release commit. Record the exact output in the
release evidence before tagging. This wall-clock measurement deliberately is
not a noisy CI threshold; the alpha target is approximately five percent
median overhead or less on the standard 256×256 tiled fixture.

Initial implementation measurement on 2026-09-03 used the repository virtual
environment on an Apple-Silicon Mac (`mps`), the verified
`span_photo_x4` checkpoint, five alternating runs, 128-pixel tiles and the
256×256 fixture. Median inference wall time was 0.075467 s with preview disabled
and 0.076108 s with the bounded preview enabled: **0.851% overhead**. This is a
local implementation measurement, not a portable performance claim; it must be
repeated on the final release commit.
