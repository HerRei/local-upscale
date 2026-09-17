#!/usr/bin/env python3
"""Download the extra corresponding sources a platform's worker needs.

``packaging/extra-sources.json`` records, for copyleft libraries that third-party wheels
bundle (LibRaw in rawpy, libquadmath in NumPy's OpenBLAS), the exact source archive, its
SHA-256 and the platforms it applies to. This fetches the entries for one platform into a
directory, refuses any archive whose digest differs, and prints the matching
``--extra-source NAME=PATH`` arguments for ``build_source_bundle.py``, one per line.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "packaging" / "extra-sources.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def entries_for(platform: str, registry: dict) -> list[tuple[str, dict]]:
    """The registry entries for ``platform``, as (extra-source name, entry) pairs."""
    return [
        (entry.get("extra_source", key), entry)
        for key, entry in registry["sources"].items()
        if platform in entry["platforms"]
    ]


def fetch(entry: dict, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / entry["url"].rsplit("/", 1)[-1]
    if target.is_file() and sha256(target) == entry["sha256"]:
        return target
    partial = target.with_name(target.name + ".partial")
    request = urllib.request.Request(entry["url"], headers={"User-Agent": "LocalSR-build/1"})
    with urllib.request.urlopen(request, timeout=300) as response, partial.open("wb") as out:
        shutil.copyfileobj(response, out)
    actual = sha256(partial)
    if actual != entry["sha256"]:
        partial.unlink()
        raise SystemExit(f"{entry['url']} has SHA-256 {actual}, expected {entry['sha256']}")
    partial.replace(target)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--platform", required=True, help="for example linux-x86_64")
    parser.add_argument("--destination", type=Path, required=True)
    arguments = parser.parse_args(argv)
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    selected = entries_for(arguments.platform, registry)
    if not selected:
        raise SystemExit(f"no extra sources are registered for {arguments.platform}")
    for name, entry in selected:
        path = fetch(entry, arguments.destination / name)
        print(f"--extra-source={name}={path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
