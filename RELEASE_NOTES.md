# LocalSR 0.0.3 Alpha

LocalSR 0.0.3 Alpha ships a ground-up visual redesign and the first round of workflow
customization. Processing stays on the computer, model files are downloaded only when
selected, and compatible Spandrel checkpoints can also be supplied manually.

## Highlights

- **"Machined Graphite" interface** — a bespoke dark design language: four tonal planes
  joined by shadow-and-catch-light seams, a carved preview well with a floating glass zoom
  HUD, floating compare chips, uppercase micro-cap inspector sections, and a recessed run-
  summary card. The palette is engineered on a computed OKLCH ramp with verified contrast
  on every text tier and accent gradient stop.
- **Custom recipes** — save the current task, model, scale, format, quality, and hardware
  configuration under a name, then apply it in one click. Recipes persist across sessions
  and delete with a hover disc.
- **Info popovers** — an ⓘ beside the model picker lists every compatible model and what it
  is for; another beside Safe memory mode explains its trade-off.
- **Safe memory mode is now opt-in** — it still enables itself automatically when free
  memory is very low.
- **Face-aware restoration** — Detect Faces toggles to Clear Faces so it can always be
  deselected; the curated catalog adds `HAT-S ×4 Face — Restoration` paired with HAT-S.
- **Result actions stay honest** — removing or replacing the image behind the last finished
  result retires Open Result / Show in Folder.
- **GPU detection across vendors** — NVIDIA CUDA, AMD ROCm, Intel XPU (integrated and
  discrete Arc), and Apple Metal are detected when the matching PyTorch backend is
  installed (the interactive installer selects it); the full matrix is covered by tests.

## Requirements

- The interactive installer (`python install.py`) selects a CPU, CUDA, ROCm, or Intel XPU
  PyTorch backend before installing LocalSR.
- Model downloads are on-demand and SHA-256 verified; nothing leaves the computer.

This remains a private alpha for testing; expect rough edges and please report anything
that misbehaves.
