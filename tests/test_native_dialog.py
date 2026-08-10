import shutil
import subprocess
import sys

from localsr.ui import native_dialog


def test_initial_directory_uses_parent_for_a_file(tmp_path):
    source = tmp_path / "photo.png"
    source.write_bytes(b"image placeholder")

    assert native_dialog._initial_directory(str(source)) == str(tmp_path)


def test_macos_dialog_passes_initial_directory_as_an_argument(monkeypatch, tmp_path):
    captured = {}

    def fake_run(command, *, environment=None):
        captured["command"] = command
        captured["environment"] = environment
        return ["/tmp/first.png", "/tmp/second.png"]

    monkeypatch.setattr(native_dialog, "_run", fake_run)

    result = native_dialog._macos_dialog("images", str(tmp_path))

    assert result == ["/tmp/first.png", "/tmp/second.png"]
    assert captured["command"][:2] == ["osascript", "-e"]
    assert captured["command"][-1] == str(tmp_path)


def test_packaged_macos_dialog_uses_bundled_native_helper(monkeypatch, tmp_path):
    executable = tmp_path / "LocalSR.app" / "Contents" / "MacOS" / "LocalSR"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"")
    helper = executable.parent.parent / "Frameworks" / "LocalSRDialog"
    helper.parent.mkdir()
    helper.write_bytes(b"")
    captured = {}

    monkeypatch.setattr(native_dialog.sys, "frozen", True, raising=False)
    monkeypatch.setattr(native_dialog.sys, "executable", str(executable))
    monkeypatch.setattr(
        native_dialog,
        "_run",
        lambda command, *, environment=None: captured.setdefault("command", command) and [],
    )

    native_dialog._macos_dialog("images", str(tmp_path))

    assert captured["command"] == [str(helper), "images", str(tmp_path)]


def test_macos_image_dialog_uses_valid_multiple_selection_grammar():
    script = native_dialog._macos_script("images")

    assert "with multiple selections allowed" in script
    assert "multiple selections allowed true" not in script
    assert 'tell application "Finder"' in script
    assert "activate" in script


def test_macos_dialog_scripts_compile(tmp_path):
    if sys.platform != "darwin" or not shutil.which("osacompile"):
        return

    for mode in ("images", "model", "folder", "output"):
        result = subprocess.run(
            [
                "osacompile",
                "-e",
                native_dialog._macos_script(mode),
                "-o",
                str(tmp_path / f"{mode}.scpt"),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr


def test_windows_dialog_parses_json_array(monkeypatch, tmp_path):
    monkeypatch.setattr(
        native_dialog,
        "_run",
        lambda command, *, environment=None: ['["C:\\\\one.png","C:\\\\two.png"]'],
    )

    assert native_dialog._windows_dialog("images", str(tmp_path)) == [
        "C:\\one.png",
        "C:\\two.png",
    ]


def test_linux_dialog_fails_cleanly_without_desktop_picker(monkeypatch, tmp_path):
    monkeypatch.setattr(native_dialog.shutil, "which", lambda _name: None)

    assert native_dialog._linux_dialog("images", str(tmp_path)) == []


def test_dialog_runner_returns_empty_list_when_command_is_missing(monkeypatch):
    def missing_command(*_args, **_kwargs):
        raise OSError("not installed")

    monkeypatch.setattr(subprocess, "run", missing_command)

    assert native_dialog._run(["missing-dialog"]) == []
