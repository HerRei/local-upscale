"""Small native file-dialog adapters without a second GUI toolkit dependency."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

IMAGE_GLOBS = "*.jpg *.jpeg *.png *.tif *.tiff *.webp *.dng"
MODEL_GLOBS = "*.pth *.pt *.safetensors"


def _initial_directory(initial: str) -> str:
    path = Path(initial).expanduser() if initial else Path.home()
    if path.is_file():
        path = path.parent
    while not path.is_dir() and path != path.parent:
        path = path.parent
    return os.fspath(path if path.is_dir() else Path.home())


def _run(command: list[str], *, environment: dict[str, str] | None = None) -> list[str]:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            env=environment,
        )
    except OSError:
        return []
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _macos_script(mode: str) -> str:
    if mode in {"folder", "output"}:
        prompt = "Add Image Folder" if mode == "folder" else "Select Output Directory"
        chooser = f'choose folder with prompt "{prompt}" default location startLocation'
        body = "set outputText to POSIX path of chosenItem"
    else:
        prompt = "Add Images" if mode == "images" else "Select Model Checkpoint"
        # AppleScript's Standard Additions grammar uses a presence/absence flag,
        # not `multiple selections allowed true/false`.
        multiple = " with multiple selections allowed" if mode == "images" else ""
        chooser = f'choose file with prompt "{prompt}" default location startLocation {multiple}'
        body = """
        if class of chosenItem is list then
            set outputPaths to {}
            repeat with selectedItem in chosenItem
                set end of outputPaths to POSIX path of selectedItem
            end repeat
            set AppleScript's text item delimiters to linefeed
            set outputText to outputPaths as text
        else
            set outputText to POSIX path of chosenItem
        end if
        """
    return f"""
    on reactivateLocalSR()
        tell application "System Events"
            if exists process "LocalSR" then
                set frontmost of process "LocalSR" to true
            end if
        end tell
    end reactivateLocalSR

    on run argv
        try
            set startLocation to POSIX file (item 1 of argv)
            -- Host Standard Additions in a regular foreground application.
            -- A bare `osascript` process is background-only; its otherwise
            -- visible open panel can become click-through above LocalSR.
            tell application "Finder"
                activate
                set chosenItem to {chooser}
            end tell
            {body}
            my reactivateLocalSR()
            return outputText
        on error number -128
            my reactivateLocalSR()
            return ""
        end try
    end run
    """


def _macos_dialog(mode: str, initial: str) -> list[str]:
    helper = _packaged_macos_dialog_helper()
    if helper is not None:
        return _run([os.fspath(helper), mode, _initial_directory(initial)])
    script = _macos_script(mode)
    return _run(["osascript", "-e", script, "--", _initial_directory(initial)])


def _packaged_macos_dialog_helper() -> Path | None:
    if not getattr(sys, "frozen", False):
        return None
    executable = Path(sys.executable)
    candidates = (
        executable.parent.parent / "Frameworks" / "LocalSRDialog",
        executable.with_name("LocalSRDialog"),
    )
    return next((helper for helper in candidates if helper.is_file()), None)


def _windows_dialog(mode: str, initial: str) -> list[str]:
    script = r"""
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.Application]::EnableVisualStyles()
    $mode = $env:LOCALSR_DIALOG_MODE
    $initial = $env:LOCALSR_DIALOG_INITIAL
    $paths = @()
    if ($mode -eq 'folder' -or $mode -eq 'output') {
        $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
        $dialog.Description = if ($mode -eq 'folder') { 'Add Image Folder' } else { 'Select Output Directory' }
        $dialog.SelectedPath = $initial
        if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
            $paths = @($dialog.SelectedPath)
        }
    } else {
        $dialog = New-Object System.Windows.Forms.OpenFileDialog
        $dialog.InitialDirectory = $initial
        $dialog.Multiselect = ($mode -eq 'images')
        $dialog.Title = if ($mode -eq 'images') { 'Add Images' } else { 'Select Model Checkpoint' }
        $dialog.Filter = if ($mode -eq 'images') {
            'Images|*.jpg;*.jpeg;*.png;*.tif;*.tiff;*.webp;*.dng|All files|*.*'
        } else {
            'Models|*.pth;*.pt;*.safetensors|All files|*.*'
        }
        if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
            $paths = @($dialog.FileNames)
        }
    }
    ConvertTo-Json -InputObject @($paths) -Compress
    """
    environment = os.environ.copy()
    environment["LOCALSR_DIALOG_MODE"] = mode
    environment["LOCALSR_DIALOG_INITIAL"] = _initial_directory(initial)
    lines = _run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        environment=environment,
    )
    if not lines:
        return []
    try:
        payload = json.loads("\n".join(lines))
    except json.JSONDecodeError:
        return []
    if isinstance(payload, str):
        return [payload]
    return (
        [str(item) for item in payload if isinstance(item, str)]
        if isinstance(payload, list)
        else []
    )


def _linux_dialog(mode: str, initial: str) -> list[str]:
    directory = _initial_directory(initial)
    if shutil.which("zenity"):
        command = ["zenity", "--file-selection", f"--filename={directory}{os.sep}"]
        if mode in {"folder", "output"}:
            command.extend(["--directory", "--title=Select Folder"])
        elif mode == "images":
            command.extend(
                [
                    "--multiple",
                    "--separator=\n",
                    "--title=Add Images",
                    f"--file-filter=Images | {IMAGE_GLOBS}",
                ]
            )
        else:
            command.extend(
                ["--title=Select Model Checkpoint", f"--file-filter=Models | {MODEL_GLOBS}"]
            )
        return _run(command)

    if shutil.which("kdialog"):
        if mode in {"folder", "output"}:
            return _run(["kdialog", "--getexistingdirectory", directory])
        filter_text = f"Images ({IMAGE_GLOBS})" if mode == "images" else f"Models ({MODEL_GLOBS})"
        command = ["kdialog", "--getopenfilename", directory, filter_text]
        if mode == "images":
            command.extend(["--multiple", "--separate-output"])
        return _run(command)
    return []


def request_dialog(mode: str, initial: str = "") -> list[str]:
    if mode not in {"images", "model", "folder", "output"}:
        return []
    if sys.platform == "darwin":
        return _macos_dialog(mode, initial)
    if sys.platform == "win32":
        return _windows_dialog(mode, initial)
    return _linux_dialog(mode, initial)
