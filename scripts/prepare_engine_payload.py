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
import os
import re
import subprocess
import tarfile
import zlib
from pathlib import Path, PurePosixPath

from release_targets import (
    ROOT,
    UPDATE_ARCH,
    target,
    targets,
    update_files,
)
from release_targets import (
    engine_id as compute_engine_id,
)
from release_targets import (
    protocol_version as current_protocol_version,
)
from release_targets import (
    version as current_version,
)

PART_BYTES = 1900 * 1024**2
# desktop/src-tauri/src/engine_payload.rs accepts the same backend names.
BACKENDS = {"CUDA", "ROCM", "CPU", "MPS", "DIRECTML"}
OS_VALUES = {"windows", "linux", "macos"}
ARCH_VALUES = {"x86_64", "aarch64"}
ENGINE_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")
# What the updaters of LocalSR 0.4.0 and 0.5.0 accept in an engine manifest: exactly these
# fields (serde deny_unknown_fields), schema_version 1. A field more and they refuse the
# update. tests/legacy_clients holds their parsers; never add a field here.
LEGACY_UPDATE_FIELDS = (
    "schema_version",
    "format",
    "backend",
    "file_count",
    "unpacked_bytes",
    "parts",
)
LEGACY_PART_FIELDS = ("filename", "size", "sha256")


def legacy_update_manifest(manifest: dict) -> bytes:
    """The engine manifest of the update feed, readable by every updater since 0.4.0.

    The same package as ``manifest`` (schema 2): the version, platform and engine
    identity it leaves out are bound by the signed release statement instead.
    """
    legacy = {
        "schema_version": 1,
        "format": manifest["format"],
        "backend": manifest["backend"],
        "file_count": manifest["file_count"],
        "unpacked_bytes": manifest["unpacked_bytes"],
        "parts": [
            {field: part[field] for field in LEGACY_PART_FIELDS} for part in manifest["parts"]
        ],
    }
    return (json.dumps(legacy, indent=2, sort_keys=True) + "\n").encode("utf-8")


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


def _engine_files(engine: Path) -> list[Path]:
    """The files to archive. A symlink is accepted only as a file link whose target
    lies inside the engine folder (PyInstaller's libfoo.so -> libfoo.so.1); it is
    archived as that regular file. Directory links, links leaving the folder,
    dangling links and loops are refused."""
    root = engine.resolve()
    files = []
    for path in sorted(engine.rglob("*")):
        if path.is_symlink():
            try:
                target_path = path.resolve(strict=True)
            except (OSError, RuntimeError) as error:
                raise ValueError(f"engine payload contains a broken link: {path.name}") from error
            if not target_path.is_relative_to(root):
                raise ValueError(f"engine payload contains a link leaving the engine: {path.name}")
            if not target_path.is_file():
                raise ValueError(f"engine payload contains a link to a directory: {path.name}")
        elif path.is_dir():
            continue
        elif not path.is_file():
            raise ValueError("engine payload contains a non-regular file")
        files.append(path)
    return files


def _head_commit() -> str:
    env_sha = os.environ.get("GITHUB_SHA", "").strip()
    if env_sha:
        return env_sha
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def _resolve_target_for(backend: str, os_name: str, arch: str) -> dict | None:
    for entry in targets():
        if (
            entry["platform"] == os_name
            and UPDATE_ARCH[entry["architecture"]] == arch
            and entry["update"]["backend"].upper() == backend
        ):
            return entry
    return None


def _default_engine_id(
    entry: dict | None,
    *,
    commit: str | None = None,
    engine_id: str | None = None,
) -> str:
    if engine_id is not None:
        return engine_id
    env_id = os.environ.get("LOCALSR_ENGINE_ID", "").strip()
    if env_id and (entry is None or env_id.startswith(f"{entry['id']}-")):
        return env_id
    if entry is not None:
        return compute_engine_id(entry, commit or _head_commit())
    raise ValueError("engine_id is required when target metadata cannot be inferred")


def prepare(
    engine: Path,
    output: Path,
    prefix: str,
    *,
    part_bytes: int = PART_BYTES,
    backend: str = "CUDA",
    worker: str = "localsr-worker.exe",
    version: str | None = None,
    os_name: str | None = None,
    arch: str | None = None,
    protocol_version: int | None = None,
    engine_id: str | None = None,
) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", prefix):
        raise ValueError("invalid engine payload prefix")
    if not 1 <= part_bytes < 2 * 1024**3:
        raise ValueError("payload parts must be smaller than 2 GiB")
    if backend not in BACKENDS:
        raise ValueError("unsupported engine payload backend")
    resolved_os = os_name or (
        "windows" if worker.endswith(".exe") else ("macos" if backend == "MPS" else "linux")
    )
    resolved_arch = arch or ("aarch64" if resolved_os == "macos" else "x86_64")
    if resolved_os not in OS_VALUES or resolved_arch not in ARCH_VALUES:
        raise ValueError("unsupported engine payload platform")
    resolved_version = version or current_version()
    resolved_protocol = current_protocol_version() if protocol_version is None else protocol_version
    if not _positive_int(resolved_protocol):
        raise ValueError("invalid engine payload protocol_version")
    resolved_engine_id = _default_engine_id(
        _resolve_target_for(backend, resolved_os, resolved_arch),
        engine_id=engine_id,
    )
    if not ENGINE_ID_RE.fullmatch(resolved_engine_id):
        raise ValueError("invalid engine payload engine_id")
    if not (engine / worker).is_file():
        raise ValueError("the frozen worker is missing")
    output.mkdir(parents=True, exist_ok=True)
    files = _engine_files(engine)
    writer = PartWriter(output, prefix, part_bytes)
    try:
        with gzip.GzipFile(fileobj=writer, mode="wb", compresslevel=1, mtime=0) as compressed:
            # Links inside the engine were checked above and are archived as the
            # regular files they point to; dereferencing also keeps tarfile from
            # writing a zero-length hard-link entry for a second name of a file.
            with tarfile.open(
                fileobj=compressed, mode="w|", format=tarfile.PAX_FORMAT, dereference=True
            ) as archive:
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
        "schema_version": 2,
        "format": "tar.gz.parts",
        "backend": backend,
        "version": resolved_version,
        "os": resolved_os,
        "arch": resolved_arch,
        "protocol_version": resolved_protocol,
        "engine_id": resolved_engine_id,
        "file_count": len(files),
        "unpacked_bytes": sum(p.stat().st_size for p in files),
        "parts": writer.parts,
    }
    path = output / "engine-payload.json"
    # Never replace a manifest that is already there (the caller renames this one).
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
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
    manifest: object,
    directory: Path,
    prefix: str,
    backend: str,
    worker: str,
    *,
    expected_version: str | None = None,
    expected_os: str | None = None,
    expected_arch: str | None = None,
    expected_protocol_version: int | None = None,
    expected_engine_id: str | None = None,
) -> list[Path]:
    """Check an engine payload in ``directory`` the way the app's installer will.

    Every part must be a regular file named ``<prefix>.part-NNNN`` directly in the
    folder, of the listed size and digest; together they must be a gzip tar archive of
    exactly ``file_count`` regular files with safe relative paths, ``unpacked_bytes`` in
    all, and the worker at the top. Returns the part paths in order.
    """
    parts = manifest.get("parts") if isinstance(manifest, dict) else None
    manifest_version = manifest.get("version") if isinstance(manifest, dict) else None
    manifest_os = manifest.get("os") if isinstance(manifest, dict) else None
    manifest_arch = manifest.get("arch") if isinstance(manifest, dict) else None
    manifest_protocol = manifest.get("protocol_version") if isinstance(manifest, dict) else None
    manifest_engine_id = manifest.get("engine_id") if isinstance(manifest, dict) else None
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != 2
        or manifest.get("format") != "tar.gz.parts"
        or manifest.get("backend") != backend
        or not isinstance(manifest_version, str)
        or not manifest_version.strip()
        or manifest_os not in OS_VALUES
        or manifest_arch not in ARCH_VALUES
        or not _positive_int(manifest_protocol)
        or not isinstance(manifest_engine_id, str)
        or not ENGINE_ID_RE.fullmatch(manifest_engine_id)
        or (expected_version is not None and manifest_version != expected_version)
        or (expected_os is not None and manifest_os != expected_os)
        or (expected_arch is not None and manifest_arch != expected_arch)
        or (
            expected_protocol_version is not None and manifest_protocol != expected_protocol_version
        )
        or (expected_engine_id is not None and manifest_engine_id != expected_engine_id)
        or not _positive_int(manifest.get("file_count"))
        or not _positive_int(manifest.get("unpacked_bytes"))
        or not isinstance(parts, list)
        or not 1 <= len(parts) <= 64
    ):
        raise ValueError(f"invalid {backend} engine payload manifest")
    paths = []
    for index, part in enumerate(parts, start=1):
        name = part.get("filename") if isinstance(part, dict) else None
        if not isinstance(name, str) or name != f"{prefix}.part-{index:04d}":
            raise ValueError(f"engine payload part {name!r} is not part {index} of {prefix}")
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
        with (
            gzip.GzipFile(fileobj=joined, mode="rb") as decoded,
            tarfile.open(fileobj=decoded, mode="r|") as archive,
        ):
            for member in archive:
                relative = PurePosixPath(member.name)
                if (
                    not member.isfile()
                    or relative.is_absolute()
                    or not relative.parts
                    or ".." in relative.parts
                    or "\\" in member.name
                    or ":" in member.name
                    or member.name != relative.as_posix()
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
    commit: str | None = None,
    engine_id: str | None = None,
) -> Path:
    """Write the updater's engine package of one edition into ``output``."""
    entry = target(target_id)
    resolved_version = release_version or current_version()
    resolved_os = entry["platform"]
    resolved_arch = UPDATE_ARCH[entry["architecture"]]
    resolved_protocol = current_protocol_version()
    resolved_engine_id = _default_engine_id(entry, commit=commit, engine_id=engine_id)
    files = update_files(entry, resolved_version)
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
            version=resolved_version,
            os_name=resolved_os,
            arch=resolved_arch,
            protocol_version=resolved_protocol,
            engine_id=resolved_engine_id,
        )
        written.replace(destination)
        _write_update_manifest(destination, output / files["engine_update_manifest"])
        return destination
    assert reuse is not None
    data = reuse.read_bytes()
    validate_payload(
        json.loads(data),
        output,
        files["engine_prefix"],
        files["payload_backend"],
        files["worker"],
        expected_version=resolved_version,
        expected_os=resolved_os,
        expected_arch=resolved_arch,
        expected_protocol_version=resolved_protocol,
        expected_engine_id=resolved_engine_id,
    )
    destination.write_bytes(data)
    _write_update_manifest(destination, output / files["engine_update_manifest"])
    return destination


def _write_update_manifest(manifest: Path, update: Path) -> None:
    update.write_bytes(legacy_update_manifest(json.loads(manifest.read_bytes())))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, help="Release target ID, e.g. linux-x86_64-cpu")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", help="Release version (default: pyproject.toml)")
    parser.add_argument("--commit", help="Release commit SHA for engine_id calculation")
    parser.add_argument("--engine-id", help="Explicit engine_id override")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--engine", type=Path, help="Frozen engine directory to package")
    source.add_argument("--reuse", type=Path, help="Existing payload manifest to adopt")
    args = parser.parse_args()
    print(
        prepare_for_target(
            args.target,
            args.output,
            engine=args.engine,
            reuse=args.reuse,
            release_version=args.version,
            commit=args.commit,
            engine_id=args.engine_id,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
