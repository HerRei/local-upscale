#!/usr/bin/env python3
"""Check the complete target matrix and exact set of bounded public downloads."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePath

from release_targets import validate_manifest

MAX_PUBLIC_ASSET_BYTES = 2 * 1024**3


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def expected_assets(output: Path) -> dict[str, tuple[Path, str]]:
    listing = output / "release-files.txt"
    paths = [Path(line) for line in listing.read_text(encoding="utf-8").splitlines() if line]
    index = json.loads((output / "release-index.json").read_text())
    public = index.get("public_assets")
    if not isinstance(public, list) or not public:
        raise ValueError("release index has no verified public assets")
    wanted = {item["filename"] for item in public} | {"SHA256SUMS", "release-index.json"}
    if len(paths) != len(wanted) or {path.name for path in paths} != wanted:
        raise ValueError("release file list does not match the verified release index")
    result: dict[str, tuple[Path, str]] = {}
    for path in paths:
        resolved = path.resolve()
        if not resolved.is_relative_to(output.resolve()) or PurePath(path.name).name != path.name:
            raise ValueError(f"release file escaped prepared output: {path}")
        if not path.is_file() or path.name in result:
            raise ValueError(f"release file is missing or duplicated: {path.name}")
        if path.stat().st_size >= MAX_PUBLIC_ASSET_BYTES:
            raise ValueError(f"release asset exceeds GitHub's size limit: {path.name}")
        result[path.name] = (path, sha256(path))
    if set(result) & {"release-files.txt", "matrix-selection.json"}:
        raise ValueError("private preparation manifests must not be public assets")
    if not {"SHA256SUMS", "release-index.json"}.issubset(result):
        raise ValueError("SHA256SUMS and release-index.json are required public assets")
    for item in public:
        path, digest = result[item["filename"]]
        if digest != item["sha256"] or path.stat().st_size != item["size"]:
            raise ValueError(f"public asset differs from verified index: {path.name}")
    return result


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
