"""Prepare bounded public downloads while retaining each logical installer."""

from __future__ import annotations

import shlex
import shutil
from pathlib import Path

from prepare_release_assets import asset_record, split_file, validate_asset_name

PART_BYTES = 1900 * 1024**2


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
