#!/usr/bin/env python3
"""Refuse heavyweight CI work unless SSD and HDD reserves remain intact."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

GIB = 1024**3


@dataclass(frozen=True)
class VolumeStatus:
    name: str
    path: str
    total_bytes: int
    used_bytes: int
    free_bytes: int
    reserve_bytes: int
    predicted_peak_bytes: int
    required_free_bytes: int
    passed: bool

    @property
    def free_percent(self) -> float:
        return 100 * self.free_bytes / self.total_bytes


def gib(value: float) -> int:
    return int(value * GIB)


def status_for(
    name: str,
    path: Path,
    min_percent: float,
    min_absolute_bytes: int,
    predicted_peak_bytes: int,
) -> VolumeStatus:
    if not path.exists():
        raise FileNotFoundError(f"{name} path does not exist: {path}")
    total, used, free = shutil.disk_usage(path)
    reserve = max(min_absolute_bytes, int(total * min_percent / 100))
    required = reserve + predicted_peak_bytes
    return VolumeStatus(
        name=name,
        path=str(path.resolve()),
        total_bytes=total,
        used_bytes=used,
        free_bytes=free,
        reserve_bytes=reserve,
        predicted_peak_bytes=predicted_peak_bytes,
        required_free_bytes=required,
        passed=free >= required,
    )


def print_status(item: VolumeStatus) -> None:
    print(f"{item.name}: {item.path}")
    print(f"  total: {item.total_bytes / GIB:.2f} GiB")
    print(f"  free: {item.free_bytes / GIB:.2f} GiB ({item.free_percent:.1f}%)")
    print(f"  protected reserve: {item.reserve_bytes / GIB:.2f} GiB")
    print(f"  predicted build peak: {item.predicted_peak_bytes / GIB:.2f} GiB")
    print(f"  required free before start: {item.required_free_bytes / GIB:.2f} GiB")
    print(f"  result: {'PASS' if item.passed else 'FAIL'}")


def atomic_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ssd-path", type=Path, default=Path("/"))
    parser.add_argument("--hdd-path", type=Path, default=Path("/mnt/hdd"))
    parser.add_argument("--skip-hdd", action="store_true")
    parser.add_argument("--ssd-min-percent", type=float, default=25.0)
    parser.add_argument("--ssd-min-gib", type=float, default=20.0)
    parser.add_argument("--ssd-peak-gib", type=float, default=20.0)
    parser.add_argument("--hdd-min-percent", type=float, default=20.0)
    parser.add_argument("--hdd-min-gib", type=float, default=100.0)
    parser.add_argument("--hdd-peak-gib", type=float, default=20.0)
    parser.add_argument("--metrics", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    statuses = [
        status_for(
            "SSD",
            args.ssd_path,
            args.ssd_min_percent,
            gib(args.ssd_min_gib),
            gib(args.ssd_peak_gib),
        )
    ]
    if not args.skip_hdd:
        statuses.append(
            status_for(
                "HDD",
                args.hdd_path,
                args.hdd_min_percent,
                gib(args.hdd_min_gib),
                gib(args.hdd_peak_gib),
            )
        )
    for item in statuses:
        print_status(item)
    report = {
        "schema_version": 1,
        "timestamp": datetime.now(UTC).isoformat(),
        "volumes": [asdict(item) | {"free_percent": item.free_percent} for item in statuses],
        "passed": all(item.passed for item in statuses),
    }
    if args.metrics:
        atomic_json(args.metrics, report)
    if not report["passed"]:
        print("Storage invariant failed; heavyweight build will not start.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
