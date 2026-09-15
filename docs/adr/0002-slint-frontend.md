# Architecture Decision Record: 0002 - Slint Frontend

## Status

Superseded by ADR 0005. The Slint front end was removed in 0.1.0-beta; the Tauri host is the
only desktop interface.

## Context

LocalSR needs a compact cross-platform desktop-tool interface without a browser runtime or a
web/SaaS visual language. The PyTorch/Spandrel inference worker must remain isolated so GPU failures
do not take down the UI and Torch is never imported into the frontend process.

## Decision

Use Slint for the default visible interface and keep the existing Python inference worker and
newline-delimited JSON protocol.

- `.slint` files define reusable visual components and the application window.
- A small Python host owns presentation state, model downloads, queueing, estimates, and settings.
- A standard-library subprocess bridge owns the inference worker; stdout remains machine-readable
  JSON and stderr remains diagnostic text.
- Bounded JPEG previews and active-tile coordinates cross the protocol. Full-size outputs stay in
  the worker's disk-backed memmap pipeline.
- File selection delegates to native OS dialog services (AppleScript on macOS, system dialogs on
  Windows, and Zenity/KDialog on Linux), avoiding a second GUI toolkit in normal packages.
- The previous Qt frontend is temporarily retained behind `--legacy` as a migration rollback path.

## Consequences

- The application presents one maintainable native-style Slint interface on Windows, macOS, and
  Linux without changing inference behavior.
- Slint UI tests run in subprocesses so the optional legacy pytest-qt suite never mixes Qt and Winit
  event loops on macOS.
- Linux headless tests use Xvfb.
- PyInstaller must collect the Slint native runtime and `.slint` sources alongside the separate
  LocalSR worker executable.
- Slint's Python integration is currently an alpha package, so the pinned version and packaged
  builds require deliberate cross-platform validation before each release.
