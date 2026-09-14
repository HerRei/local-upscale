from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from localsr import __main__, desktop_launcher


def test_launcher_forwards_literal_arguments_cwd_and_exit_status(monkeypatch, tmp_path):
    monkeypatch.setenv(desktop_launcher.EXECUTABLE_VARIABLE, sys.executable)
    monkeypatch.delenv("LOCALSR_DESKTOP_LAUNCHER_ACTIVE", raising=False)
    monkeypatch.chdir(tmp_path)
    arguments = ["a photo.png", "--recipe", 'Portraits $(touch unwanted) "quoted"', "--auto-start"]
    script = (
        "import json,os,sys; from pathlib import Path; "
        "Path('received.json').write_text(json.dumps([os.getcwd(),sys.argv[1:]])); "
        "sys.exit(17)"
    )
    assert desktop_launcher.launch_desktop(["-c", script, *arguments]) == 17
    assert json.loads((tmp_path / "received.json").read_text()) == [str(tmp_path), arguments]
    assert not (tmp_path / "unwanted").exists()


def test_missing_desktop_explains_installation_without_loading_a_ui(monkeypatch):
    monkeypatch.delenv(desktop_launcher.EXECUTABLE_VARIABLE, raising=False)
    monkeypatch.setattr(desktop_launcher.sys, "platform", "linux")
    monkeypatch.setattr(desktop_launcher.shutil, "which", lambda _name: None)
    with pytest.raises(RuntimeError, match="Tauri desktop app is installed separately"):
        desktop_launcher.find_desktop()


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_discovers_the_beta_installation_without_launching_it(monkeypatch, tmp_path, platform):
    monkeypatch.delenv(desktop_launcher.EXECUTABLE_VARIABLE, raising=False)
    monkeypatch.setattr(desktop_launcher.sys, "platform", platform)
    monkeypatch.setattr(desktop_launcher.shutil, "which", lambda _name: None)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData"))
    if platform == "darwin":
        executable = tmp_path / "Applications/LocalSR Beta.app/Contents/MacOS/localsr-next"
    else:
        executable = tmp_path / "AppData/Programs/LocalSR Beta/localsr-next.exe"
    executable.parent.mkdir(parents=True)
    executable.write_text("launching this test file would fail")
    executable.chmod(0o700)

    assert desktop_launcher.find_desktop() == executable.resolve()


@pytest.mark.parametrize("configured", ["relative/localsr-next", "/missing/localsr-next"])
def test_invalid_override_does_not_launch_another_installation(monkeypatch, configured):
    monkeypatch.setenv(desktop_launcher.EXECUTABLE_VARIABLE, configured)
    monkeypatch.setattr(desktop_launcher.shutil, "which", lambda _name: sys.executable)
    with pytest.raises(RuntimeError, match="existing absolute executable"):
        desktop_launcher.find_desktop()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX executable permission")
def test_nonexecutable_override_is_reported(monkeypatch, tmp_path):
    path = tmp_path / "LocalSR.AppImage"
    path.write_text("not executable")
    path.chmod(0o600)
    monkeypatch.setenv(desktop_launcher.EXECUTABLE_VARIABLE, str(path))
    with pytest.raises(RuntimeError, match="not executable"):
        desktop_launcher.find_desktop()


def test_recursion_is_stopped_before_starting_another_process(monkeypatch):
    monkeypatch.setenv("LOCALSR_DESKTOP_LAUNCHER_ACTIVE", "1")
    runner = Mock()
    monkeypatch.setattr(desktop_launcher.subprocess, "run", runner)
    with pytest.raises(RuntimeError, match="points back to the Python launcher"):
        desktop_launcher.launch_desktop([])
    runner.assert_not_called()


def test_launch_failure_is_readable(monkeypatch):
    monkeypatch.delenv("LOCALSR_DESKTOP_LAUNCHER_ACTIVE", raising=False)
    monkeypatch.setattr(desktop_launcher, "find_desktop", lambda: Path(sys.executable))
    monkeypatch.setattr(
        desktop_launcher.subprocess, "run", Mock(side_effect=OSError("access denied"))
    )
    with pytest.raises(RuntimeError, match="Could not launch LocalSR desktop: access denied"):
        desktop_launcher.launch_desktop([])


@pytest.mark.parametrize(
    "arguments",
    [
        ["a.png", "--preset", "quick", "b.mov", "--recipe", "My recipe", "--auto-start"],
        ["--install-integrations"],
        ["--uninstall-integrations"],
        ["--smoke-test"],
        ["--", "-photo.png"],
    ],
)
def test_python_entrypoint_delegates_desktop_actions_without_reordering(monkeypatch, arguments):
    launch = Mock(return_value=9)
    monkeypatch.setattr(desktop_launcher, "launch_desktop", launch)
    monkeypatch.setattr(sys, "argv", ["localsr", *arguments])
    with pytest.raises(SystemExit) as result:
        __main__.main()
    assert result.value.code == 9
    launch.assert_called_once_with(arguments)


@pytest.mark.parametrize(
    "arguments", [["--help"], ["process", "--help"], ["watch", "--help"], ["benchmark", "--help"]]
)
def test_cli_help_needs_neither_gui_toolkit(tmp_path, arguments):
    # Block imports even if a developer environment still has an old UI wheel.
    script = """
import importlib.abc, runpy, sys
class NoUi(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'slint', 'PySide6'}:
            raise ImportError('GUI toolkit must not be loaded')
sys.meta_path.insert(0, NoUi())
sys.argv = ['localsr', *sys.argv[1:]]
runpy.run_module('localsr', run_name='__main__')
"""
    environment = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    result = subprocess.run(
        [sys.executable, "-c", script, *arguments],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "usage:" in result.stdout
