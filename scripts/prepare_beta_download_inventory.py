#!/usr/bin/env python3
"""Verify retained review artifacts and describe all targets without download links."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def prepare(artifact_root: Path, output: Path) -> dict:
    plan = json.loads((ROOT / "ci/public-beta-release.json").read_text())
    artifact_root = artifact_root.resolve()
    inventory = {
        "schema_version": 1,
        "version": plan["version"],
        "published": False,
        "ready": False,
        "rollout": ["website-github", "microsoft-store"],
        "contact": "hermes.reisner@gmail.com",
        "issue_tracker_url": None,
        "issue_tracker_status": "GitHub Issues agreed; public repository URL not yet confirmed",
        "scope": "Retained older review artifacts. Every final candidate awaits an authorized build and acceptance.",
        "targets": [],
    }
    sums = []
    for target in plan["targets"]:
        artifacts = []
        for recorded in target["review_artifacts"]:
            source = artifact_root / recorded["path"]
            if not source.resolve().is_relative_to(artifact_root) or source.is_symlink():
                raise ValueError("Artifact must be an ordinary file inside the review root")
            with source.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            size = source.stat().st_size
            if size != recorded["size"] or digest != recorded["sha256"]:
                raise ValueError(f"Retained artifact changed: {recorded['path']}")
            artifacts.append(
                {
                    "filename": source.name,
                    "size": size,
                    "sha256": digest,
                    "status": "older-review-artifact-rebuild-required",
                    "github_single_asset_supported": size < 2 * 1024**3,
                    "download_url": None,
                }
            )
            sums.append(f"{digest}  {recorded['path']}\n")
        inventory["targets"].append(
            {
                "id": target["id"],
                "platform": target["platform"],
                "architecture": target["architecture"],
                "backend": target["backend"],
                "coverage": target["coverage"],
                "final_package_ready": False,
                "final_artifact": None,
                "status": "awaiting-final-build" if artifacts else "not-built",
                "delivery": "direct-download; Microsoft Store follows website/GitHub beta"
                if target["id"] == "windows-x86_64-directml"
                else "direct-download",
                "download_url": None,
                "review_artifacts": artifacts,
            }
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(inventory, indent=2) + "\n")
    output.with_name("REVIEW-SHA256SUMS").write_text("".join(sums))
    return inventory


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = prepare(args.artifact_root, args.output)
    print(
        f"Verified {sum(len(t['review_artifacts']) for t in result['targets'])} retained artifacts; all {len(result['targets'])} final targets remain unavailable."
    )
