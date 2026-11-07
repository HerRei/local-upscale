#!/usr/bin/env python3
"""Fail when any ELF file in an extracted AppImage needs a newer glibc than the oldest supported LTS has.

AppImageHub (the AppImage catalog) counts every dynamically linked ELF file in the payload, even one that is
only loaded in rare cases, and the AppImage's runtime, and tests on the oldest Ubuntu LTS still in standard
support (code/check-libc.sh). Ubuntu 22.04 has glibc 2.35, so that is the default ceiling. Uses objdump from
binutils, as check-libc.sh does.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ELF_MAGIC = b"\x7fELF"
VERSION = re.compile(r"GLIBC_(\d+(?:\.\d+)+)")


def version_key(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def is_elf(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            return stream.read(4) == ELF_MAGIC
    except OSError:
        return False


def required_glibc(path: Path) -> str | None:
    """The newest GLIBC_ version among the file's undefined (required) dynamic symbols, or None."""
    output = subprocess.run(
        ["objdump", "-T", str(path)], capture_output=True, text=True, check=False
    ).stdout
    versions = [
        m.group(1)
        for line in output.splitlines()
        if "*UND*" in line
        for m in [VERSION.search(line)]
        if m
    ]
    return max(versions, key=version_key) if versions else None


def scan(tree: Path) -> dict[str, str]:
    files = [p for p in tree.rglob("*") if p.is_file() and not p.is_symlink() and is_elf(p)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        found = dict(zip(files, pool.map(required_glibc, files), strict=True))
    return {str(path.relative_to(tree)): version for path, version in found.items() if version}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tree", type=Path, help="the extracted AppImage (squashfs-root)")
    parser.add_argument(
        "--appimage", type=Path, required=True, help="the AppImage itself, for its runtime"
    )
    parser.add_argument(
        "--max", default="2.35", help="newest glibc allowed (default: Ubuntu 22.04's 2.35)"
    )
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if not args.tree.is_dir():
        parser.error(f"not a directory: {args.tree}")
    needs = scan(args.tree)
    runtime = required_glibc(args.appimage)
    if runtime:
        needs["(AppImage runtime)"] = runtime
    newest = max(needs.values(), key=version_key) if needs else None
    ceiling = version_key(args.max)
    offenders = sorted(
        ((path, version) for path, version in needs.items() if version_key(version) > ceiling),
        key=lambda item: (version_key(item[1]), item[0]),
        reverse=True,
    )
    report = {
        "max_allowed": args.max,
        "newest_required": newest,
        "elf_files_using_glibc": len(needs),
        "offenders": [{"path": path, "glibc": version} for path, version in offenders],
    }
    if args.report:
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        f"{len(needs)} ELF files use the host's glibc; the newest version any of them needs: {newest}"
    )
    if offenders:
        print(f"{len(offenders)} need more than glibc {args.max}, e.g.:", file=sys.stderr)
        for path, version in offenders[:20]:
            print(f"  GLIBC_{version}  {path}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
