#!/usr/bin/env bash
# Run the checks available on this development host. See docs/development.md.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"
VENV_DIR="${VENV_DIR:-.venv}"
PYTHON="${PYTHON:-python3}"
VENV_PYTHON="$VENV_DIR/bin/python"
# A reused virtualenv may have an editable install of another checkout.
export PYTHONPATH="$REPO_DIR/src${PYTHONPATH:+:$PYTHONPATH}"

usage() {
    cat <<'HELP'
Usage: ./local-ci.sh [all|setup|lint-only|typecheck|test-only|frontend|rust|package]

  all         Python, Svelte, Rust, and package checks (default)
  setup       Create the Python venv and install development/frontend dependencies
  lint-only   Python lint/format, workflow syntax, and repository policy checks
  typecheck   Check the gradually typed Python boundary
  test-only   Run the offline Python suite
  frontend    Check, test, and build Svelte
  rust        Format, Clippy, and native Rust tests
  package     Build and verify Python wheel/source package contents
  --help      Show this help without installing or running anything

Aliases: lint = lint-only; test = test-only.
Environment: VENV_DIR (default .venv), PYTHON (used by setup; default python3).
Requires Node/npm, Rust with rustfmt and clippy, actionlint, and uv on PATH.
Run setup explicitly when dependencies change; checks do not update them.
Platform installers and physical GPU validation remain release/acceptance checks.
HELP
}

require() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "Required tool missing: $1. See docs/development.md." >&2
        exit 1
    fi
}

require_python() {
    if [ ! -x "$VENV_PYTHON" ]; then
        echo "Missing $VENV_PYTHON. Run ./local-ci.sh setup first." >&2
        exit 1
    fi
}

run_lint() {
    require_python
    require actionlint
    require uv
    "$VENV_PYTHON" -m ruff check src tests scripts
    "$VENV_PYTHON" -m ruff format --check src tests scripts
    "$VENV_PYTHON" -m vulture src scripts packaging ci/vulture_whitelist.py \
        --exclude src/localsr/video_models/seedvr2/vendor
    actionlint -config-file .github/actionlint.yaml .github/workflows/*.yml
    uv lock --check
    "$VENV_PYTHON" scripts/check_release_version.py
    "$VENV_PYTHON" scripts/check_beta_readiness.py
    "$VENV_PYTHON" scripts/export_desktop_catalog.py --check
    "$VENV_PYTHON" scripts/check_desktop_architecture.py
    git diff --check
}

run_typecheck() {
    require_python
    "$VENV_PYTHON" -m pyright --pythonpath "$VENV_PYTHON"
}

run_tests() {
    require_python
    "$VENV_PYTHON" -m pytest tests/ -q --tb=short
}

run_frontend() {
    require npm
    npm --prefix desktop run format:check
    npm --prefix desktop run check
    npm --prefix desktop run knip
    npm --prefix desktop test
    npm --prefix desktop run build:frontend
}

run_rust() {
    require cargo
    cargo fmt --manifest-path desktop/src-tauri/Cargo.toml --all -- --check
    cargo clippy --manifest-path desktop/src-tauri/Cargo.toml --locked --all-targets -- -D warnings
    cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked --all-targets
}

run_package() {
    require_python
    # A fresh output directory avoids validating stale distributions from an
    # earlier version. Preserve the artifacts for inspection after the checks.
    mkdir -p build/local-ci
    local package_dir
    package_dir="$(mktemp -d "$REPO_DIR/build/local-ci/package.XXXXXX")"
    "$VENV_PYTHON" -m build --outdir "$package_dir"
    "$VENV_PYTHON" scripts/verify_python_package_data.py "$package_dir"/*.whl "$package_dir"/*.tar.gz
    echo "Verified Python distributions: $package_dir"
}

setup() {
    require "$PYTHON"
    require npm
    if [ ! -x "$VENV_PYTHON" ]; then
        "$PYTHON" -m venv "$VENV_DIR"
    fi
    "$VENV_PYTHON" -m pip install -e '.[dev,video,face,package]' build
    npm --prefix desktop ci
}

if [ "$#" -gt 1 ]; then
    usage >&2
    exit 2
fi

case "${1:-all}" in
    -h|--help|help) usage; exit 0 ;;
    setup) setup ;;
    lint-only|lint) run_lint ;;
    typecheck) run_typecheck ;;
    test-only|test) run_tests ;;
    frontend) run_frontend ;;
    rust) run_rust ;;
    package) run_package ;;
    all)
        run_lint
        run_typecheck
        run_tests
        run_frontend
        run_rust
        run_package
        ;;
    *) usage >&2; exit 2 ;;
esac

echo "Local CI completed successfully (${1:-all})."
