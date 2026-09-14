# Slint retirement

Approved 13 September 2026 (B22), in the separate beta checkout. The active
`.12` checkout, release jobs and runners are outside this cleanup. Existing
binary candidates and their installation/upgrade evidence are retained.

## Changes

- Removed the Slint screens, worker bridge, preview buffer, native dialog/menu
  helpers, dependency and package data. Qt Widgets retains its own dialogs and
  worker client and remains available through the optional `legacy` extra.
- Removed the Slint PyInstaller specification, Inno Setup installer, former
  Linux AppImage wrapper, helper executable, cross-build requirements, manual
  workflow and dedicated compilation/smoke checks. Current Tauri installers and
  the frozen Python worker keep their own packaging.
- Removed Slint from `uv.lock` and all seven current backend locks without
  changing the remaining package versions or hashes. The frozen `.12` cross-alpha
  requirements retain their historical pin; they are not the beta dependency plan.
- `python -m localsr` delegates to the separately installed Tauri executable.
  `LOCALSR_DESKTOP_EXECUTABLE` supplies an explicit path when auto-discovery is
  unavailable. Files, recipes, presets and integration commands pass through as
  literal arguments. Missing installations and launcher loops report an error.
  Discovery recognizes Beta, Next Preview and standard Tauri installation names;
  it only accepts the Tauri executable, so an old Slint installation is not chosen.
- Python processing/watch/benchmark commands and the worker protocol remain.
  The Tauri application ID, state paths, recipes, queue database and model cache
  are unchanged. No installed app or user data is removed by this source cleanup.

## Test migration

The old Slint compilation/layout/callback tests are retired with their components.
Behavior still used by the desktop is covered at the current boundary:

| Former check | Retained or migrated check |
| --- | --- |
| Folder expansion, media types, duplicate inputs | Rust `commands` path-expansion tests |
| Literal launch files, recipes, presets and auto-start | New Python launcher subprocess tests; Rust `launch` and Svelte launch-intent tests |
| Native menu/picker callbacks | Tauri `integrations` tests and installed beta acceptance |
| Batch dispatch and completion ownership | `App.test.ts`, `state.test.ts`, `VideoComparison.test.ts` |
| Bounded live previews, undimmed source, tile geometry | `test_live_preview.py`, `PreviewPane.test.ts`, `progressive-preview.test.ts` |
| Settings atomicity and recipe persistence | Rust `settings`, `update_storage` and recipe tests; Svelte recipe tests |
| Face companion selection | New Rust selection test for both HAT pairs, missing imports and explicit enablement; existing Svelte import/terms checks |
| Model/task filtering and Quick/Best ranking | `state.test.ts` and `App.test.ts` |
| Worker startup/shutdown | Protocol/subprocess tests and Tauri bundled-worker smoke |
| Signing-secret scope | Migrated from the retired manual workflow to the retained signed Tauri workflow |

The complete local check command passed: **595 Python tests, 3 skipped;
110 frontend tests; 67 Rust tests, 1 separately gated production-update test
ignored**. Ruff, Pyright, Svelte checks/build, rustfmt, Clippy, workflow syntax,
lock/catalog/version/architecture checks and wheel/sdist validation passed.
The generated wheel and source package contain no Slint files or dependency;
the wheel retains the Qt client and Python CLI. On native Windows, that wheel was installed without
dependencies in the disposable probe environment: `python -m localsr` launched
the retained Tauri host with `--headless-smoke-test` and its bundled worker
handshake passed. This used the existing executable from the acceptance layout,
not a rebuilt MSIX. The existing 1.0.7.0 installation remains registered for the
acceptance user. The source delta is retained with the logs.
The pre-cleanup source snapshot is retained under
`build/beta-review/runtime-and-slint/`; it includes changes already present in
the beta checkout so removal remains reviewable and reversible.

Historical ADRs, old release notes and prior test reports remain dated records.
They are not instructions to install or package Slint in the beta.
