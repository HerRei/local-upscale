#!/usr/bin/env python3
"""Install or mount a Tauri preview package and verify its bundled worker boots."""

from __future__ import annotations

import argparse
import glob
import json
import os
import plistlib
import shutil
import subprocess
import tempfile
from pathlib import Path


def run(
    command: list[str],
    *,
    env: dict[str, str],
    timeout: float,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    print("+", " ".join(command), flush=True)
    return subprocess.run(
        command,
        env=env,
        timeout=timeout,
        check=check,
        capture_output=True,
    )


def app_executable(app: Path) -> Path:
    plist_path = app / "Contents" / "Info.plist"
    with plist_path.open("rb") as handle:
        executable_name = plistlib.load(handle).get("CFBundleExecutable")
    if not executable_name:
        raise RuntimeError(f"CFBundleExecutable is missing from {plist_path}")
    executable = app / "Contents" / "MacOS" / str(executable_name)
    if not executable.is_file():
        raise RuntimeError(f"app executable is missing: {executable}")
    return executable


def mount_dmg(artifact: Path, env: dict[str, str], timeout: float) -> tuple[Path, str]:
    result = run(
        ["hdiutil", "attach", "-nobrowse", "-readonly", "-plist", str(artifact)],
        env=env,
        timeout=timeout,
    )
    payload = plistlib.loads(result.stdout)
    entities = payload.get("system-entities", [])
    mount_points = [entry.get("mount-point") for entry in entities if entry.get("mount-point")]
    devices = [entry.get("dev-entry") for entry in entities if entry.get("dev-entry")]
    if not mount_points or not devices:
        raise RuntimeError("hdiutil did not return a mounted volume and device")
    return Path(mount_points[-1]), str(devices[-1])


def smoke_macos(artifact: Path, report: Path, env: dict[str, str], timeout: float) -> None:
    mount, device = mount_dmg(artifact, env, timeout)
    try:
        apps = sorted(mount.glob("*.app"))
        if len(apps) != 1:
            raise RuntimeError(f"expected one app in the DMG, found {len(apps)}")
        run(
            [str(app_executable(apps[0])), "--smoke-test"],
            env=env,
            timeout=timeout,
        )
    finally:
        run(["hdiutil", "detach", device], env=env, timeout=30, check=False)


def installed_windows_executable(directory: Path) -> Path:
    candidates = sorted(
        path
        for path in directory.rglob("*.exe")
        if not path.name.lower().startswith(("uninstall", "unins"))
        and not {"engine", "resources"}.intersection(
            part.lower() for part in path.relative_to(directory).parts[:-1]
        )
    )
    preferred = [path for path in candidates if "localsr" in path.name.lower()]
    if not preferred:
        raise RuntimeError(f"could not find the installed LocalSR executable in {directory}")
    return min(preferred, key=lambda path: (len(path.relative_to(directory).parts), path.name))


def smoke_windows(artifact: Path, report: Path, env: dict[str, str], timeout: float) -> None:
    with tempfile.TemporaryDirectory(prefix="localsr-next-installer-") as temporary:
        install_dir = Path(temporary) / "app"
        run(
            [str(artifact), "/S", f"/D={install_dir}"],
            env=env,
            timeout=timeout,
        )
        executable = installed_windows_executable(install_dir)
        run([str(executable), "--smoke-test"], env=env, timeout=timeout)
        uninstallers = sorted(install_dir.glob("[Uu]ninstall*.exe")) + sorted(
            install_dir.glob("unins*.exe")
        )
        if uninstallers:
            run([str(uninstallers[0]), "/S"], env=env, timeout=timeout, check=False)


def smoke_linux(artifact: Path, report: Path, env: dict[str, str], timeout: float) -> None:
    artifact.chmod(artifact.stat().st_mode | 0o111)
    env["APPIMAGE_EXTRACT_AND_RUN"] = "1"
    command = [str(artifact), "--smoke-test"]
    if shutil.which("xvfb-run"):
        command = ["xvfb-run", "-a", *command]
    if shutil.which("dbus-run-session"):
        command = ["dbus-run-session", "--", *command]
    run(command, env=env, timeout=timeout)


def validate_report(report: Path) -> dict[str, object]:
    if not report.is_file():
        raise RuntimeError(f"desktop smoke report was not written: {report}")
    payload = json.loads(report.read_text(encoding="utf-8"))
    if payload.get("passed") is not True or payload.get("worker") != "ready":
        raise RuntimeError(f"desktop package smoke failed: {payload}")
    return payload


def resolve_artifact(pattern: str) -> Path:
    matches = [Path(value).resolve() for value in glob.glob(pattern)]
    if len(matches) != 1:
        raise RuntimeError(f"expected one artifact for {pattern!r}, found {len(matches)}")
    if not matches[0].is_file():
        raise RuntimeError(f"artifact is not a file: {matches[0]}")
    return matches[0]


def smoke(artifact: Path, report: Path, timeout: float = 240.0) -> dict[str, object]:
    report = report.resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    report.unlink(missing_ok=True)
    env = os.environ.copy()
    env["LOCALSR_SMOKE_REPORT"] = str(report)
    with tempfile.TemporaryDirectory(prefix="localsr-next-smoke-data-") as data_root:
        env["XDG_DATA_HOME"] = str(Path(data_root) / "xdg")
        env["LOCALAPPDATA"] = str(Path(data_root) / "local")
        suffix = artifact.suffix.lower()
        if suffix == ".dmg":
            smoke_macos(artifact, report, env, timeout)
        elif suffix == ".exe":
            smoke_windows(artifact, report, env, timeout)
        elif suffix == ".appimage":
            smoke_linux(artifact, report, env, timeout)
        else:
            raise RuntimeError(f"unsupported Tauri package type: {artifact.name}")
    payload = validate_report(report)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-glob", required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=240.0)
    args = parser.parse_args()
    smoke(resolve_artifact(args.artifact_glob), args.report, args.timeout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
