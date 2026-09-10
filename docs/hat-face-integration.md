# HAT face companion integration — 2026-09-10

Prepared on `codex/hat-face-models`, based on `550d0ba`. This branch includes the
existing HAT-L catalog/checksum commits and completes both companion paths for a
later release. It does not bump app versions, change release automation, move
v0.0.12-alpha, or publish installer/weight assets.

## Changes and evidence

- Stock HAT-L now pairs reciprocally with HAT-L Face, as HAT-S already did.
- The generated Tauri catalog includes all 12 models and the exact HAT-L size/hash.
  Both face entries retain Labs, unresolved rights, and verified external import.
- Native image jobs carry the selected companion ID instead of hardcoding HAT-S.
- Both variants are covered by frontend import tests, catalog policy tests, and
  legacy Slint pairing tests. The GUI catalog assertion excludes both face models
  from its official-upstream URL checks.
- Model cards explain the reported recovery scores, interpolation, and limits.
  The original Task 4 evaluation was not rerun during this integration.

## Verification on this Mac

- Complete Python suite, including PySide6 tests: **510 passed, 2 skipped**.
- Desktop frontend: **55 passed**; Svelte/TypeScript check and Vite build passed.
- Native Rust desktop: **46 passed**; formatting check passed.
- Ruff lint and Pyright passed. Changed Python files pass Ruff format; the broader
  format check finds one pre-existing issue in `scripts/build_tauri_preview.py`,
  which remains unchanged to preserve the release work.
- Both installed face files passed size/SHA-256 verification through `ModelStore`.
  `ModelAdapter` parsed each as HAT, scale 4. Fresh CPU inference transformed
  1×3×32×32 tensors to finite 1×3×128×128 outputs without custom-model trust bypasses.

## Use and release boundary

The updated source lives in the isolated integration worktree. Existing LocalSR
worktrees, installed applications, environments, and the queued v0.0.12 release
remain intact. Select stock HAT-S or HAT-L in a build of this branch and enable
its matching face companion; import the exact allowed checkpoint if absent.
A supported local face detector is required.

The HAT-L release URL in the catalog is reserved and currently unavailable. This
integration publishes documentation, not new weights. The HAT-S release already
exists but does not independently resolve checkpoint rights.
