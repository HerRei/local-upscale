#!/bin/bash
# Local CI runner - replicates GitHub Actions CI locally
# Usage: ./local-ci.sh [lint|test|all|lint-only|test-only]
#
# Environment variables:
#   VENV_DIR   - Virtual environment directory (default: .venv)
#   PYTHON     - Python executable to use (default: python3)
#
# Examples:
#   ./local-ci.sh              # Run all checks
#   ./local-ci.sh lint-only    # Run only lint
#   ./local-ci.sh test-only    # Run only tests
#   VENV_DIR=.venv311 ./local-ci.sh test-only  # Use existing venv

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON="${PYTHON:-python3}"
VENV_DIR="${VENV_DIR:-.venv}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_step() { echo -e "${BLUE}[STEP]${NC} $1"; }

run_lint() {
    log_step "Running lint & format checks..."
    $VENV_DIR/bin/ruff check src tests
    $VENV_DIR/bin/ruff format --check src tests
    log_info "Lint passed!"
}

run_tests() {
    log_step "Running test suite..."
    $VENV_DIR/bin/python -m pytest tests/ -q --tb=short
    log_info "Tests passed!"
}

setup_venv() {
    if [ ! -d "$VENV_DIR" ]; then
        log_info "Creating virtual environment at $VENV_DIR..."
        $PYTHON -m venv "$VENV_DIR"
    fi

    log_info "Installing dependencies..."
    $VENV_DIR/bin/python -m pip install --upgrade pip
    $VENV_DIR/bin/pip install -e ".[dev]" 2>/dev/null || $VENV_DIR/bin/python -m pip install -e ".[dev]"

    # Verify installation
    if ! $VENV_DIR/bin/python -c "import localsr" 2>/dev/null; then
        log_error "Failed to import localsr. Check installation."
        exit 1
    fi
    log_info "Dependencies installed successfully!"
}

show_help() {
    echo "Usage: $0 [command]"
    echo ""
    echo "Commands:"
    echo "  lint-only   Run only lint and format checks"
    echo "  test-only   Run only tests (implies setup)"
    echo "  lint        Run lint + tests (default)"
    echo "  all         Same as lint"
    echo ""
    echo "Environment:"
    echo "  VENV_DIR    Virtual environment directory (default: .venv)"
    echo "  PYTHON      Python executable (default: python3)"
    echo ""
    echo "Examples:"
    echo "  $0                      # Run all checks"
    echo "  $0 lint-only            # Lint only"
    echo "  VENV_DIR=.venv311 $0 test-only  # Use existing venv"
}

case "${1:-lint}" in
    lint-only|lint)
        run_lint
        ;;
    test-only|test)
        setup_venv
        run_tests
        ;;
    all|lint|*)
        setup_venv
        run_lint
        run_tests
        ;;
    -h|--help|help)
        show_help
        ;;
esac

log_info "Local CI completed successfully!"
