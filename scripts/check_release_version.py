#!/usr/bin/env python3
"""Fail when release-facing version metadata disagrees."""

from __future__ import annotations

import argparse
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check(tag: str | None = None, root: Path = ROOT) -> str:
    with (root / "pyproject.toml").open("rb") as stream:
        version = str(tomllib.load(stream)["project"]["version"])
    expected_tag = f"v{version}"
    failures: list[str] = []

    if tag and tag != expected_tag:
        failures.append(f"tag {tag!r} does not match {expected_tag!r}")

    iss = (root / "packaging/windows/LocalSR.iss").read_text(encoding="utf-8")
    match = re.search(r'^#define MyAppVersion "([^"]+)"$', iss, re.MULTILINE)
    if not match or match.group(1) != version:
        failures.append("Inno Setup MyAppVersion does not match pyproject.toml")

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

    spec = (root / "packaging/localsr.spec").read_text(encoding="utf-8")
    expected_bundle_fields = {
        "CFBundleShortVersionString": "APP_BUNDLE_VERSION",
        "CFBundleVersion": "APP_BUILD_NUMBER",
        "LocalSRReleaseVersion": "APP_VERSION",
    }
    for field, variable in expected_bundle_fields.items():
        if f'"{field}": {variable}' not in spec:
            failures.append(f"macOS bundle field {field} is not derived from {variable}")

    workflow = (root / ".github/workflows/release.yml").read_text(encoding="utf-8")
    if "--prerelease" not in workflow:
        failures.append("release workflow never passes --prerelease")
    if 'TITLE="LocalSR $TAG"' not in workflow:
        failures.append("release workflow title is not derived from the tag")

    docs = (root / "docs/releasing.md").read_text(encoding="utf-8")
    if expected_tag not in docs:
        failures.append(f"release documentation does not identify {expected_tag}")

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
