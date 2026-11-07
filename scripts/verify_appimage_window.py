#!/usr/bin/env python3
"""Start an extracted AppImage the way AppImageHub (the AppImage catalog) tests it, and require a window.

AppImageHub runs the app with ``firejail --net=none`` on a plain X server, waits 10 s, then at most 20 more
for a visible window, and fails the app when it quit or showed none (code/worker.sh). Here the app runs as
the calling user in network, PID and mount namespaces of its own: only a loopback interface exists, as with
firejail, so the update check and every other connection fail as they do for an offline user, and ending
the namespace ends every process the app started. Saves a screenshot. Needs Xvfb, xdotool, ImageMagick's
import, util-linux, iproute2 and password-less sudo (GitHub-hosted runners).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

DISPLAY = ":97"
NETWORK_REACHABLE = 97
# Run as root inside the namespaces: bring up loopback, prove nothing outside answers, then become the user.
ROOT_SCRIPT = (
    "ip link set lo up || exit 96; "
    "if getent ahosts github.com >/dev/null 2>&1 || curl -s --max-time 3 -o /dev/null https://1.1.1.1; "
    f'then echo "network reachable" >&2; exit {NETWORK_REACHABLE}; fi; '
    'exec "$@"'
)


def visible_windows(env: dict[str, str]) -> list[str]:
    found = subprocess.run(
        ["xdotool", "search", "--onlyvisible", "--name", "."],
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
        check=False,
    ).stdout.split()
    return [
        subprocess.run(
            ["xdotool", "getwindowname", window],
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
            check=False,
        ).stdout.strip()
        for window in found
    ]


def end_namespace(pidfile: Path) -> None:
    """SIGKILL the namespace's first process; the kernel then kills every other process in it."""
    if not pidfile.is_file():
        return
    unshare = pidfile.read_text().strip()
    children = subprocess.run(
        ["pgrep", "-P", unshare], capture_output=True, text=True, check=False
    ).stdout.split()
    for child in children:
        subprocess.run(["sudo", "-n", "kill", "-KILL", child], check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--appdir", type=Path, required=True, help="the extracted AppImage (squashfs-root)"
    )
    parser.add_argument(
        "--appimage", type=Path, required=True, help="the AppImage it was extracted from"
    )
    parser.add_argument(
        "--timeout", type=float, default=30, help="seconds to wait for a window (AppImageHub: 30)"
    )
    parser.add_argument("--screenshot", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    for tool in ("Xvfb", "xdotool", "import", "unshare", "setpriv", "pgrep", "sudo"):
        if not shutil.which(tool):
            parser.error(f"{tool} is missing")
    root = args.appdir.resolve()
    appimage = args.appimage.resolve()
    if not (root / "AppRun").is_file():
        parser.error(f"no AppRun in {root}")

    env = {**os.environ, "DISPLAY": DISPLAY}
    passed = {
        key: os.environ[key] for key in ("HOME", "PATH", "LANG", "TMPDIR") if key in os.environ
    }
    # AppImageHub sets the last one for older WebKitGTK; no D-Bus session and no Wayland, as there.
    passed |= {
        "DISPLAY": DISPLAY,
        "APPDIR": str(root),
        "APPIMAGE": str(appimage),
        "ARGV0": str(appimage),
        "WEBKIT_DISABLE_COMPOSITING_MODE": "1",
    }
    xvfb = subprocess.Popen(
        ["Xvfb", DISPLAY, "-screen", "0", "1280x800x24", "-nolisten", "tcp"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    socket = Path(f"/tmp/.X11-unix/X{DISPLAY[1:]}")
    for _ in range(50):
        if socket.exists():
            break
        time.sleep(0.2)
    else:
        xvfb.kill()
        raise SystemExit("Xvfb did not start")

    windows: list[str] = []
    seconds = None
    alive = False
    with tempfile.TemporaryDirectory(prefix="localsr-window-") as temporary:
        pidfile = Path(temporary) / "unshare.pid"
        command = [
            "sudo", "-n", "sh", "-c", 'echo $$ > "$0"; exec "$@"', str(pidfile),
            "unshare", "--net", "--pid", "--fork", "--kill-child", "--mount-proc", "--",
            "sh", "-c", ROOT_SCRIPT, "sh",
            "setpriv", f"--reuid={os.getuid()}", f"--regid={os.getgid()}", "--init-groups", "--",
            "env", "-i", *[f"{key}={value}" for key, value in passed.items()], str(root / "AppRun"),
        ]  # fmt: skip
        started = time.monotonic()
        app = subprocess.Popen(command)
        try:
            while time.monotonic() - started < args.timeout and app.poll() is None:
                windows = visible_windows(env)
                if windows:
                    seconds = round(time.monotonic() - started, 1)
                    # AppImageHub looks again 10 s after the start at the earliest: still running?
                    time.sleep(max(2.0, 10 - seconds))
                    break
                time.sleep(1)
            alive = app.poll() is None
            if windows and alive:
                subprocess.run(
                    ["import", "-window", "root", str(args.screenshot)],
                    env=env,
                    timeout=30,
                    check=False,
                )
        finally:
            end_namespace(pidfile)
            try:
                app.wait(timeout=30)
            except subprocess.TimeoutExpired:
                app.kill()
                app.wait()
            xvfb.terminate()
            xvfb.wait(timeout=10)

    passed_check = bool(windows) and alive and app.returncode != NETWORK_REACHABLE
    report = {
        "passed": passed_check,
        "window_titles": windows,
        "seconds_to_window": seconds,
        "running_after_window": alive if windows else None,
        "exit_code": None if alive else app.returncode,
        "screenshot": str(args.screenshot) if windows and alive else None,
    }
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if app.returncode == NETWORK_REACHABLE:
        print("The namespace could reach the network, so the check proves nothing", file=sys.stderr)
    elif not windows:
        print(f"No visible window within {args.timeout:g} s without network", file=sys.stderr)
    elif not alive:
        print("The app quit right after showing its window", file=sys.stderr)
    return 0 if passed_check else 1


if __name__ == "__main__":
    sys.exit(main())
