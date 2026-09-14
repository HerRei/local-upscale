"""Launch the separately installed Tauri desktop from the Python CLI."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

EXECUTABLE_VARIABLE = "LOCALSR_DESKTOP_EXECUTABLE"
_LAUNCH_GUARD = "LOCALSR_DESKTOP_LAUNCHER_ACTIVE"
_APP_NAMES = ("LocalSR Beta", "LocalSR Next Preview", "LocalSR")


def find_desktop() -> Path:
    configured = os.environ.get(EXECUTABLE_VARIABLE)
    if configured:
        candidate = Path(configured).expanduser()
        if not candidate.is_absolute() or not candidate.is_file():
            raise RuntimeError(
                f"{EXECUTABLE_VARIABLE} must name an existing absolute executable path."
            )
        if not os.access(candidate, os.X_OK):
            raise RuntimeError(f"{EXECUTABLE_VARIABLE} is not executable: {candidate}")
        return candidate.resolve()

    executable = shutil.which("localsr-next")
    if executable:
        return Path(executable).resolve()

    candidates: list[Path] = []
    if sys.platform == "darwin":
        for parent in (Path.home() / "Applications", Path("/Applications")):
            candidates.extend(
                parent / f"{name}.app" / "Contents" / "MacOS" / "localsr-next"
                for name in _APP_NAMES
            )
    elif sys.platform == "win32":
        for variable in ("LOCALAPPDATA", "ProgramFiles"):
            parent = os.environ.get(variable)
            if parent:
                for folder in (Path(parent), Path(parent) / "Programs"):
                    candidates.extend(folder / name / "localsr-next.exe" for name in _APP_NAMES)
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()

    raise RuntimeError(
        "LocalSR's Tauri desktop app is installed separately from the Python engine. "
        "Open the installed app, put localsr-next on PATH, or set "
        f"{EXECUTABLE_VARIABLE} to its full executable path (or Linux AppImage). "
        "For development, run npm run tauri -- dev in desktop/. "
        "The Python process, watch and benchmark commands remain available; "
        "the optional Qt interface is available with --legacy."
    )


def launch_desktop(arguments: list[str]) -> int:
    if os.environ.get(_LAUNCH_GUARD):
        raise RuntimeError(
            f"{EXECUTABLE_VARIABLE} points back to the Python launcher. "
            "Select the Tauri executable instead."
        )
    executable = find_desktop()
    environment = os.environ.copy()
    environment[_LAUNCH_GUARD] = "1"
    try:
        return subprocess.run(
            [str(executable), *arguments], env=environment, check=False
        ).returncode
    except OSError as error:
        raise RuntimeError(f"Could not launch LocalSR desktop: {error}") from error
