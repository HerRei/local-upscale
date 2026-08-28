"""Privacy-bounded diagnostic summary and cross-platform clipboard support."""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys


def build_diagnostic_summary(
    *,
    version: str,
    task: str,
    model: str,
    device: str,
) -> str:
    """Return useful support facts without paths, filenames, or user content."""
    return "\n".join(
        (
            f"LocalSR: {version}",
            f"Platform: {platform.system()} {platform.release()} ({platform.machine()})",
            f"Python: {platform.python_version()} ({sys.implementation.name})",
            f"Task: {task or 'Not selected'}",
            f"Model: {model or 'Not selected'}",
            f"Device: {device or 'Not detected'}",
            "Privacy: file paths, media names, and image contents are intentionally omitted.",
        )
    )


def copy_to_clipboard(text: str) -> bool:
    if sys.platform == "darwin":
        commands = [("pbcopy",)]
    elif sys.platform == "win32":
        commands = [
            (
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "Set-Clipboard -Value ([Console]::In.ReadToEnd())",
            )
        ]
    else:
        commands = [("wl-copy",), ("xclip", "-selection", "clipboard")]

    for command in commands:
        if shutil.which(command[0]) is None:
            continue
        try:
            subprocess.run(
                command,
                input=text,
                text=True,
                check=True,
                timeout=5,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except (OSError, subprocess.SubprocessError):
            continue
    return False
