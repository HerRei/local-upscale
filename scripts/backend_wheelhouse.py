#!/usr/bin/env python3
"""Install a target's hashed dependency lock from a reusable, verified wheelhouse."""

from __future__ import annotations

import argparse
import email
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from release_targets import ROOT, targets


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024**2), b""):
            digest.update(block)
    return digest.hexdigest()


def cache_key(lock: Path) -> str:
    digest = hashlib.sha256()
    for source in (lock, Path(__file__), ROOT / "requirements/build-tools.txt"):
        digest.update(source.read_bytes())
    digest.update(
        f"{sys.implementation.name}:{sys.version}:{sys.platform}:{platform.machine()}".encode()
    )
    return digest.hexdigest()


def describe_wheels(directory: Path) -> list[dict]:
    records = []
    seen = set()
    for path in sorted(directory.glob("*.whl")):
        with zipfile.ZipFile(path) as wheel:
            metadata = [name for name in wheel.namelist() if name.endswith(".dist-info/METADATA")]
            if len(metadata) != 1:
                raise ValueError(f"invalid wheel metadata: {path.name}")
            fields = email.message_from_bytes(wheel.read(metadata[0]))
        name, version = fields["Name"], fields["Version"]
        normalized = name.lower().replace("_", "-").replace(".", "-")
        if normalized in seen:
            raise ValueError(f"duplicate wheel distribution: {name}")
        seen.add(normalized)
        records.append(
            {
                "filename": path.name,
                "name": name,
                "version": version,
                "sha256": sha256(path),
                "size": path.stat().st_size,
            }
        )
    if not records:
        raise ValueError("wheelhouse is empty")
    return records


def verify(directory: Path, key: str) -> dict:
    manifest = json.loads((directory / "manifest.json").read_text())
    if manifest.get("schema_version") != 1 or manifest.get("key") != key:
        raise ValueError("wheelhouse provenance mismatch")
    if describe_wheels(directory) != manifest["wheels"]:
        raise ValueError("wheelhouse file digest or dependency mismatch")
    expected = "".join(
        f"{item['name']}=={item['version']} --hash=sha256:{item['sha256']}\n"
        for item in manifest["wheels"]
    )
    if (directory / "offline.txt").read_text() != expected:
        raise ValueError("wheelhouse offline lock mismatch")
    return manifest


def prepare(lock: Path, cache: Path, target: str) -> tuple[Path, dict]:
    key = cache_key(lock)
    directory = cache / target / key
    if directory.exists():
        print(f"Verifying reusable wheelhouse {target}/{key[:12]}", flush=True)
        return directory, verify(directory, key)
    directory.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".preparing-", dir=directory.parent))
    try:
        env = os.environ.copy()
        env["SOURCE_DATE_EPOCH"] = "1704067200"
        print(f"Building wheelhouse {target}/{key[:12]} from the reviewed lock", flush=True)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "wheel",
                "--disable-pip-version-check",
                "--no-cache-dir",
                "--require-hashes",
                "--no-deps",
                "--wheel-dir",
                str(stage),
                "-r",
                str(lock),
                "--build-constraint",
                str(ROOT / "requirements/build-tools.txt"),
            ],
            check=True,
            env=env,
        )
        records = describe_wheels(stage)
        manifest = {
            "schema_version": 1,
            "key": key,
            "target": target,
            "source_lock_sha256": sha256(lock),
            "wheels": records,
        }
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        (stage / "offline.txt").write_text(
            "".join(
                f"{item['name']}=={item['version']} --hash=sha256:{item['sha256']}\n"
                for item in records
            )
        )
        verify(stage, key)
        stage.rename(directory)
        return directory, manifest
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True)
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if sys.implementation.name != "cpython" or sys.version_info[:2] != (3, 11):
        parser.error("backend locks require CPython 3.11")
    target = next((item for item in targets() if item["id"] == args.target), None)
    actual_platform = (
        "windows" if os.name == "nt" else "linux" if sys.platform.startswith("linux") else "macos"
    )
    if (
        target is None
        or target["platform"] != actual_platform
        or platform.machine().lower() not in {"x86_64", "amd64"}
    ):
        parser.error("wheelhouse must be built and installed on its named target platform")
    lock = ROOT / "requirements/locks" / f"{args.target}.txt"
    directory, manifest = prepare(lock, args.cache_root.resolve(), args.target)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-index",
            "--find-links",
            str(directory),
            "--require-hashes",
            "--no-deps",
            "-r",
            str(directory / "offline.txt"),
        ],
        check=True,
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--no-build-isolation",
            "-e",
            str(ROOT),
        ],
        check=True,
    )
    subprocess.run([sys.executable, "-m", "pip", "check"], check=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
