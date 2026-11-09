"""Exercise the suite's leak detector in a separate pytest process."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "daemon,name,should_fail",
    [
        (False, "test-worker", True),
        (True, "localsr-test-pool", True),
        (True, "external-daemon", False),
    ],
)
def test_thread_leak_fixture(tmp_path, daemon, name, should_fail):
    shutil.copy(Path(__file__).with_name("conftest.py"), tmp_path / "conftest.py")
    (tmp_path / "test_case.py").write_text(
        "import threading\n"
        "def test_thread():\n"
        f"    threading.Thread(target=lambda: threading.Event().wait(3), daemon={daemon!r}, name={name!r}).start()\n",
        encoding="utf-8",
    )
    env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(tmp_path), "-q", "-p", "no:cacheprovider"],
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
    )
    assert (result.returncode != 0) == should_fail, result.stdout + result.stderr
    if should_fail:
        assert "Test leaked threads" in result.stdout
