# Contributing to LocalSR

LocalSR is intentionally focused: one image, one local super-resolution model, and a safe,
understandable inference path. Changes should preserve that clarity.

## Development setup

Use Python 3.11:

```bash
python3.11 -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Run the checks used by CI:

```bash
ruff check src tests smoke_test_gui.py
ruff format --check src tests smoke_test_gui.py
QT_QPA_PLATFORM=offscreen pytest -q  # PowerShell: $env:QT_QPA_PLATFORM="offscreen"
pyside6-qmllint --unqualified disable --max-warnings 0 src/localsr/ui/qml/*.qml
```

Run `localsr` for any interface or end-to-end change. Do not commit model checkpoints, generated
outputs, virtual environments, or application preferences.

Open `src/localsr/ui/qml/LocalSR.qmlproject` in Qt Design Studio for visual interface work.
`DesignMock.qml` is design-time data only; production values come from `LocalSRController`. Preserve
that boundary so QML stays previewable without importing the inference stack.

## Engineering boundaries

- Keep PyTorch and Spandrel inside the worker process. The GUI must remain alive if inference dies.
- Keep stdout machine-readable JSON; diagnostics belong on stderr.
- Preserve cooperative cancellation, atomic output replacement, and memmap cleanup.
- Never silently disable PyTorch memory safety limits or bypass hardware checks.
- Treat `.pth` and `.pt` files as untrusted pickle input. Curated downloads require pinned hashes.
- Resource figures are estimates. Display uncertainty and prefer measured local calibration.
- Avoid adding generative image synthesis; LocalSR is a conventional super-resolution harness.
- Keep behavior portable across Windows, macOS, and Linux.
- Keep curated model downloads optional, license-attributed, size-pinned, and SHA-256 verified.
- Run a packaged `--smoke-test` after changing PyInstaller hooks, QML imports, or worker startup.

## Pull requests

Keep changes small enough to review, add regression tests, explain user-facing tradeoffs, and note
platform-specific behavior. CI must pass on all three operating systems before merging.
