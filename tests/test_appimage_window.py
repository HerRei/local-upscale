from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "verify_appimage_window", ROOT / "scripts/verify_appimage_window.py"
)
window = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(window)


def fake_tools(monkeypatch, *, capture_code: int = 0, colours: str = "4096", ids: str = ""):
    calls: list[list[str]] = []

    def run(command, **_kwargs):
        calls.append(command)
        if command[0] == "import":
            if capture_code == 0:
                Path(command[-1]).write_bytes(b"png")
            return subprocess.CompletedProcess(command, capture_code, "", "import: no window")
        if command[0] == "identify":
            return subprocess.CompletedProcess(command, 0, colours, "")
        return subprocess.CompletedProcess(command, 0, ids, "")

    monkeypatch.setattr(window.subprocess, "run", run)
    return calls


def test_only_windows_titled_like_the_app_count(monkeypatch):
    calls = fake_tools(monkeypatch, ids="123\n456\n")
    assert window.app_windows({}) == ["123", "456"]
    assert calls[0][-2:] == ["--name", "^LocalSR$"]


def test_a_drawn_window_passes(monkeypatch, tmp_path):
    fake_tools(monkeypatch)
    assert window.screenshot_problem("123", tmp_path / "shot.png", {}) is None


def test_a_failed_screenshot_fails(monkeypatch, tmp_path):
    fake_tools(monkeypatch, capture_code=1)
    assert "screenshot failed" in window.screenshot_problem("123", tmp_path / "shot.png", {})


def test_a_single_colour_window_fails(monkeypatch, tmp_path):
    fake_tools(monkeypatch, colours="1")
    assert "single colour" in window.screenshot_problem("123", tmp_path / "shot.png", {})
