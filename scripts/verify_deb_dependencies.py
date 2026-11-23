#!/usr/bin/env python3
"""Fail when an extracted .deb links a system library that its Depends do not install.

Every host ELF file in the package (the Rust host and the frozen worker) loads each
library it needs (DT_NEEDED) either from the package itself or from the system. Each
system library must come from a package that `apt install` brings along with the .deb:
one named in its Depends, a dependency of those, or one every Debian and Ubuntu system
has (Priority: required). GPU driver libraries come with the driver and stay out of
Depends, as they stay out of the AppImage.

Runs on the build host against `dpkg-deb -x` output, before the package is installed
(so `dpkg -S` finds only the system's own copies), with readelf, dpkg and apt-cache.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from build_source_bundle import owning_packages
from build_tauri_preview import LINUXDEPLOY_DRIVER_LIBRARIES
from verify_appimage_libc import HOST_MACHINES, elf_machine, is_elf

NEEDED = re.compile(r"\(NEEDED\)\s+Shared library: \[([^\]]+)\]")
SONAME = re.compile(r"\(SONAME\)\s+Library soname: \[([^\]]+)\]")
# The GPU driver brings these; neither the AppImage nor the .deb ships or depends on them.
DRIVER_LIBRARIES = frozenset(LINUXDEPLOY_DRIVER_LIBRARIES)


def parse_depends(field: str) -> list[list[str]]:
    """Package names of a Depends field, one list of alternatives per entry."""
    groups = []
    for entry in field.split(","):
        names = [
            re.sub(r"\s*\(.*?\)", "", alternative).strip().split(":")[0]
            for alternative in entry.split("|")
        ]
        names = [name for name in names if name]
        if names:
            groups.append(names)
    return groups


def parse_apt_cache_depends(output: str) -> set[str]:
    """Every package `apt-cache depends --recurse` reached (unindented lines)."""
    return {
        line.strip().strip("<>").split(":")[0]
        for line in output.splitlines()
        if line.strip() and not line[0].isspace()
    }


def parse_required(output: str) -> set[str]:
    """Packages that every system has, from `dpkg-query -W` with package, priority, essential."""
    required = set()
    for line in output.splitlines():
        package, _, rest = line.partition("\t")
        priority, _, essential = rest.partition("\t")
        if priority.strip() == "required" or essential.strip() == "yes":
            required.add(package.split(":")[0])
    return required


def dynamic_section(path: Path) -> tuple[set[str], list[str]]:
    """The file's own sonames and the libraries it needs, from `readelf -d`."""
    result = subprocess.run(
        ["readelf", "-d", str(path)], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(f"readelf -d {path} failed: {result.stderr.strip()}")
    return set(SONAME.findall(result.stdout)), NEEDED.findall(result.stdout)


def scan(tree: Path) -> tuple[set[str], dict[str, list[str]]]:
    """What the package provides (file names and sonames) and which files need what."""
    entries = [path for path in tree.rglob("*") if path.is_file() or path.is_symlink()]
    provided = {path.name for path in entries}
    elves = [
        path
        for path in entries
        if not path.is_symlink() and is_elf(path) and elf_machine(path) in HOST_MACHINES
    ]
    needs: dict[str, list[str]] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for path, (sonames, needed) in zip(elves, pool.map(dynamic_section, elves), strict=True):
            provided |= sonames
            for library in needed:
                needs.setdefault(library, []).append(path.relative_to(tree).as_posix())
    return provided, needs


def system_needs(provided: set[str], needs: dict[str, list[str]]) -> dict[str, list[str]]:
    """The needed libraries the package does not carry itself, except the GPU driver's."""
    return {
        library: sorted(users)
        for library, users in sorted(needs.items())
        if library not in provided and library not in DRIVER_LIBRARIES
    }


def missing_libraries(
    external: dict[str, list[str]], owners: dict[str, set[str]], installed_with: set[str]
) -> dict[str, dict[str, list[str]]]:
    """System libraries that no package `apt install` brings along provides."""
    return {
        library: {"owners": sorted(owners.get(library, set())), "used_by": users[:5]}
        for library, users in external.items()
        if not owners.get(library, set()) & installed_with
    }


def _output(command: list[str]) -> str:
    return subprocess.run(command, capture_output=True, text=True, check=True).stdout


def owners_of(library: str) -> set[str]:
    query = subprocess.run(
        ["dpkg", "-S", f"*/{library}"], capture_output=True, text=True, check=False
    )
    return owning_packages(query.stdout)


def installed_with(roots: list[str]) -> set[str]:
    """The Depends, everything they pull in, and the packages every system has."""
    closure = parse_apt_cache_depends(
        _output(
            [
                "apt-cache",
                "depends",
                "--recurse",
                "--no-recommends",
                "--no-suggests",
                "--no-conflicts",
                "--no-breaks",
                "--no-replaces",
                "--no-enhances",
                *roots,
            ]
        )
    )
    required = parse_required(
        _output(["dpkg-query", "-W", "-f", "${Package}\\t${Priority}\\t${Essential}\\n"])
    )
    return closure | required


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tree", type=Path, help="the package extracted with dpkg-deb -x")
    parser.add_argument("--package", type=Path, required=True, help="the .deb, for its Depends")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if not args.tree.is_dir():
        parser.error(f"not a directory: {args.tree}")
    depends = _output(["dpkg-deb", "-f", str(args.package), "Depends"]).strip()
    groups = parse_depends(depends)
    if not groups:
        print(f"{args.package.name} declares no Depends", file=sys.stderr)
        return 1
    provided, needs = scan(args.tree)
    if not needs:
        print(f"no ELF file in {args.tree} needs a library: is it the extracted .deb?")
        return 2
    external = system_needs(provided, needs)
    allowed = installed_with(sorted({name for group in groups for name in group}))
    owners = {library: owners_of(library) for library in external}
    missing = missing_libraries(external, owners, allowed)
    report = {
        "depends": depends,
        "system_libraries": {library: sorted(owners[library]) for library in external},
        "missing": missing,
    }
    if args.report:
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Depends: {depends}")
    if missing:
        print(f"{len(missing)} system libraries do not come with the Depends:", file=sys.stderr)
        for library, detail in missing.items():
            owner = ", ".join(detail["owners"]) or "no installed package"
            print(f"  {library} ({owner}), needed by {detail['used_by'][0]}", file=sys.stderr)
        return 1
    print(f"{len(external)} system libraries, each from a package that comes with the Depends")
    return 0


if __name__ == "__main__":
    sys.exit(main())
