#!/usr/bin/env python3
"""Stream a frozen engine into bounded, checksummed payload parts.

Used for the adjacent Windows CUDA installer payloads and for the updater's
separate engine package on every platform. As a command it writes the updater's
engine package for one release target under its public names:

    prepare_engine_payload.py --target ID --output DIR (--engine DIR | --reuse MANIFEST)

--reuse adopts a payload that already exists in DIR (the CUDA installer's parts)
after checking every part against its manifest.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import tarfile
import zlib
from pathlib import Path, PurePosixPath

from release_targets import target, update_files, version

PART_BYTES = 1900 * 1024**2
# desktop/src-tauri/src/engine_payload.rs accepts the same backend names.
BACKENDS = {"CUDA", "ROCM", "CPU", "MPS", "DIRECTML"}


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


def prepare(
    engine: Path,
    output: Path,
    prefix: str,
    *,
    part_bytes: int = PART_BYTES,
    backend: str = "CUDA",
    worker: str = "localsr-worker.exe",
) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", prefix):
        raise ValueError("invalid engine payload prefix")
    if not 1 <= part_bytes < 2 * 1024**3:
        raise ValueError("payload parts must be smaller than 2 GiB")
    if backend not in BACKENDS:
        raise ValueError("unsupported engine payload backend")
    if not (engine / worker).is_file():
        raise ValueError("the frozen worker is missing")
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
        "backend": backend,
        "file_count": len(files),
        "unpacked_bytes": sum(p.stat().st_size for p in files),
        "parts": writer.parts,
    }
    path = output / "engine-payload.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


class _JoinedParts:
    """The parts read in order as one stream, each hashed on the way."""

    def __init__(self, paths: list[Path]):
        self.pending = list(paths)
        self.stream = None
        self.digest = hashlib.sha256()
        self.size = 0
        self.digests: list[tuple[str, int]] = []

    def read(self, count: int = -1) -> bytes:
        while True:
            if self.stream is None:
                if not self.pending:
                    return b""
                self.stream = self.pending.pop(0).open("rb")
                self.digest, self.size = hashlib.sha256(), 0
            data = self.stream.read(count if count and count > 0 else 4 * 1024**2)
            if data:
                self.digest.update(data)
                self.size += len(data)
                return data
            self.stream.close()
            self.stream = None
            self.digests.append((self.digest.hexdigest(), self.size))


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def validate_payload(
    manifest: object, directory: Path, prefix: str, backend: str, worker: str
) -> list[Path]:
    """Check an engine payload in ``directory`` the way the app's installer will.

    Every part must be a regular file named ``<prefix>.part-NNNN`` directly in the
    folder, of the listed size and digest; together they must be a gzip tar archive of
    exactly ``file_count`` regular files with safe relative paths, ``unpacked_bytes`` in
    all, and the worker at the top. Returns the part paths in order.
    """
    parts = manifest.get("parts") if isinstance(manifest, dict) else None
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != 1
        or manifest.get("format") != "tar.gz.parts"
        or manifest.get("backend") != backend
        or not _positive_int(manifest.get("file_count"))
        or not _positive_int(manifest.get("unpacked_bytes"))
        or not isinstance(parts, list)
        or not 1 <= len(parts) <= 64
    ):
        raise ValueError(f"invalid {backend} engine payload manifest")
    name_re = re.compile(re.escape(prefix) + r"\.part-\d{4}")
    paths = []
    for part in parts:
        name = part.get("filename") if isinstance(part, dict) else None
        if not isinstance(name, str) or not name_re.fullmatch(name):
            raise ValueError(f"engine payload part {name!r} is not a part of {prefix}")
        path = directory / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"engine payload part {name} is missing or a link")
        if not _positive_int(part.get("size")) or part["size"] >= 2 * 1024**3:
            raise ValueError(f"engine payload part {name} has an invalid size")
        if path.stat().st_size != part["size"]:
            raise ValueError(f"{name} does not match its engine payload manifest")
        paths.append(path)
    joined = _JoinedParts(paths)
    count = total = 0
    has_worker = False
    names: set[str] = set()
    try:
        with gzip.GzipFile(fileobj=joined, mode="rb") as decoded, tarfile.open(
            fileobj=decoded, mode="r|"
        ) as archive:
            for member in archive:
                relative = PurePosixPath(member.name)
                if (
                    not member.isfile()
                    or relative.is_absolute()
                    or not relative.parts
                    or ".." in relative.parts
                    or "\\" in member.name
                    or ":" in member.name
                    or str(relative).lower() in names
                ):
                    raise ValueError(f"engine payload holds an unsafe entry: {member.name!r}")
                names.add(str(relative).lower())
                count += 1
                total += member.size
                has_worker |= member.name == worker
            # tar stops at its end markers; finish the gzip decoder as the runtime
            # does, so a corrupt or missing trailer cannot be signed.
            while decoded.read(4 * 1024**2):
                pass
        while joined.read(4 * 1024**2):
            pass
    except (tarfile.TarError, EOFError, OSError, zlib.error) as error:
        raise ValueError(f"the {prefix} parts are not an engine archive: {error}") from error
    if joined.digests != [(part["sha256"], part["size"]) for part in parts]:
        raise ValueError(f"the {prefix} parts do not match their engine payload manifest")
    if count != manifest["file_count"] or total != manifest["unpacked_bytes"] or not has_worker:
        raise ValueError(f"the {prefix} archive does not match its engine payload manifest")
    return paths


def prepare_for_target(
    target_id: str,
    output: Path,
    *,
    engine: Path | None = None,
    reuse: Path | None = None,
    release_version: str | None = None,
) -> Path:
    """Write the updater's engine package of one edition into ``output``."""
    files = update_files(target(target_id), release_version or version())
    destination = output / files["engine_manifest"]
    if (engine is None) == (reuse is None):
        raise ValueError("give either the engine directory or the payload to reuse")
    if engine is not None:
        written = prepare(
            engine,
            output,
            files["engine_prefix"],
            backend=files["payload_backend"],
            worker=files["worker"],
        )
        written.replace(destination)
        return destination
    assert reuse is not None
    data = reuse.read_bytes()
    validate_payload(
        json.loads(data),
        output,
        files["engine_prefix"],
        files["payload_backend"],
        files["worker"],
    )
    destination.write_bytes(data)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, help="Release target ID, e.g. linux-x86_64-cpu")
    parser.add_argument("--output", type=Path, required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--engine", type=Path, help="Frozen engine directory to package")
    source.add_argument("--reuse", type=Path, help="Existing payload manifest to adopt")
    args = parser.parse_args()
    print(prepare_for_target(args.target, args.output, engine=args.engine, reuse=args.reuse))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
