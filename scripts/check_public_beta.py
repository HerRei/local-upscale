#!/usr/bin/env python3
"""Validate the beta release plan and its version metadata."""

from __future__ import annotations

import argparse
import base64
import json
import re
import tomllib
from pathlib import Path

from check_beta_readiness import validate as validate_readiness

ROOT = Path(__file__).resolve().parents[1]


def check(tag: str | None = None, root: Path = ROOT) -> str:
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+-beta(\.\d+)?", version):
        raise ValueError("The public beta check requires a beta version")
    if tag is not None and tag != f"v{version}":
        raise ValueError("Beta tag does not match the application version")
    versions = [
        json.loads((root / "desktop/package.json").read_text())["version"],
        json.loads((root / "desktop/package-lock.json").read_text())["version"],
        json.loads((root / "desktop/package-lock.json").read_text())["packages"][""]["version"],
        json.loads((root / "desktop/src-tauri/tauri.conf.json").read_text())["version"],
        tomllib.loads((root / "desktop/src-tauri/Cargo.toml").read_text())["package"]["version"],
    ]
    cargo_lock = tomllib.loads((root / "desktop/src-tauri/Cargo.lock").read_text())
    versions += [
        package["version"] for package in cargo_lock["package"] if package["name"] == "localsr-next"
    ]
    package_init = (root / "src/localsr/__init__.py").read_text()
    match = re.search(r'^__version__ = "([^"]+)"$', package_init, re.MULTILINE)
    versions.append(match.group(1) if match else "missing")
    if set(versions) != {version}:
        raise ValueError("Python, npm/lock, Cargo/lock and Tauri versions disagree")
    plan = json.loads((root / "ci/public-beta-release.json").read_text())
    if (
        plan.get("schema_version") != 1
        or plan.get("version") != version
        or plan.get("channel") != "beta"
    ):
        raise ValueError("Invalid beta release plan identity")
    expected = json.loads((root / "ci/tauri-targets.json").read_text())["targets"]
    fields = ("id", "platform", "architecture", "backend")
    actual = plan.get("targets", [])
    if [{k: t[k] for k in fields} for t in actual] != [{k: t[k] for k in fields} for t in expected]:
        raise ValueError(
            "The beta plan must list every registered backend target in registry order"
        )
    if any(t.get("coverage") not in {"main-path", "labs"} for t in actual):
        raise ValueError("Every beta backend needs an explicit coverage label")
    validate_readiness(root / plan["readiness"], root)
    public = base64.b64decode((root / plan["update_public_key"]).read_text().strip(), validate=True)
    if not public.startswith(b"untrusted comment: minisign public key"):
        raise ValueError("The beta public update key has an unexpected format")
    for path, token in (
        ("README.md", f"v{version}"),
        ("CHANGELOG.md", f"## [{version}]"),
        ("docs/releasing.md", f"v{version}"),
    ):
        if token not in (root / path).read_text():
            raise ValueError(f"{path} does not identify the beta candidate")
    if not (root / f"docs/releases/v{version}.md").is_file():
        raise ValueError("Beta release notes are missing")
    return version


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag")
    args = parser.parse_args()
    print(f"Beta metadata synchronized: v{check(args.tag)}")
