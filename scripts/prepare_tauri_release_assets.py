#!/usr/bin/env python3
"""Validate and compact the three user-facing Tauri installers for publication."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path, PurePath

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def locate_exact(root: Path, filename: str) -> Path:
    if PurePath(filename).name != filename:
        raise ValueError(f"unsafe release filename: {filename!r}")
    matches = [path for path in root.rglob(filename) if path.is_file()]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one {filename}, found {len(matches)}")
    return matches[0]


def load_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def prepare(
    root: Path,
    manifest_path: Path,
    tag: str,
    output: Path,
    readiness_path: Path | None = None,
) -> Path:
    manifest = load_json(manifest_path)
    if manifest.get("schema_version") != 1:
        raise ValueError("unsupported Tauri release manifest schema")
    version = str(manifest.get("version", ""))
    if tag != f"v{version}":
        raise ValueError(f"tag {tag!r} does not match manifest version {version!r}")
    readiness = load_json(readiness_path) if readiness_path else None
    if readiness and readiness.get("release") != version:
        raise ValueError("beta-readiness version does not match the release manifest")
    entries = manifest.get("artifacts")
    if not isinstance(entries, list) or len(entries) != 3:
        raise ValueError("the compact release must contain exactly three installers")

    output.mkdir(parents=True, exist_ok=True)
    bundles: list[dict[str, object]] = []
    digests: set[str] = set()
    for raw_entry in entries:
        if not isinstance(raw_entry, dict):
            raise ValueError("release manifest artifact entries must be objects")
        entry = dict(raw_entry)
        filename = str(entry["filename"])
        source = locate_exact(root, filename)
        digest = sha256(source)
        if not SHA256_RE.fullmatch(digest):
            raise ValueError(f"invalid digest for {filename}")
        if digest in digests:
            raise ValueError("release installers must have distinct SHA-256 digests")
        digests.add(digest)

        checksum_path = source.with_name(source.name + ".sha256")
        metadata_path = source.with_name(source.name + ".metadata.json")
        architecture_path = source.with_name(source.name + ".architecture.json")
        expected_line = f"{digest}  {filename}"
        if checksum_path.read_text(encoding="utf-8-sig").strip() != expected_line:
            raise ValueError(f"checksum sidecar mismatch for {filename}")
        metadata = load_json(metadata_path)
        architecture = load_json(architecture_path)
        expected = {
            "artifact_filename": filename,
            "sha256": digest,
            "platform": entry["platform"],
            "architecture": entry["architecture"],
            "backend": entry["backend"],
        }
        for key, value in expected.items():
            if metadata.get(key) != value:
                raise ValueError(f"{filename} metadata {key!r} does not match manifest")
        smoke = metadata.get("package_smoke")
        if not isinstance(smoke, dict) or smoke.get("passed") is not True:
            raise ValueError(f"{filename} has no passing installed-package smoke evidence")
        if architecture.get("result") != "PASS" or not architecture.get("native_binary_count"):
            raise ValueError(f"{filename} has no passing native architecture evidence")
        signing = metadata.get("signing")
        expected_signing = entry.get("signing")
        if not isinstance(signing, dict) or signing.get("status") != expected_signing:
            raise ValueError(f"{filename} signing evidence does not match {expected_signing!r}")
        if entry["platform"] == "macos" and not all(
            signing.get(field)
            for field in ("developer_id", "team_id", "notarized", "stapled", "gatekeeper_accepted")
        ):
            raise ValueError(f"{filename} has incomplete Developer ID/notarization evidence")
        if entry["platform"] == "windows" and not all(
            signing.get(field) for field in ("signer_subject", "signer_thumbprint", "timestamped")
        ):
            raise ValueError(f"{filename} has incomplete Authenticode evidence")

        shutil.copy2(source, output / filename)
        bundles.append(
            entry
            | {
                "sha256": digest,
                "size": source.stat().st_size,
                "signing_evidence": signing,
                "architecture_evidence": architecture,
                "smoke_evidence": smoke,
            }
        )

    checksums = "".join(f"{item['sha256']}  {item['filename']}\n" for item in bundles)
    (output / "SHA256SUMS").write_text(checksums, encoding="utf-8")
    index = {
        "schema_version": 1,
        "release_tag": tag,
        "version": version,
        "generated_at": datetime.now(UTC).isoformat(),
        "channel": "alpha",
        "beta_ready": False,
        "installers": bundles,
    }
    if readiness:
        index["beta_readiness"] = readiness
    index_path = output / "release-index.json"
    index_path.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    files = [str(output / str(item["filename"])) for item in bundles]
    files.extend([str(output / "SHA256SUMS"), str(index_path)])
    (output / "release-files.txt").write_text("\n".join(files) + "\n", encoding="utf-8")
    return index_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--readiness", type=Path, required=True)
    args = parser.parse_args()
    path = prepare(
        args.root.resolve(),
        args.manifest.resolve(),
        args.tag,
        args.output.resolve(),
        args.readiness.resolve(),
    )
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
