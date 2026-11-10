import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_importing_the_seedvr2_engine_keeps_the_vendored_optimisation_report_off_stdout():
    # The worker's stdout carries JSON lines only; the vendored module prints an
    # optimisation report at import time unless its once-per-process flag is set.
    env = {k: v for k, v in os.environ.items() if k != "SEEDVR2_OPTIMIZATIONS_LOGGED"}
    env["PYTHONPATH"] = str(ROOT / "src")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import localsr.video_models.seedvr2.engine\n"
            "import localsr.video_models.seedvr2.vendor.optimization.compatibility",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr
    # The vendored memory manager still prints one memory line at import (free
    # memory, or "Memory check failed" without a GPU); only the optimisation
    # report is switched off by the flag.
    unexpected = [line for line in result.stdout.splitlines() if "memory" not in line.lower()]
    assert unexpected == []
