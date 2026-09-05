#!/usr/bin/env python3
"""Generate and validate the complete desktop release matrix from one registry."""

from __future__ import annotations

import argparse
import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "ci" / "tauri-targets.json"


def targets() -> list[dict]:
    data = json.loads(REGISTRY.read_text())
    entries = data["targets"]
    if data.get("schema_version") != 1 or len({x["id"] for x in entries}) != len(entries):
        raise ValueError("invalid or duplicate release target IDs")
    return entries


def version(root: Path = ROOT) -> str:
    with (root / "pyproject.toml").open("rb") as stream:
        return tomllib.load(stream)["project"]["version"]


def artifact_entries(release_version: str, *, alpha: bool) -> list[dict]:
    return [
        {
            "id": target["id"],
            "filename": f"LocalSR-v{release_version}-{target['suffix']}",
            "platform": target["platform"],
            "architecture": target["architecture"],
            "backend": target["backend"],
            "signing": target["alpha_signing" if alpha else "signing"],
            **({"external_engine": True} if target.get("external_engine") else {}),
        }
        for target in targets()
    ]


def validate_manifest(manifest: dict) -> None:
    entries = manifest.get("artifacts")
    if not isinstance(entries, list) or not entries:
        raise ValueError("release manifest has no targets")
    expected = artifact_entries(str(manifest["version"]), alpha="release_policy" in manifest)
    if entries != expected:
        raise ValueError("release manifest must match every target in ci/tauri-targets.json")


def matrix(platform: str, release_version: str) -> dict:
    return {
        "include": [
            target | {"pkg_name": f"LocalSR-v{release_version}-{target['suffix']}"}
            for target in targets()
            if target["platform"] == platform
        ]
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-manifests", action="store_true")
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--platform", choices=("windows", "linux", "macos"))
    args = parser.parse_args()
    current = version()
    if args.write_manifests:
        for alpha, filename in (
            (False, "tauri-release-artifacts.json"),
            (
                True,
                f"v{current}-artifacts.json".replace("-alpha-artifacts", "-cross-alpha-artifacts"),
            ),
        ):
            data = {
                "schema_version": 1,
                "version": current,
                **(
                    {"release_policy": f"v{current.split('-')[0]}-cross-alpha-exception"}
                    if alpha
                    else {}
                ),
                "artifacts": artifact_entries(current, alpha=alpha),
            }
            (ROOT / "ci" / filename).write_text(json.dumps(data, indent=2) + "\n")
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as stream:
            for platform in ("linux", "windows"):
                stream.write(f"{platform}={json.dumps(matrix(platform, current))}\n")
            stream.write(f"version={current}\n")
    if args.platform:
        print(json.dumps(matrix(args.platform, current)))


if __name__ == "__main__":
    main()
