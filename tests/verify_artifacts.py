#!/usr/bin/env python3
"""Stream-verify release archives, checksums, metadata, and native architectures.

The verifier intentionally does not extract archives. LocalSR bundles are
large, and extraction into /tmp would turn verification into another SSD-space
risk. It parses Mach-O, ELF, and PE headers directly from archive members.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import struct
import sys
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterator

CPU_ARCHES = {
    0x00000007: "i386",
    0x01000007: "x86_64",
    0x0000000C: "arm",
    0x0100000C: "arm64",
}
ELF_ARCHES = {3: "i386", 40: "arm", 62: "x86_64", 183: "arm64", 224: "amdgpu"}
PE_ARCHES = {0x014C: "i386", 0x8664: "x86_64", 0xAA64: "arm64"}
SHA256_RE = re.compile(r"\b([0-9a-fA-F]{64})\b")
ROCM_DEVICE_CODE_SUFFIXES = {".co", ".hsaco"}


@dataclass(frozen=True)
class ArtifactSpec:
    filename: str
    platform: str
    architecture: str
    backend: str
    main: str
    require_mps: bool = False


@dataclass(frozen=True)
class MemberHeader:
    name: str
    size: int
    header: bytes


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _cpu_name(value: int) -> str:
    return CPU_ARCHES.get(value, f"cpu-0x{value:08x}")


def macho_arches(header: bytes) -> set[str] | None:
    """Return Mach-O slices found in *header*, or None for a non-Mach-O file."""
    if len(header) < 8:
        return None
    magic = header[:4]
    thin = {
        b"\xce\xfa\xed\xfe": "<",
        b"\xcf\xfa\xed\xfe": "<",
        b"\xfe\xed\xfa\xce": ">",
        b"\xfe\xed\xfa\xcf": ">",
    }
    if magic in thin:
        return {_cpu_name(struct.unpack_from(thin[magic] + "I", header, 4)[0])}

    fat = {
        b"\xca\xfe\xba\xbe": (">", 20),
        b"\xbe\xba\xfe\xca": ("<", 20),
        b"\xca\xfe\xba\xbf": (">", 32),
        b"\xbf\xba\xfe\xca": ("<", 32),
    }
    if magic not in fat:
        return None
    endian, stride = fat[magic]
    count = struct.unpack_from(endian + "I", header, 4)[0]
    if count > 32 or len(header) < 8 + count * stride:
        raise ValueError(f"Invalid/truncated Mach-O fat header with {count} slices")
    return {
        _cpu_name(struct.unpack_from(endian + "I", header, 8 + index * stride)[0])
        for index in range(count)
    }


def elf_arch(header: bytes) -> str | None:
    if len(header) < 20 or header[:4] != b"\x7fELF":
        return None
    endian = "<" if header[5] == 1 else ">" if header[5] == 2 else None
    if endian is None:
        raise ValueError("Invalid ELF endianness")
    machine = struct.unpack_from(endian + "H", header, 18)[0]
    return ELF_ARCHES.get(machine, f"elf-machine-{machine}")


def pe_arch(header: bytes) -> str | None:
    if len(header) < 64 or header[:2] != b"MZ":
        return None
    pe_offset = struct.unpack_from("<I", header, 0x3C)[0]
    if pe_offset + 6 > len(header):
        raise ValueError("PE header lies beyond the streamed member header")
    if header[pe_offset : pe_offset + 4] != b"PE\0\0":
        return None
    machine = struct.unpack_from("<H", header, pe_offset + 4)[0]
    return PE_ARCHES.get(machine, f"pe-machine-0x{machine:04x}")


def _safe_member_name(name: str) -> str:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Unsafe archive member path: {name}")
    return str(path)


def _read_header(stream: BinaryIO, limit: int = 65536) -> bytes:
    return stream.read(limit)


def iter_archive_headers(path: Path) -> Iterator[MemberHeader]:
    if path.name.endswith(".tar.gz"):
        with tarfile.open(path, "r:gz") as archive:
            for member in archive:
                name = _safe_member_name(member.name)
                if not member.isfile():
                    continue
                stream = archive.extractfile(member)
                if stream is None:
                    raise ValueError(f"Cannot read archive member: {name}")
                yield MemberHeader(name, member.size, _read_header(stream))
        return
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist():
                name = _safe_member_name(member.filename)
                if member.is_dir():
                    continue
                with archive.open(member) as stream:
                    yield MemberHeader(name, member.file_size, _read_header(stream))
        return
    raise ValueError(f"Unsupported artifact format: {path.name}")


def load_manifest(path: Path) -> list[ArtifactSpec]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError(f"Unsupported artifact manifest schema: {data.get('schema_version')}")
    return [ArtifactSpec(**entry) for entry in data["artifacts"]]


def locate_exact(root: Path, filename: str) -> Path:
    matches = [path for path in root.rglob(filename) if path.is_file()]
    if len(matches) != 1:
        rendered = ", ".join(str(path) for path in matches) or "none"
        raise ValueError(f"Expected exactly one {filename}; found {len(matches)}: {rendered}")
    return matches[0]


def verify_checksum(artifact: Path) -> str:
    expected_file = artifact.with_name(artifact.name + ".sha256")
    if not expected_file.is_file():
        raise ValueError(f"Missing checksum sidecar: {expected_file.name}")
    text = expected_file.read_text(encoding="utf-8-sig", errors="strict")
    match = SHA256_RE.search(text)
    if not match:
        raise ValueError(f"No SHA256 digest in {expected_file.name}")
    expected = match.group(1).lower()
    actual = sha256(artifact)
    if actual != expected:
        raise ValueError(f"Checksum mismatch for {artifact.name}: {actual} != {expected}")
    return actual


def verify_metadata(artifact: Path, spec: ArtifactSpec, digest: str) -> dict[str, object]:
    path = artifact.with_name(artifact.name + ".metadata.json")
    if not path.is_file():
        raise ValueError(f"Missing metadata sidecar: {path.name}")
    metadata = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "artifact_filename": spec.filename,
        "platform": spec.platform,
        "architecture": spec.architecture,
        "backend": spec.backend,
        "sha256": digest,
    }
    for key, value in expected.items():
        if metadata.get(key) != value:
            raise ValueError(f"{path.name}: {key}={metadata.get(key)!r}, expected {value!r}")
    for key in ("repository_commit", "github_run_id", "run_attempt", "timestamp"):
        if not metadata.get(key):
            raise ValueError(f"{path.name}: required field {key!r} is empty")
    signing = metadata.get("signing")
    if not isinstance(signing, dict) or not signing.get("status"):
        raise ValueError(f"{path.name}: signing status is missing")
    if signing.get("status") == "developer-id-notarized":
        if not all(
            signing.get(key)
            for key in ("developer_id", "notarized", "gatekeeper_accepted", "team_id")
        ):
            raise ValueError(f"{path.name}: incomplete Developer ID/notarization evidence")
    if spec.platform == "linux" and spec.backend == "CPU":
        live_models = metadata.get("live_models")
        if not isinstance(live_models, dict) or live_models.get("result") != "PASS":
            raise ValueError(f"{path.name}: real Quick/Best live-model evidence is missing")
        presets = {
            str(model.get("preset"))
            for model in live_models.get("models", [])
            if isinstance(model, dict)
        }
        if presets != {"Quick", "Best"}:
            raise ValueError(f"{path.name}: Quick/Best live-model evidence is incomplete")
    if spec.require_mps:
        mps = metadata.get("mps")
        if not isinstance(mps, dict):
            raise ValueError(f"{path.name}: missing MPS provenance")
        wheel = str(mps.get("torch_arm64_wheel", ""))
        if "torch-2.2.2" not in wheel or "macosx_11_0_arm64" not in wheel:
            raise ValueError(f"{path.name}: unexpected ARM64 torch provenance: {wheel!r}")
        if not SHA256_RE.fullmatch(str(mps.get("torch_arm64_sha256", ""))):
            raise ValueError(f"{path.name}: invalid ARM64 torch wheel SHA256")
    return metadata


def verify_archive(artifact: Path, spec: ArtifactSpec) -> tuple[int, list[str]]:
    native_count = 0
    main_arches: set[str] | None = None
    torch_arm_members: list[str] = []
    errors: list[str] = []

    for member in iter_archive_headers(artifact):
        try:
            if spec.platform == "macos":
                arches = macho_arches(member.header)
                if arches is None:
                    continue
                native_count += 1
                if spec.architecture not in arches:
                    errors.append(f"{member.name}: Mach-O slices {sorted(arches)}")
                if member.name == spec.main:
                    main_arches = arches
                if spec.require_mps and "torch" in member.name.lower():
                    torch_arm_members.append(member.name)
            elif spec.platform == "linux":
                architecture = elf_arch(member.header)
                if architecture is None:
                    continue
                # ROCm wheels intentionally ship ELF code objects for AMD GPUs.
                # They are not host executables and must not be compared with
                # the bundle's x86_64 CPU architecture. Keep this exception
                # narrow so an AMDGPU-tagged .so still fails verification.
                if (
                    architecture == "amdgpu"
                    and PurePosixPath(member.name).suffix.lower() in ROCM_DEVICE_CODE_SUFFIXES
                ):
                    continue
                native_count += 1
                if architecture != spec.architecture:
                    errors.append(f"{member.name}: ELF architecture {architecture}")
                if member.name == spec.main:
                    main_arches = {architecture}
            elif spec.platform == "windows":
                architecture = pe_arch(member.header)
                if architecture is None:
                    continue
                native_count += 1
                if architecture != spec.architecture:
                    errors.append(f"{member.name}: PE architecture {architecture}")
                if member.name == spec.main:
                    main_arches = {architecture}
            else:
                raise ValueError(f"Unknown platform: {spec.platform}")
        except ValueError as exc:
            errors.append(f"{member.name}: {exc}")

    if main_arches is None:
        errors.append(f"main executable not found as native binary: {spec.main}")
    elif main_arches != {spec.architecture}:
        errors.append(
            f"{spec.main}: main executable must be thin {spec.architecture}, got {sorted(main_arches)}"
        )
    if native_count == 0:
        errors.append("archive contains no recognized native binaries")
    if spec.require_mps and not any("libtorch" in name.lower() for name in torch_arm_members):
        errors.append("ARM64 bundle contains no ARM64 libtorch Mach-O member")
    return native_count, errors


def atomic_write(path: Path, text: str) -> None:
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def verify_artifact(artifact: Path, spec: ArtifactSpec) -> tuple[str, str]:
    digest = verify_checksum(artifact)
    verify_metadata(artifact, spec, digest)
    native_count, errors = verify_archive(artifact, spec)
    lines = [
        f"Artifact: {artifact.name}",
        f"Executable: {spec.main}",
        f"Architecture: {spec.architecture}",
        f"Backend: {spec.backend}",
        f"Native binaries inspected: {native_count}",
        f"SHA256: {digest}",
    ]
    if errors:
        lines.append("Result: FAIL")
        lines.extend(f"Mismatch: {error}" for error in errors)
        report = "\n".join(lines) + "\n"
        atomic_write(artifact.with_name(artifact.name + ".architecture.txt"), report)
        raise ValueError(report.rstrip())
    lines.append("Result: PASS")
    report = "\n".join(lines) + "\n"
    atomic_write(artifact.with_name(artifact.name + ".architecture.txt"), report)
    return report, digest


def verify_unique_digests(verified: list[tuple[str, str]]) -> None:
    by_digest: dict[str, list[str]] = {}
    for filename, digest in verified:
        by_digest.setdefault(digest, []).append(filename)
    duplicates = [filenames for filenames in by_digest.values() if len(filenames) > 1]
    if duplicates:
        groups = "; ".join(", ".join(filenames) for filenames in duplicates)
        raise ValueError(f"Release archives must have distinct SHA-256 digests: {groups}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact_root", type=Path)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "ci" / "release-artifacts.json",
    )
    parser.add_argument("--only", help="verify only one filename from the manifest")
    args = parser.parse_args(argv)
    specs = load_manifest(args.manifest)
    if args.only:
        specs = [spec for spec in specs if spec.filename == args.only]
        if not specs:
            parser.error(f"artifact is not present in manifest: {args.only}")
    failures: list[str] = []
    verified: list[tuple[str, str]] = []
    for spec in specs:
        try:
            artifact = locate_exact(args.artifact_root, spec.filename)
            report, digest = verify_artifact(artifact, spec)
            print(report, end="")
            verified.append((spec.filename, digest))
        except Exception as exc:  # report the complete matrix before failing
            failures.append(f"{spec.filename}: {exc}")
            print(f"Artifact: {spec.filename}\nResult: FAIL\nReason: {exc}", file=sys.stderr)
    try:
        verify_unique_digests(verified)
    except ValueError as exc:
        failures.append(str(exc))
    if failures:
        print("\nArtifact verification failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print(f"Verified {len(specs)} release artifacts with distinct digests: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
