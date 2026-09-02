# Architecture Decision Record: 0005 - Tauri Control Plane Preview

## Status

Accepted for an additive alpha release. The legacy Slint host remains independently
buildable and installable; the Tauri host may publish prereleases only through the
fail-closed signed pipeline. Public-beta/default-host promotion still requires the
physical acceptance matrix and remaining product decisions.

## Context

LocalSR needs an MIT application harness, a scalable engine boundary, broad
desktop support, and room for multiple image and video engines. The inference
implementation already has the right high-risk boundary: PyTorch, Spandrel,
PyAV, RAW decoding, face detection, and SeedVR2 run in an isolated Python
worker over JSON Lines.

Keeping product state, downloads, queues, and filesystem authority in the
Python UI host makes future engine additions harder to coordinate and ties the
visible application to Slint's separate licensing choices. Rewriting working
inference in Rust or C would reduce model compatibility and substantially
increase backend risk without improving the user workflow.

## Decision

Build a second desktop host with these layers:

1. A Svelte/TypeScript interface provides the same Media → Preview → Enhance
   workspace, native dialogs, comparison, recipes, diagnostics, image tasks,
   and explicitly experimental video controls.
2. A Tauri/Rust control plane owns native authority: persistent SQLite
   queueing, worker lifecycle and restart, settings, output naming, model
   policy, HTTPS downloads, SHA-256 verification, and atomic installation.
3. The existing Python worker remains the inference engine. A versioned,
   language-neutral protocol negotiates image, RAW, face, frame-by-frame
   video, SeedVR2, cancellation, hardware, and progressive-preview features.
4. Models stay external. The MIT application publishes only metadata and a
   policy-aware download option; every model retains its own license and must
   be explicitly labelled where terms are non-commercial or unclear.
5. The Slint build, bundle identifier, and settings remain intact behind a manual
   legacy workflow. The preview uses `com.localsr.desktop.next`, its own settings
   and queue database, the shared verified model cache, and its own signed release
   workflow.

## Consequences

- Existing releases remain reproducible and can coexist with the preview.
- Multiple videos and images use one durable FIFO queue; finishing a video
  automatically dispatches the next item instead of clearing the queue.
- The webview receives typed commands and events but no shell or general
  filesystem plugin. Media and model bytes never need to cross into JavaScript.
- Pickle-based custom checkpoints remain possible only after an explicit
  security acknowledgement; Safetensors is the safe default.
- Packaging produces a worker-only PyInstaller directory embedded as a Tauri
  resource. Alpha publication fails closed until owner-provided Developer ID,
  notarization, and Authenticode credentials pass installed-package verification.
- The new host can coexist as an alpha, but it does not become a public beta or
  overwrite the Slint app until real-media, backend, accessibility, and signed
  physical-install checks pass.
