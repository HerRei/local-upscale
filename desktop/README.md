# LocalSR Next Preview

This directory contains the additive Tauri 2 desktop host. It preserves the
current LocalSR workflow and runs the existing Python inference code as a
hidden worker.

## Layering

```text
Svelte UI (no shell or general filesystem access)
  ↕ typed Tauri commands and events
Rust control plane (SQLite queue, downloads, policy, native authority)
  ↕ versioned JSON Lines over stdin/stdout
Python inference worker (PyTorch, Spandrel, RAW, face, PyAV, SeedVR2)
```

The preview has a separate bundle identifier and stores its preferences and
queue under the `LocalSR/next` application-data directory. It only shares the
existing checksum-verified `LocalSR/models` cache. Running or uninstalling it
does not overwrite the released Slint application.

The parity surface currently includes fit-to-window and 1:1 image inspection,
dynamic zoom and bounded panning, non-destructive Single/Batch scope, native
menus and recipe shortcuts, file-open arguments, FIFO image/video jobs,
notifications, interface scaling, and copyable live diagnostics. Video remains
visibly labelled Labs / Experimental.

## Development

Prerequisites are Node.js, Rust stable, Python 3.11, and the normal LocalSR
development environment.

```bash
cd desktop
npm ci
npm run check
npm test
npm run tauri -- dev
```

During development, the Rust host launches `../.venv/bin/python -m
localsr.worker`. Set `LOCALSR_WORKER` to a worker executable to test a frozen
engine.

Finder, Explorer, Dolphin, and Nautilus actions are optional and never install
silently. Enable or remove them from **System integrations** in the app, or run
the packaged executable with `--install-integrations` or
`--uninstall-integrations`. The additive command is named `localsr-next`, so it
does not replace the released app's command. On Linux, place the AppImage where
you intend to keep it before enabling integrations; LocalSR records the stable
AppImage path rather than its temporary runtime mount.

## Production-shaped build

```bash
python -m pip install -e ".[package,video]"
python scripts/build_tauri_preview.py
```

The build script exports the catalog, creates a worker-only PyInstaller engine,
embeds that directory as a Tauri resource, runs frontend checks, and then
builds the native installer. Credential-free local macOS previews receive an
ad-hoc resource seal. The separate public-alpha workflow passes
`--require-signing` and refuses ad-hoc/unsigned substitutes.

Each packaged preview is acceptance-tested by mounting or silently installing
the actual DMG, NSIS setup, or AppImage and launching its bundled worker with
`--smoke-test`. A successful report requires the frozen worker to negotiate the
protocol and reach `ready`; a mocked download or source-tree Python process
does not satisfy this gate.

The existing `.github/workflows/release.yml` remains an independent,
manual-only Slint build and does not invoke the preview build.
`.github/workflows/tauri-preview.yml` verifies all three desktop hosts and can
build short-lived private Windows NSIS and Linux AppImage artifacts when
manually requested. An architecture check rejects accidental coupling between
the two workflows, shared bundle identities, or native authority exposed to
the webview. `.github/workflows/desktop-release.yml` is the only tag-triggered
pipeline; it exposes one conventional package per host and five total assets.
It requires production signatures, a native ARM64 PyTorch 2.13 DMG, and an
installed-package smoke test before publishing. The Intel runner remains a
host-control-plane gate and cannot silently fall back to PyTorch 2.2.

The hosted preview packages deliberately use the portable CPU runtime on
Windows/Linux and the native MPS-capable runtime on Apple Silicon. Selecting
how signed CUDA, DirectML, XPU, and ROCm engine packs are delivered through one
simple installer remains a cutover decision; the worker protocol does not tie
the interface or queue database to one backend.
