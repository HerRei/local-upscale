# LocalSR desktop

The desktop app uses Svelte 5 and Tauri 2. Rust handles native operations
and persistent state; a separate Python process runs inference.

```text
Svelte interface
    ↕ typed Tauri commands and events
Rust host · queue, settings, downloads, recovery
    ↕ versioned JSON Lines over stdin/stdout
Python worker · PyTorch, Spandrel, PyAV, RAW, face and SeedVR2 processing
```

The webview has no general shell, filesystem or network API. Native file access
and downloads go through the Rust host. See the [worker protocol](../protocol/README.md)
and [component boundaries](../docs/development.md#desktop-components).

## Run from source

Use Python 3.11, Node.js and a stable Rust toolchain with `rustfmt` and `clippy`.
Install [Tauri's native prerequisites](https://v2.tauri.app/start/prerequisites/)
for your operating system. GPU development also requires the appropriate PyTorch
build and driver; see the backend pins in [`requirements/`](../requirements/).

From the repository root:

```sh
./local-ci.sh setup
cd desktop
npm run tauri -- dev
```

The development host starts the Python worker from the repository's `.venv`.
`LOCALSR_WORKER` can point to a frozen worker executable for packaging tests.
A browser-only workspace is available through `npm run dev:web`; its demo data
is for interface development and does not run inference.

## Check changes

From `desktop/`:

```sh
npm run format:check
npm run check
npm test
npm run build:frontend
cargo fmt --manifest-path src-tauri/Cargo.toml --all -- --check
cargo clippy --manifest-path src-tauri/Cargo.toml --locked --all-targets -- -D warnings
cargo test --manifest-path src-tauri/Cargo.toml --locked --all-targets
```

The repository's [local checks](../docs/development.md) also validate Python,
worker messages, catalog consistency and packaging inputs.

## Build an installer

With the target platform's Python environment active, run from the repository root:

```sh
python -m pip install -e '.[package,video,face]'
python scripts/build_tauri_preview.py
```

The script exports the catalog, freezes the inference worker, checks the frontend
and bundles both in a native installer. Local builds can use ad-hoc macOS signing;
public packages require the platform's production trust checks. A packaged
`--smoke-test` must start the bundled worker and complete its protocol handshake.

Packages exist for macOS MPS, Windows CPU/CUDA/DirectML and Linux CPU/CUDA/ROCm;
[Platforms](../docs/platforms.md) lists what has been tested where. Store builds update
through Microsoft Store; direct builds use [signed updates](../docs/updates.md).

## Application data

Settings, recipes and queues live in `LocalSR/next` under the platform's
application-data directory. Verified checkpoints use the shared `LocalSR/models`
cache.

`python -m localsr` launches the separately installed Tauri executable. If it is
not found, set `LOCALSR_DESKTOP_EXECUTABLE` to its absolute path (the executable
inside the macOS app bundle, `localsr-next.exe`, or a Linux AppImage). Files,
`--recipe`, `--preset` and `--auto-start` pass through unchanged. For source
development, use `npm run tauri -- dev` here. The Python CLI works independently.

MSIX upgrades preserve the profile; uninstalling the Store package can delete it,
so back it up first.

Optional Finder, Explorer, Dolphin and Nautilus actions are managed through
**System integrations**, or with `--install-integrations` / `--uninstall-integrations`.
On Linux, move the AppImage to its permanent location before registering it.
