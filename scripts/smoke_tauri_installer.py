#!/usr/bin/env python3
"""Install or mount a Tauri preview package and verify its bundled worker boots."""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import plistlib
import struct
import subprocess
import tempfile
import time
from pathlib import Path

PE_ARCHES = {0x014C: "x86", 0x8664: "x86_64", 0xAA64: "arm64"}


def temporary_directory(prefix: str) -> tempfile.TemporaryDirectory[str]:
    kwargs = {"prefix": prefix}
    if os.name == "nt":
        kwargs["ignore_cleanup_errors"] = True
    return tempfile.TemporaryDirectory(**kwargs)


def run(
    command: list[str],
    *,
    env: dict[str, str],
    timeout: float,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    print("+", " ".join(command), flush=True)
    try:
        result = subprocess.run(
            command,
            env=env,
            timeout=timeout,
            check=False,
            capture_output=True,
        )
    except subprocess.TimeoutExpired as error:
        print_process_output(error.stdout, error.stderr)
        raise
    if result.returncode:
        print_process_output(result.stdout, result.stderr)
    if check:
        result.check_returncode()
    return result


def print_process_output(stdout: bytes | str | None, stderr: bytes | str | None) -> None:
    for label, content in (("stdout", stdout), ("stderr", stderr)):
        if isinstance(content, bytes):
            decoded = content.decode(errors="replace").strip()
        else:
            decoded = (content or "").strip()
        if decoded:
            print(f"--- process {label} ---\n{decoded}", flush=True)


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


def detach_dmg(device: str, env: dict[str, str], timeout: float, attempts: int = 5) -> None:
    for attempt in range(attempts):
        result = run(
            ["hdiutil", "detach", device],
            env=env,
            timeout=timeout,
            check=False,
        )
        if result.returncode == 0:
            return
        if attempt + 1 < attempts:
            # The app has exited, but its worker or LaunchServices can retain a
            # read handle for a fraction of a second on a cold mounted bundle.
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"could not detach the mounted test image {device}")


def smoke_macos(artifact: Path, report: Path, env: dict[str, str], timeout: float) -> None:
    mount, device = mount_dmg(artifact, env, timeout)
    try:
        apps = sorted(mount.glob("*.app"))
        if len(apps) != 1:
            raise RuntimeError(f"expected one app in the DMG, found {len(apps)}")
        run(
            # Run before Tauri/WebView/single-instance initialization. A
            # developer preview may already be open on an acceptance Mac; a
            # normal --smoke-test launch would then forward to that process
            # and falsely exit without testing the mounted bundle's worker.
            [str(app_executable(apps[0])), "--headless-smoke-test"],
            env=env,
            timeout=timeout,
        )
        verify_backend(report, env, timeout)
    finally:
        detach_dmg(device, env, min(timeout, 30.0))


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


def read_pe_arch(path: Path) -> str:
    with path.open("rb") as stream:
        header = stream.read(65536)
    if len(header) < 64 or header[:2] != b"MZ":
        raise RuntimeError(f"{path.name} is not a PE binary")
    offset = struct.unpack_from("<I", header, 0x3C)[0]
    if offset + 6 > len(header) or header[offset : offset + 4] != b"PE\0\0":
        raise RuntimeError(f"{path.name} has an invalid PE header")
    machine = struct.unpack_from("<H", header, offset + 4)[0]
    return PE_ARCHES.get(machine, f"pe-machine-0x{machine:04x}")


def record_windows_payload_architectures(
    artifact: Path, install_dir: Path, executable: Path, report: Path
) -> None:
    payload = json.loads(report.read_text(encoding="utf-8"))
    worker_value = payload.get("worker_path")
    if not isinstance(worker_value, str) or not worker_value:
        raise RuntimeError("the installed worker did not report its executable path")
    worker = Path(worker_value).resolve()
    root = install_dir.resolve()
    binaries = (("host", executable.resolve()), ("worker", worker))
    native_payloads = []
    for role, binary in binaries:
        if not binary.is_file():
            raise RuntimeError(f"installed {role} executable is missing: {binary}")
        try:
            relative = binary.relative_to(root)
        except ValueError as error:
            raise RuntimeError(f"installed {role} escaped the test directory: {binary}") from error
        native_payloads.append(
            {
                "role": role,
                "path": relative.as_posix(),
                "format": "PE",
                "architecture": read_pe_arch(binary),
            }
        )
    payload["package_architecture"] = {
        "package_type": "nsis",
        "container": {
            "role": "nsis-bootstrap",
            "format": "PE",
            "architecture": read_pe_arch(artifact),
        },
        "native_payloads": native_payloads,
    }
    report.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def smoke_windows(artifact: Path, report: Path, env: dict[str, str], timeout: float) -> None:
    with temporary_directory(prefix="localsr-next-installer-") as temporary:
        install_dir = Path(temporary) / "app"
        run(
            [str(artifact), "/S", f"/D={install_dir}"],
            env=env,
            timeout=timeout,
        )
        executable = installed_windows_executable(install_dir)
        # Self-hosted Windows Actions runners commonly run as a service in
        # Session 0. Exercise the installed Rust host and bundled worker before
        # WebView initialization so this acceptance test does not require an
        # interactive desktop.
        run([str(executable), "--headless-smoke-test"], env=env, timeout=timeout)
        record_windows_payload_architectures(artifact, install_dir, executable, report)
        payload_path = install_dir / "engine-payload.json"
        if payload_path.is_file():
            data = json.loads(report.read_text())
            data["engine_payload"] = json.loads(payload_path.read_text())
            data["engine_payload_sha256"] = hashlib.sha256(payload_path.read_bytes()).hexdigest()
            report.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        verify_backend(report, env, timeout)
        uninstallers = sorted(install_dir.glob("[Uu]ninstall*.exe")) + sorted(
            install_dir.glob("unins*.exe")
        )
        if uninstallers:
            run([str(uninstallers[0]), "/S"], env=env, timeout=timeout, check=False)


def smoke_linux(artifact: Path, report: Path, env: dict[str, str], timeout: float) -> None:
    artifact.chmod(artifact.stat().st_mode | 0o111)
    env["APPIMAGE_EXTRACT_AND_RUN"] = "1"
    # Self-hosted Linux runners may not provide a functional GTK/Wayland
    # session even under Xvfb. Exercise the actual AppImage host, its resource
    # layout and bundled worker before WebView initialization instead.
    run([str(artifact), "--headless-smoke-test"], env=env, timeout=timeout)
    # APPIMAGE_EXTRACT_AND_RUN removes its temporary tree when the host exits.
    # Extract an owned copy so the identity probe exercises that exact payload.
    if env.get("LOCALSR_SMOKE_BACKEND"):
        with tempfile.TemporaryDirectory(prefix="localsr-backend-appimage-") as temporary:
            subprocess.run(
                [str(artifact), "--appimage-extract"],
                cwd=temporary,
                check=True,
                stdout=subprocess.DEVNULL,
                timeout=timeout,
            )
            workers = list((Path(temporary) / "squashfs-root").rglob("localsr-worker"))
            if len(workers) != 1:
                raise RuntimeError("AppImage does not contain exactly one frozen worker")
            verify_backend(report, env, timeout, worker=workers[0])


def verify_backend(
    report: Path, env: dict[str, str], timeout: float, *, worker: Path | None = None
) -> None:
    backend = env.get("LOCALSR_SMOKE_BACKEND")
    if not backend:
        return
    data = json.loads(report.read_text())
    worker = worker or Path(data["worker_path"])
    probe_path = report.with_name(report.stem + ".backend.json")
    probe_path.unlink(missing_ok=True)
    run(
        [str(worker), "--backend-probe", backend, "--output", str(probe_path)],
        env=env,
        timeout=timeout,
    )
    data["backend_probe"] = json.loads(probe_path.read_text())
    report.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


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
    env["LOCALSR_SMOKE_TIMEOUT_SECONDS"] = str(int(timeout))
    with temporary_directory(prefix="localsr-next-smoke-data-") as data_root:
        env["XDG_DATA_HOME"] = str(Path(data_root) / "xdg")
        env["LOCALAPPDATA"] = str(Path(data_root) / "local")
        suffix = artifact.suffix.lower()
        try:
            if suffix == ".dmg":
                smoke_macos(artifact, report, env, timeout)
            elif suffix == ".exe":
                smoke_windows(artifact, report, env, timeout)
            elif suffix == ".appimage":
                smoke_linux(artifact, report, env, timeout)
            else:
                raise RuntimeError(f"unsupported Tauri package type: {artifact.name}")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            if report.is_file():
                payload = json.loads(report.read_text(encoding="utf-8"))
                print(json.dumps(payload, indent=2, sort_keys=True), flush=True)
                if isinstance(error, subprocess.TimeoutExpired):
                    raise RuntimeError(
                        f"desktop package timed out after {error.timeout} seconds: {payload}"
                    ) from error
                raise RuntimeError(
                    f"desktop package exited with {error.returncode}: {payload}"
                ) from error
            if isinstance(error, subprocess.TimeoutExpired):
                raise RuntimeError(
                    f"desktop package timed out after {error.timeout} seconds without a report"
                ) from error
            raise
    payload = validate_report(report)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-glob", required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=240.0)
    parser.add_argument(
        "--backend", choices=("CPU", "CUDA", "DirectML", "Intel-XPU", "AMD-ROCm", "MPS")
    )
    args = parser.parse_args()
    if args.backend:
        os.environ["LOCALSR_SMOKE_BACKEND"] = args.backend
    smoke(resolve_artifact(args.artifact_glob), args.report, args.timeout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
