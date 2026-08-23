#!/usr/bin/env python3
"""Recursively require architecture slices in every Mach-O under a directory."""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

CPU_ARCHES = {
    0x00000007: "i386",
    0x01000007: "x86_64",
    0x0000000C: "arm",
    0x0100000C: "arm64",
}


def cpu_name(value: int) -> str:
    return CPU_ARCHES.get(value, f"cpu-0x{value:08x}")


def macho_arches(header: bytes) -> set[str] | None:
    if len(header) < 8:
        return None
    thin = {
        b"\xce\xfa\xed\xfe": "<",
        b"\xcf\xfa\xed\xfe": "<",
        b"\xfe\xed\xfa\xce": ">",
        b"\xfe\xed\xfa\xcf": ">",
    }
    if header[:4] in thin:
        return {cpu_name(struct.unpack_from(thin[header[:4]] + "I", header, 4)[0])}
    fat = {
        b"\xca\xfe\xba\xbe": (">", 20),
        b"\xbe\xba\xfe\xca": ("<", 20),
        b"\xca\xfe\xba\xbf": (">", 32),
        b"\xbf\xba\xfe\xca": ("<", 32),
    }
    if header[:4] not in fat:
        return None
    endian, stride = fat[header[:4]]
    count = struct.unpack_from(endian + "I", header, 4)[0]
    if count > 32 or len(header) < 8 + count * stride:
        raise ValueError(f"truncated fat Mach-O header ({count} slices)")
    return {
        cpu_name(struct.unpack_from(endian + "I", header, 8 + index * stride)[0])
        for index in range(count)
    }


def inspect(root: Path, required: set[str], main: Path | None) -> dict[str, object]:
    mismatches: list[dict[str, object]] = []
    binaries: list[dict[str, object]] = []
    for path in sorted(
        item for item in root.rglob("*") if item.is_file() and not item.is_symlink()
    ):
        with path.open("rb") as stream:
            header = stream.read(4096)
        try:
            arches = macho_arches(header)
        except ValueError as exc:
            mismatches.append({"path": str(path.relative_to(root)), "error": str(exc)})
            continue
        if arches is None:
            continue
        record = {"path": str(path.relative_to(root)), "architectures": sorted(arches)}
        binaries.append(record)
        missing = required - arches
        if missing:
            mismatches.append(record | {"missing": sorted(missing)})
    main_record = None
    if main:
        with main.open("rb") as stream:
            main_arches = macho_arches(stream.read(4096))
        main_record = {"path": str(main), "architectures": sorted(main_arches or [])}
        if main_arches != required:
            mismatches.append(main_record | {"expected_exact": sorted(required)})
    return {
        "schema_version": 1,
        "root": str(root),
        "required_architectures": sorted(required),
        "native_binary_count": len(binaries),
        "main": main_record,
        "mismatches": mismatches,
        "result": "PASS" if binaries and not mismatches else "FAIL",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--require", required=True, help="comma-separated architecture slices")
    parser.add_argument("--main", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    required = {item.strip() for item in args.require.split(",") if item.strip()}
    if not required:
        parser.error("--require cannot be empty")
    report = inspect(args.root.resolve(), required, args.main.resolve() if args.main else None)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    if report["result"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
