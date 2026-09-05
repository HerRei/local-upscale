#!/usr/bin/env python3
"""Stream a frozen Windows engine into bounded, checksummed installer payloads."""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import tarfile
from pathlib import Path

PART_BYTES = 1900 * 1024**2


class PartWriter:
    def __init__(self, directory: Path, prefix: str, limit: int):
        self.directory, self.prefix, self.limit = directory, prefix, limit
        self.parts: list[dict] = []
        self.stream = None
        self.digest = hashlib.sha256()
        self.size = 0

    def write(self, data: bytes) -> int:
        total = len(data)
        view = memoryview(data)
        while view:
            if self.stream is None:
                name = f"{self.prefix}.part-{len(self.parts) + 1:04d}"
                self.stream = (self.directory / name).open("xb")
            count = min(len(view), self.limit - self.size)
            self.stream.write(view[:count])
            self.digest.update(view[:count])
            self.size += count
            view = view[count:]
            if self.size == self.limit:
                self.close_part()
        return total

    def flush(self) -> None:
        if self.stream:
            self.stream.flush()

    def close_part(self) -> None:
        if self.stream:
            name = Path(self.stream.name).name
            self.stream.close()
            self.parts.append(
                {"filename": name, "size": self.size, "sha256": self.digest.hexdigest()}
            )
            self.stream = None
            self.digest = hashlib.sha256()
            self.size = 0


def prepare(engine: Path, output: Path, prefix: str, *, part_bytes: int = PART_BYTES) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", prefix):
        raise ValueError("invalid engine payload prefix")
    if not 1 <= part_bytes < 2 * 1024**3:
        raise ValueError("payload parts must be smaller than 2 GiB")
    if not (engine / "localsr-worker.exe").is_file():
        raise ValueError("the frozen Windows worker is missing")
    output.mkdir(parents=True, exist_ok=True)
    paths = sorted(engine.rglob("*"))
    if any(path.is_symlink() for path in paths):
        raise ValueError("engine payload contains a link")
    files = [path for path in paths if not path.is_dir()]
    if any(path.is_symlink() or not path.is_file() for path in files):
        raise ValueError("engine payload contains a link or non-regular file")
    writer = PartWriter(output, prefix, part_bytes)
    try:
        with gzip.GzipFile(fileobj=writer, mode="wb", compresslevel=1, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w|", format=tarfile.PAX_FORMAT) as archive:
                for path in files:
                    info = archive.gettarinfo(
                        str(path), arcname=path.relative_to(engine).as_posix()
                    )
                    info.uid = info.gid = info.mtime = 0
                    info.uname = info.gname = ""
                    with path.open("rb") as source:
                        archive.addfile(info, source)
    finally:
        writer.close_part()
    manifest = {
        "schema_version": 1,
        "format": "tar.gz.parts",
        "backend": "CUDA",
        "file_count": len(files),
        "unpacked_bytes": sum(p.stat().st_size for p in files),
        "parts": writer.parts,
    }
    path = output / "engine-payload.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
