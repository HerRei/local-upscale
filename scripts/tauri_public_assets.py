"""Prepare bounded public downloads while retaining each logical installer."""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import shutil
from pathlib import Path

MIB = 1024**2
PART_BYTES = 1900 * MIB
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


def stage_file(source: Path, output: Path, expected: dict) -> dict:
    validate_asset_name(source.name)
    if asset_record(source) != expected:
        raise ValueError(f"release payload digest or size mismatch: {source.name}")
    destination = output / source.name
    if destination.exists() and asset_record(destination) != expected:
        raise ValueError(f"refusing conflicting prepared asset: {source.name}")
    if not destination.exists():
        shutil.copy2(source, destination)
    return expected


def prepare_installer(source: Path, output: Path, *, part_bytes: int = PART_BYTES) -> list[dict]:
    record = asset_record(source)
    if source.stat().st_size <= part_bytes:
        return [stage_file(source, output, record)]
    if source.suffix.lower() != ".appimage":
        raise ValueError(
            f"{source.name} exceeds the public asset budget; external payloads are required"
        )
    parts = split_file(source, output, part_bytes)
    filename = shlex.quote(source.name)
    checksums = "".join(f"{part['sha256']}  {part['filename']}\n" for part in parts)
    names = " ".join(shlex.quote(part["filename"]) for part in parts)
    script = output / f"{source.name}.restore.sh"
    script.write_text(
        '#!/usr/bin/env bash\nset -euo pipefail\ncd -- "$(dirname -- "$0")"\n'
        + f"name={filename}\nexpected={record['sha256']}\n"
        + 'if [ -f "$name" ]; then\n'
        + '  printf "%s  %s\\n" "$expected" "$name" | sha256sum --check\n'
        + '  chmod +x "$name"\n  exit 0\nfi\n'
        + "sha256sum --check <<'LOCALSR_HASHES'\n"
        + checksums
        + "LOCALSR_HASHES\n"
        + 'temporary="${name}.assembling.$$"\ntrap \'rm -f -- "$temporary"\' EXIT\n'
        + f'cat -- {names} > "$temporary"\n'
        + 'printf "%s  %s\\n" "$expected" "$temporary" | sha256sum --check\n'
        + 'chmod +x "$temporary"\nmv -- "$temporary" "$name"\n'
        + 'printf "Ready to launch: %s/%s\\n" "$PWD" "$name"\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    return parts + [asset_record(script)]


def prepare_engine_parts(metadata: dict, smoke: dict, source: Path, output: Path) -> list[dict]:
    payload = metadata.get("engine_payload")
    if not isinstance(payload, dict) or payload != smoke.get("engine_payload"):
        raise ValueError("installed engine payload manifest does not match build evidence")
    if metadata.get("engine_payload_sha256") != smoke.get("engine_payload_sha256"):
        raise ValueError("installed engine payload manifest digest mismatch")
    if (
        payload.get("schema_version") != 1
        or payload.get("format") != "tar.gz.parts"
        or payload.get("backend") != "CUDA"
        or not payload.get("file_count")
        or not payload.get("unpacked_bytes")
        or not payload.get("parts")
    ):
        raise ValueError("invalid engine payload manifest")
    records = []
    for part in payload["parts"]:
        validate_asset_name(part["filename"])
        if not 0 < part["size"] < 2 * 1024**3:
            raise ValueError("engine payload part exceeds the asset limit")
        records.append(stage_file(source.parent / part["filename"], output, part))
    if len({part["filename"] for part in records}) != len(records):
        raise ValueError("duplicate engine payload parts")
    return records
