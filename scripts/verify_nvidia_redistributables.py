#!/usr/bin/env python3
"""Fail when a package contains NVIDIA libraries that may not be redistributed.

The policy is ``packaging/nvidia/redistributables.json``. Every file whose name
looks like an NVIDIA CUDA library must match an allowed entry; forbidden or
unrecognized NVIDIA libraries fail the check.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "packaging" / "nvidia" / "redistributables.json"


def classify(names: list[str], policy: dict) -> dict:
    nvidia = re.compile(policy["nvidia_name_pattern"])
    allowed = [(entry, [re.compile(p) for p in entry["patterns"]]) for entry in policy["allowed"]]
    forbidden = [
        (entry, [re.compile(p) for p in entry["patterns"]]) for entry in policy["forbidden"]
    ]
    report: dict = {"allowed": {}, "forbidden": [], "unknown": []}
    for path in names:
        name = Path(path).name
        hit = next((e for e, ps in forbidden if any(p.search(name) for p in ps)), None)
        if hit is not None:
            report["forbidden"].append(
                {"file": path, **{k: hit[k] for k in ("component", "reason")}}
            )
            continue
        entry = next((e for e, ps in allowed if any(p.search(name) for p in ps)), None)
        if entry is not None:
            report["allowed"].setdefault(entry["component"], []).append(path)
        elif nvidia.search(name):
            report["unknown"].append(path)
    report["ok"] = not report["forbidden"] and not report["unknown"]
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tree", type=Path, help="frozen worker, engine payload or AppDir")
    parser.add_argument("--policy", type=Path, default=POLICY)
    arguments = parser.parse_args(argv)
    policy = json.loads(arguments.policy.read_text(encoding="utf-8"))
    names = sorted(
        p.relative_to(arguments.tree).as_posix()
        for p in arguments.tree.rglob("*")
        if p.is_file() or p.is_symlink()
    )
    report = classify(names, policy)
    print(json.dumps(report, indent=2))
    if not report["ok"]:
        print(
            "NVIDIA redistribution check failed: remove the listed libraries or record a verified "
            "license basis in packaging/nvidia/redistributables.json.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
