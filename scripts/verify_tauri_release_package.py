#!/usr/bin/env python3
"""Verify the native container and bundled binaries of a Tauri release package."""

from __future__ import annotations

import argparse
import json
import plistlib
import struct
import subprocess
from pathlib import Path

from verify_macho_tree import inspect as inspect_macho_tree

ELF_ARCHES = {62: "x86_64", 183: "arm64"}
PE_ARCHES = {0x014C: "x86", 0x8664: "x86_64", 0xAA64: "arm64"}


def read_elf_arch(path: Path) -> str:
    with path.open("rb") as stream:
        header = stream.read(64)
    if len(header) < 20 or header[:4] != b"\x7fELF":
        raise ValueError(f"{path.name} is not an ELF package")
    endian = "<" if header[5] == 1 else ">" if header[5] == 2 else None
    if endian is None:
        raise ValueError(f"{path.name} has invalid ELF endianness")
    machine = struct.unpack_from(endian + "H", header, 18)[0]
    return ELF_ARCHES.get(machine, f"elf-machine-{machine}")


def read_pe_arch(path: Path) -> str:
    with path.open("rb") as stream:
        header = stream.read(65536)
    if len(header) < 64 or header[:2] != b"MZ":
        raise ValueError(f"{path.name} is not a PE package")
    offset = struct.unpack_from("<I", header, 0x3C)[0]
    if offset + 6 > len(header) or header[offset : offset + 4] != b"PE\0\0":
        raise ValueError(f"{path.name} has an invalid PE header")
    machine = struct.unpack_from("<H", header, offset + 4)[0]
    return PE_ARCHES.get(machine, f"pe-machine-0x{machine:04x}")


def mount_dmg(path: Path) -> tuple[Path, str]:
    result = subprocess.run(
        ["hdiutil", "attach", "-nobrowse", "-readonly", "-plist", str(path)],
        check=True,
        capture_output=True,
    )
    payload = plistlib.loads(result.stdout)
    entities = payload.get("system-entities", [])
    mounts = [entry.get("mount-point") for entry in entities if entry.get("mount-point")]
    devices = [entry.get("dev-entry") for entry in entities if entry.get("dev-entry")]
    if not mounts or not devices:
        raise ValueError("hdiutil did not report a mounted volume")
    return Path(mounts[-1]), str(devices[-1])


def verify_macos(path: Path, architecture: str) -> dict[str, object]:
    with path.open("rb") as stream:
        stream.seek(-512, 2)
        if stream.read(4) != b"koly":
            raise ValueError(f"{path.name} is not an Apple UDIF disk image")
    mount, device = mount_dmg(path)
    try:
        apps = sorted(mount.glob("*.app"))
        if len(apps) != 1:
            raise ValueError(f"expected exactly one app in {path.name}, found {len(apps)}")
        with (apps[0] / "Contents" / "Info.plist").open("rb") as stream:
            executable_name = plistlib.load(stream).get("CFBundleExecutable")
        if not executable_name:
            raise ValueError("the bundled app has no CFBundleExecutable")
        main = apps[0] / "Contents" / "MacOS" / str(executable_name)
        report = inspect_macho_tree(
            apps[0],
            {architecture},
            main,
            excludes=(
                "*/cv2/.dylibs/*",
                "*/_internal/libavcodec.*",
                "*/_internal/libavformat.*",
                "*/_internal/libavutil.*",
                "*/_internal/libjxl*",
                "*/_internal/libogg.*",
                "*/_internal/libswresample.*",
                "*/_internal/libswscale.*",
            ),
        )
        report["container"] = "Apple UDIF disk image"
        report["artifact"] = path.name
        return report
    finally:
        subprocess.run(["hdiutil", "detach", device], check=True, capture_output=True)


def verify_windows(path: Path, architecture: str, smoke_report: Path | None) -> dict[str, object]:
    container_architecture = read_pe_arch(path)
    if smoke_report is None or not smoke_report.is_file():
        raise ValueError("Windows NSIS verification requires the installed-package smoke report")
    smoke = json.loads(smoke_report.read_text(encoding="utf-8"))
    evidence = smoke.get("package_architecture")
    if not isinstance(evidence, dict) or evidence.get("package_type") != "nsis":
        raise ValueError("Windows smoke report has no NSIS payload architecture evidence")
    container = evidence.get("container")
    if not isinstance(container, dict) or container.get("architecture") != container_architecture:
        raise ValueError("Windows smoke report does not match the NSIS PE bootstrap")
    payloads = evidence.get("native_payloads")
    if not isinstance(payloads, list):
        raise ValueError("Windows smoke report has no native payload list")
    by_role = {
        item.get("role"): item
        for item in payloads
        if isinstance(item, dict) and isinstance(item.get("role"), str)
    }
    missing = sorted({"host", "worker"} - set(by_role))
    mismatches = [
        {
            "role": role,
            "path": item.get("path"),
            "actual": item.get("architecture"),
            "expected": architecture,
        }
        for role, item in sorted(by_role.items())
        if item.get("format") != "PE" or item.get("architecture") != architecture
    ]
    if missing:
        mismatches.append({"missing_roles": missing})
    report = {
        "schema_version": 1,
        "artifact": path.name,
        "container": "NSIS PE installer",
        "container_architecture": container_architecture,
        "required_architectures": [architecture],
        "native_binary_count": len(payloads),
        "main": by_role.get("host"),
        "payloads": payloads,
        "mismatches": mismatches,
        "result": "PASS" if not mismatches else "FAIL",
    }
    if mismatches:
        raise ValueError(f"Windows NSIS payload architecture verification failed: {mismatches}")
    return report


def verify(
    path: Path,
    platform: str,
    architecture: str,
    smoke_report: Path | None = None,
) -> dict[str, object]:
    if platform == "macos":
        report = verify_macos(path, architecture)
        if report.get("result") != "PASS":
            raise ValueError(f"Mach-O verification failed: {report.get('mismatches')}")
        return report
    if platform == "windows":
        return verify_windows(path, architecture, smoke_report)
    elif platform == "linux":
        actual = read_elf_arch(path)
        container = "ELF"
    else:
        raise ValueError(f"unsupported platform: {platform}")
    report = {
        "schema_version": 1,
        "artifact": path.name,
        "container": container,
        "required_architectures": [architecture],
        "native_binary_count": 1,
        "main": {"path": str(path), "architectures": [actual]},
        "mismatches": [] if actual == architecture else [{"actual": actual}],
        "result": "PASS" if actual == architecture else "FAIL",
    }
    if report["result"] != "PASS":
        raise ValueError(f"{path.name} is {actual}, expected {architecture}")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--platform", choices=("linux", "windows", "macos"), required=True)
    parser.add_argument("--architecture", choices=("x86_64", "arm64"), required=True)
    parser.add_argument("--smoke-report", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = verify(args.artifact.resolve(), args.platform, args.architecture, args.smoke_report)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
