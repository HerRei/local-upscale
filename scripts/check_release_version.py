#!/usr/bin/env python3
"""Fail when release-facing version metadata disagrees."""

from __future__ import annotations

import argparse
import json
import re
import tomllib
from pathlib import Path

from check_public_beta import check as check_public_beta
from release_targets import validate_manifest

ROOT = Path(__file__).resolve().parents[1]


def check(tag: str | None = None, root: Path = ROOT) -> str:
    with (root / "pyproject.toml").open("rb") as stream:
        version = str(tomllib.load(stream)["project"]["version"])
    if re.fullmatch(r"\d+\.\d+\.\d+-beta(\.\d+)?", version):
        return check_public_beta(tag, root)
    expected_tag = f"v{version}"
    failures: list[str] = []

    if tag and tag != expected_tag:
        failures.append(f"tag {tag!r} does not match {expected_tag!r}")

    package_init = (root / "src/localsr/__init__.py").read_text(encoding="utf-8")
    package_match = re.search(r'^__version__ = "([^"]+)"$', package_init, re.MULTILINE)
    if not package_match or package_match.group(1) != version:
        failures.append("localsr.__version__ does not match pyproject.toml")

    readme = (root / "README.md").read_text(encoding="utf-8")
    if expected_tag not in readme:
        failures.append(f"README.md does not identify {expected_tag}")

    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    if f"## [{version}]" not in changelog:
        failures.append(f"CHANGELOG.md has no [{version}] release heading")

    desktop_package = json.loads((root / "desktop/package.json").read_text(encoding="utf-8"))
    tauri = json.loads((root / "desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
    with (root / "desktop/src-tauri/Cargo.toml").open("rb") as stream:
        cargo_version = str(tomllib.load(stream)["package"]["version"])
    if {str(desktop_package.get("version")), str(tauri.get("version")), cargo_version} != {version}:
        failures.append("npm, Tauri, Cargo, and Python release versions do not match")

    for relative, label in (("ci/tauri-release-artifacts.json", "signed Tauri"),):
        path = root / relative
        if not path.is_file():
            failures.append(f"{label} manifest is missing: {relative}")
            continue
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest.get("version") != version:
            failures.append(f"{label} artifact manifest version does not match")
        try:
            validate_manifest(manifest)
        except ValueError as error:
            failures.append(f"{label}: {error}")

    for relative, label in ((".github/workflows/desktop-release.yml", "signed release workflow"),):
        path = root / relative
        if not path.is_file():
            failures.append(f"{label} is missing: {relative}")
            continue
        workflow = path.read_text(encoding="utf-8")
        if "publish_tauri_release.py" not in workflow and "--prerelease" not in workflow:
            failures.append(f"{label} has no prerelease-only publisher")
        if 'TITLE="LocalSR $TAG"' not in workflow:
            failures.append(f"{label} title is not derived from the tag")
        for token in ("release_targets.py", "matrix.pkg_name", "verify_prepared_release.py"):
            if token not in workflow:
                failures.append(f"{label} does not use the complete generated matrix: {token}")

    docs = (root / "docs/releasing.md").read_text(encoding="utf-8")
    if expected_tag not in docs:
        failures.append(f"release documentation does not identify {expected_tag}")
    if not (root / "docs/releases" / f"{expected_tag}.md").is_file():
        failures.append(f"release notes are missing for {expected_tag}")

    if failures:
        raise ValueError("Release version mismatch:\n- " + "\n- ".join(failures))
    return version


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag")
    args = parser.parse_args()
    version = check(args.tag)
    print(f"Release metadata synchronized: v{version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
