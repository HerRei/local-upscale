#!/usr/bin/env python3
"""Write explicit testing-only signature evidence for v0.0.11-alpha."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def report(platform: str) -> dict[str, object]:
    if platform == "macos":
        return {
            "status": "ad-hoc-alpha",
            "production_signed": False,
            "ad_hoc": True,
            "notarized": False,
            "stapled": False,
            "gatekeeper_accepted": False,
            "warning": "Testing-only ad-hoc signature; Gatekeeper may reject this alpha.",
        }
    if platform == "windows":
        return {
            "status": "unsigned-alpha",
            "production_signed": False,
            "authenticode": False,
            "timestamped": False,
            "warning": "Testing-only unsigned installer; SmartScreen may warn about this alpha.",
        }
    raise ValueError(f"unsupported alpha signing platform: {platform}")


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=("macos", "windows"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    atomic_json(args.output.resolve(), report(args.platform))
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
