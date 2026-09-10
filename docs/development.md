# Development checks and code boundaries

Use Python 3.11, Node/npm, a supported Rust toolchain with `rustfmt` and `clippy`,
[actionlint](https://github.com/rhysd/actionlint), and [uv](https://docs.astral.sh/uv/).
On Linux, install Tauri's native build prerequisites as described in
[`desktop/README.md`](../desktop/README.md). Put the Rust toolchain on `PATH`.

```bash
./local-ci.sh setup
./local-ci.sh
```

`setup` explicitly installs the Python development, video, face, and packaging
extras and runs `npm ci`. The check commands reuse that environment; they do not
upgrade dependencies. Set `VENV_DIR` to use another existing virtual environment.

The default command runs Python lint/format checks, workflow syntax validation,
lock/version/catalog/architecture/readiness checks, gradual Python type checking,
the offline Python suite, legacy Slint compilation, Svelte checks/tests/build,
Rust formatting/Clippy/tests, and Python wheel/source package verification. Each
step fails the command immediately on error. Fresh Python package artifacts remain
under `build/local-ci/` for inspection.

Use `./local-ci.sh --help` for individual checks. `lint` and `test` remain aliases
for `lint-only` and `test-only`. Help and invalid commands never install packages.

These checks cover the development host. They do not run foreign OS installers,
certify GPU/driver compatibility, supply signing credentials, or clear the existing
beta-readiness blockers. Release CI still builds every registered backend and
verifies each artifact's source commit and runtime evidence. Real-model acceptance
remains an explicit, networked check: `python scripts/validate_live_models.py`.

Native-library deadlocks may prevent Python's timeout thread from running. The test suite also
uses pytest's C-level fault-handler watchdog: a test that exceeds five minutes prints thread
stacks and fails the process. Pytest 9.1.1 or newer is required for this behavior.

## Image worker

`WorkerServer` owns process messages, job activation, cancellation, and terminal
failure reporting. `ImageJobRunner` owns a single image job's decoding,
preprocessing, primary/face inference, output saving, and temporary resources.
`resolve_image_pipeline` validates the recipe before loading models;
`ImageJobProgress` translates engine callbacks into the existing protocol events.

Preview encoders and temporary writers are released before terminal events,
including on corrupt inputs, output-write failure, and cancellation. The worker
recovery tests send a failing job followed by a valid job through the actual
worker loop and check that the next output succeeds without leftover memmaps.

## Desktop components

`App.svelte` coordinates native events, durable snapshots, model selection, and
application actions. `MediaQueue` renders the imported media and queue state;
`PreviewPane` owns comparison, pan/zoom, sampled tile rendering, video-comparison
loading, and its observer lifetime. `AdvancedSettings` renders output and hardware
settings and emits typed setting patches. Native persistence stays in the parent
and Rust host. `BenchmarkStudio` draws real warm-up tiles for the selected CPU/GPU benchmark.

Asynchronous native subscriptions are disposed even when registration completes
after the component unmounts. Existing interface tests exercise the full workspace
across these component boundaries.

## Gradual Python types

`pyproject.toml` defines the checked boundary: `localsr.protocol` and the pure
pipeline configuration module. Pyright is pinned in the development extra and
runs in the Linux CI test environment, where Qt stubs are installed. Run it locally
with `./local-ci.sh typecheck`.

This is an initial boundary, not a claim that the inference engine or vendored
model code is fully typed. Expand it module by module with real annotations;
resolve errors rather than adding blanket ignores or excluding failing modules.
The retained legacy Qt client is checked alongside the shared protocol messages.
