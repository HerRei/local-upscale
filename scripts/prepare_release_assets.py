#!/usr/bin/env python3
"""Prepare a compact, installer-friendly GitHub Release asset set."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
from pathlib import Path

MIB = 1024**2
INSTALLER_TAG_PLACEHOLDER = "@LOCALSR_RELEASE_TAG@"
SAFE_ASSET_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * MIB), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_asset_name(name: str) -> None:
    if not SAFE_ASSET_NAME.fullmatch(name):
        raise ValueError(f"Unsafe release asset name: {name!r}")


def hardlink_or_copy(source: Path, destination: Path) -> None:
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def asset_record(path: Path) -> dict[str, object]:
    validate_asset_name(path.name)
    return {"filename": path.name, "size": path.stat().st_size, "sha256": sha256(path)}


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
            record = {"filename": output.name, "size": written, "sha256": digest.hexdigest()}
            validate_asset_name(output.name)
            parts.append(record)
            index += 1
    return parts


def read_sidecars(artifact: Path, digest: str) -> tuple[dict[str, object], str]:
    checksum_path = artifact.with_name(artifact.name + ".sha256")
    metadata_path = artifact.with_name(artifact.name + ".metadata.json")
    architecture_path = artifact.with_name(artifact.name + ".architecture.txt")
    for path in (checksum_path, metadata_path, architecture_path):
        if not path.is_file():
            raise ValueError(f"Missing release sidecar: {path}")

    checksum_fields = checksum_path.read_text(encoding="utf-8").strip().split()
    if len(checksum_fields) != 2 or checksum_fields != [digest, artifact.name]:
        raise ValueError(f"Invalid checksum sidecar for {artifact.name}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if not isinstance(metadata, dict):
        raise ValueError(f"Invalid metadata sidecar for {artifact.name}")
    if metadata.get("artifact_filename") != artifact.name or metadata.get("sha256") != digest:
        raise ValueError(f"Invalid metadata sidecar for {artifact.name}")
    architecture_report = architecture_path.read_text(encoding="utf-8")
    if not architecture_report.strip():
        raise ValueError(f"Empty architecture report for {artifact.name}")
    return metadata, architecture_report


def stage_installer(source: Path, output: Path, output_name: str, tag: str) -> dict[str, object]:
    validate_asset_name(output_name)
    contents = source.read_text(encoding="utf-8")
    if contents.count(INSTALLER_TAG_PLACEHOLDER) != 1:
        raise ValueError(
            f"Installer must contain exactly one {INSTALLER_TAG_PLACEHOLDER}: {source}"
        )
    destination = output / output_name
    destination.write_text(contents.replace(INSTALLER_TAG_PLACEHOLDER, tag), encoding="utf-8")
    destination.chmod(0o755 if destination.suffix == ".sh" else 0o644)
    return asset_record(destination)


def prepare(
    artifact_root: Path,
    *,
    output: Path,
    manifest: Path,
    readiness: Path | None,
    tag: str,
    shell_installer: Path,
    powershell_installer: Path,
    part_mib: int = 1900,
) -> Path:
    root = artifact_root.resolve()
    output = output.resolve()
    if not (root / ".complete").is_file():
        raise ValueError(f"Artifact root is not architecture/checksum complete: {root}")
    if not output.is_relative_to(root) or output == root:
        raise ValueError(f"Release staging must be a child of artifact root: {output}")
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"Release staging is not empty: {output}")
    if not tag.startswith("v") or not SAFE_ASSET_NAME.fullmatch(tag):
        raise ValueError(f"Invalid release tag: {tag!r}")
    if part_mib <= 0:
        raise ValueError("--part-mib must be positive")

    output.mkdir(parents=True, exist_ok=True)
    specs = json.loads(manifest.read_text(encoding="utf-8"))["artifacts"]
    payload_assets: list[dict[str, object]] = []
    bundles: list[dict[str, object]] = []
    part_bytes = part_mib * MIB

    for spec in specs:
        artifact = root / spec["platform"] / spec["filename"]
        if not artifact.is_file():
            raise ValueError(f"Missing release artifact: {artifact}")
        digest = sha256(artifact)
        metadata, architecture_report = read_sidecars(artifact, digest)
        for field in ("platform", "architecture", "backend"):
            if metadata.get(field) != spec[field]:
                raise ValueError(
                    f"Metadata {field} mismatch for {artifact.name}: "
                    f"{metadata.get(field)!r} != {spec[field]!r}"
                )

        if artifact.stat().st_size <= part_bytes:
            destination = output / artifact.name
            hardlink_or_copy(artifact, destination)
            stored_assets = [asset_record(destination)]
        else:
            stored_assets = split_file(artifact, output, part_bytes)
        payload_assets.extend(stored_assets)
        bundles.append(
            {
                "filename": artifact.name,
                "size": artifact.stat().st_size,
                "sha256": digest,
                "platform": spec["platform"],
                "architecture": spec["architecture"],
                "backend": spec["backend"],
                "main": spec["main"],
                "require_mps": bool(spec.get("require_mps", False)),
                "assets": [record["filename"] for record in stored_assets],
                "metadata": metadata,
                "architecture_report": architecture_report,
            }
        )

    installers = [
        {
            "platforms": ["macos", "linux"],
            **stage_installer(shell_installer, output, "Install-LocalSR.sh", tag),
        },
        {
            "platforms": ["windows"],
            **stage_installer(powershell_installer, output, "Install-LocalSR.ps1", tag),
        },
    ]
    payload_assets.extend(
        {key: value for key, value in installer.items() if key != "platforms"}
        for installer in installers
    )
    payload_names = [str(record["filename"]) for record in payload_assets]
    if len(payload_names) != len(set(payload_names)):
        raise ValueError("Release payload contains duplicate asset names")

    checksum_path = output / "SHA256SUMS"
    checksum_path.write_text(
        "".join(
            f"{record['sha256']}  {record['filename']}\n"
            for record in sorted(payload_assets, key=lambda item: str(item["filename"]))
        ),
        encoding="utf-8",
    )
    checksum_record = asset_record(checksum_path)

    index: dict[str, object] = {
        "schema_version": 2,
        "release_tag": tag,
        "checksum_file": checksum_path.name,
        "installers": installers,
        "bundles": bundles,
    }
    if readiness:
        index["beta_readiness"] = json.loads(readiness.read_text(encoding="utf-8"))

    index_path = output / "release-index.json"
    published_assets = payload_assets + [checksum_record]
    # The index lists itself without recursive size/digest metadata.
    index["assets"] = published_assets + [
        {"filename": index_path.name, "size": None, "sha256": None}
    ]
    index_path.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    installer_names = {"Install-LocalSR.sh", "Install-LocalSR.ps1"}
    upload_order = [
        output / "Install-LocalSR.sh",
        output / "Install-LocalSR.ps1",
        checksum_path,
        index_path,
        *(
            output / str(record["filename"])
            for record in payload_assets
            if record["filename"] not in installer_names
        ),
    ]
    (output / "release-files.txt").write_text(
        "".join(str(path) + "\n" for path in upload_order), encoding="utf-8"
    )
    return index_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact_root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--readiness", type=Path)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--shell-installer", type=Path, required=True)
    parser.add_argument("--powershell-installer", type=Path, required=True)
    parser.add_argument("--part-mib", type=int, default=1900)
    args = parser.parse_args()
    print(
        prepare(
            args.artifact_root,
            output=args.output,
            manifest=args.manifest,
            readiness=args.readiness,
            tag=args.tag,
            shell_installer=args.shell_installer,
            powershell_installer=args.powershell_installer,
            part_mib=args.part_mib,
        )
    )


if __name__ == "__main__":
    main()
