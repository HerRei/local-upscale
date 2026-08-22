# Local CI

This script replicates the GitHub Actions CI pipeline locally, so you can verify your changes before pushing.

## Why?

- **Save minutes**: GitHub Actions has usage limits on the free tier
- **Faster feedback**: No waiting for CI to queue and run
- **Offline development**: Work without internet

## Prerequisites

- Python 3.11+
- pip (usually included with Python)

## Usage

```bash
# Run everything (creates .venv, installs deps, runs lint + tests)
./local-ci.sh

# Run only linting
./local-ci.sh lint-only

# Run only tests (uses existing .venv if present)
./local-ci.sh test-only

# Use a specific virtual environment
VENV_DIR=.venv311 ./local-ci.sh test-only
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `VENV_DIR` | `.venv` | Path to virtual environment |
| `PYTHON` | `python3` | Python executable |

## What It Does

1. **Lint** (`ruff check` + `ruff format --check`)
2. **Tests** (`pytest -q`)

These match the GitHub Actions CI workflow exactly.

## Docker (Optional)

If you want to exactly match the GitHub Actions environment (Ubuntu latest), you can use `act`:

```bash
brew install act
act  # Runs all workflows locally in Docker
```

Note: The full `act` environment requires ~17GB download and significant disk space.

## Troubleshooting

### "No module named pip"

Create a fresh venv with pip:
```bash
python -m venv .venv
.venv/bin/python -m pip install --upgrade pip
./local-ci.sh
```

### Tests fail on macOS-specific features

Some tests (like the macOS menu integration test) only run on macOS. These failures are expected on Linux/Windows CI.

### CUDA/MPS tests fail

Hardware-specific tests (GPU inference) will fail without the appropriate hardware. These are skipped in CPU-only environments.
