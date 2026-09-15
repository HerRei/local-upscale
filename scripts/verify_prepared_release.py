#!/usr/bin/env python3
"""Check the complete target matrix and exact set of bounded public downloads."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from publish_tauri_release import expected_assets
from release_targets import validate_manifest


def verify(output: Path, manifest_path: Path, commit: str) -> None:
    manifest = json.loads(manifest_path.read_text())
    validate_manifest(manifest)
    index = json.loads((output / "release-index.json").read_text())
    wanted = {target["id"] for target in manifest["artifacts"]}
    if (
        index["repository_commit"] != commit
        or index["version"] != manifest["version"]
        or {target["id"] for target in index["installers"]} != wanted
        or set(index["source_matrix"]) != wanted
    ):
        raise ValueError("prepared release is missing targets or has a different identity")
    assets = expected_assets(output)
    # Preparation also keeps private build manifests, so validate the checksum
    # set directly rather than treating those private files as public downloads.
    lines = (output / "SHA256SUMS").read_text().splitlines()
    checksums = {line.split("  ", 1)[1]: line.split("  ", 1)[0] for line in lines}
    if checksums != {
        name: digest
        for name, (_path, digest) in assets.items()
        if name not in {"SHA256SUMS", "release-index.json"}
    }:
        raise ValueError("prepared checksums do not cover exactly the public payloads")
    print(f"Verified {len(wanted)} backend distributions and {len(assets)} public files")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()
    verify(args.output, args.manifest, args.commit)
