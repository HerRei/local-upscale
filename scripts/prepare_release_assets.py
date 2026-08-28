#!/usr/bin/env python3
"""Prepare GitHub Release assets, splitting files above GitHub's 2 GiB limit."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path

MIB = 1024**2


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * MIB), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hardlink_or_copy(source: Path, destination: Path) -> None:
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def split_file(source: Path, destination: Path, part_bytes: int) -> list[dict[str, object]]:
    parts: list[dict[str, object]] = []
    with source.open("rb") as stream:
        index = 1
        while True:
            first = stream.read(min(4 * MIB, part_bytes))
            if not first:
                break
            output = destination / f"{source.name}.part-{index:04d}"
            digest = hashlib.sha256()
            written = 0
            with output.open("wb") as part:
                chunk = first
                while chunk:
                    part.write(chunk)
                    digest.update(chunk)
                    written += len(chunk)
                    if written >= part_bytes:
                        break
                    chunk = stream.read(min(4 * MIB, part_bytes - written))
                part.flush()
                os.fsync(part.fileno())
            parts.append({"filename": output.name, "size": written, "sha256": digest.hexdigest()})
            index += 1
    return parts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact_root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--readiness", type=Path)
    parser.add_argument("--part-mib", type=int, default=1900)
    args = parser.parse_args()
    root = args.artifact_root.resolve()
    output = args.output.resolve()
    if not (root / ".complete").is_file():
        raise ValueError(f"Artifact root is not architecture/checksum complete: {root}")
    if not output.is_relative_to(root) or output == root:
        raise ValueError(f"Release staging must be a child of artifact root: {output}")
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"Release staging is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    specs = json.loads(args.manifest.read_text(encoding="utf-8"))["artifacts"]
    index: dict[str, object] = {"schema_version": 1, "assets": [], "bundles": []}
    assets: list[dict[str, object]] = index["assets"]  # type: ignore[assignment]
    bundles: list[dict[str, object]] = index["bundles"]  # type: ignore[assignment]
    part_bytes = args.part_mib * MIB

    for spec in specs:
        matches = list(root.rglob(spec["filename"]))
        if len(matches) != 1:
            raise ValueError(f"Expected one {spec['filename']}; found {len(matches)}")
        artifact = matches[0]
        if artifact.stat().st_size <= part_bytes:
            destination = output / artifact.name
            hardlink_or_copy(artifact, destination)
            record = {"filename": destination.name, "size": destination.stat().st_size}
            assets.append(record)
            bundles.append(
                {
                    "filename": artifact.name,
                    "sha256": sha256(artifact),
                    "assets": [destination.name],
                }
            )
        else:
            parts = split_file(artifact, output, part_bytes)
            assets.extend({"filename": part["filename"], "size": part["size"]} for part in parts)
            parts_manifest = output / f"{artifact.name}.parts.json"
            parts_manifest.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "filename": artifact.name,
                        "size": artifact.stat().st_size,
                        "sha256": sha256(artifact),
                        "join_command": f"cat {artifact.name}.part-* > {artifact.name}",
                        "parts": parts,
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            assets.append({"filename": parts_manifest.name, "size": parts_manifest.stat().st_size})
            bundles.append(
                {
                    "filename": artifact.name,
                    "sha256": sha256(artifact),
                    "assets": [part["filename"] for part in parts] + [parts_manifest.name],
                }
            )
        for suffix in (".sha256", ".metadata.json", ".architecture.txt"):
            source = artifact.with_name(artifact.name + suffix)
            if not source.is_file():
                raise ValueError(f"Missing release sidecar: {source}")
            destination = output / source.name
            hardlink_or_copy(source, destination)
            assets.append({"filename": destination.name, "size": destination.stat().st_size})

    if args.readiness:
        readiness = json.loads(args.readiness.read_text(encoding="utf-8"))
        readiness_path = output / "beta-readiness.json"
        readiness_path.write_text(
            json.dumps(readiness, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        assets.append({"filename": readiness_path.name, "size": readiness_path.stat().st_size})
        index["beta_readiness"] = {
            "filename": readiness_path.name,
            "beta_ready": readiness.get("beta_ready"),
        }

    index_path = output / "release-index.json"
    # The index includes itself by name. Its exact size is intentionally null to
    # avoid recursive size metadata changing the file's own size.
    assets.append({"filename": index_path.name, "size": None})
    index_path.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output / "release-files.txt").write_text(
        "".join(str(output / asset["filename"]) + "\n" for asset in assets), encoding="utf-8"
    )
    print(index_path)


if __name__ == "__main__":
    main()
