#!/usr/bin/env python3
"""Check the complete target matrix and exact set of bounded public downloads.

With --feed, check the update feed instead: one entry per release target, each
with its edition's backend, kind, engine identity and an engine package, signed
files below GitHub's size limit, and AppImage parts that add up to the package.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import re
from pathlib import Path, PurePath

from release_targets import (
    engine_id,
    platform_key,
    targets,
    update_files,
    validate_manifest,
    version,
)

MAX_PUBLIC_ASSET_BYTES = 2 * 1024**3
SHA256_RE = re.compile(r"[0-9a-f]{64}")
PROTOCOL_RE = re.compile(r"pub const PROTOCOL_VERSION: u32 = (\d+);")
ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def expected_assets(output: Path) -> dict[str, tuple[Path, str]]:
    listing = output / "release-files.txt"
    paths = [Path(line) for line in listing.read_text(encoding="utf-8").splitlines() if line]
    index = json.loads((output / "release-index.json").read_text())
    public = index.get("public_assets")
    if not isinstance(public, list) or not public:
        raise ValueError("release index has no verified public assets")
    wanted = {item["filename"] for item in public} | {"SHA256SUMS", "release-index.json"}
    if len(paths) != len(wanted) or {path.name for path in paths} != wanted:
        raise ValueError("release file list does not match the verified release index")
    result: dict[str, tuple[Path, str]] = {}
    for path in paths:
        resolved = path.resolve()
        if not resolved.is_relative_to(output.resolve()) or PurePath(path.name).name != path.name:
            raise ValueError(f"release file escaped prepared output: {path}")
        if not path.is_file() or path.name in result:
            raise ValueError(f"release file is missing or duplicated: {path.name}")
        if path.stat().st_size >= MAX_PUBLIC_ASSET_BYTES:
            raise ValueError(f"release asset exceeds GitHub's size limit: {path.name}")
        result[path.name] = (path, sha256(path))
    if set(result) & {"release-files.txt", "matrix-selection.json"}:
        raise ValueError("private preparation manifests must not be public assets")
    if not {"SHA256SUMS", "release-index.json"}.issubset(result):
        raise ValueError("SHA256SUMS and release-index.json are required public assets")
    for item in public:
        path, digest = result[item["filename"]]
        if digest != item["sha256"] or path.stat().st_size != item["size"]:
            raise ValueError(f"public asset differs from verified index: {path.name}")
    return result


def verify(output: Path, manifest_path: Path, commit: str) -> None:
    manifest = json.loads(manifest_path.read_text())
    validate_manifest(manifest)
    index = json.loads((output / "release-index.json").read_text())
    wanted = {target["id"] for target in manifest["artifacts"]}
    if (
        index["repository_commit"] != commit
        or index["version"] != manifest["version"]
        or {target["id"] for target in index["installers"]} != wanted
        or set(index["source_matrix"]) != wanted
    ):
        raise ValueError("prepared release is missing targets or has a different identity")
    assets = expected_assets(output)
    # Preparation also keeps private build manifests, so validate the checksum
    # set directly rather than treating those private files as public downloads.
    lines = (output / "SHA256SUMS").read_text().splitlines()
    checksums = {line.split("  ", 1)[1]: line.split("  ", 1)[0] for line in lines}
    if checksums != {
        name: digest
        for name, (_path, digest) in assets.items()
        if name not in {"SHA256SUMS", "release-index.json"}
    }:
        raise ValueError("prepared checksums do not cover exactly the public payloads")
    print(f"Verified {len(wanted)} backend distributions and {len(assets)} public files")


def _signature(value: object, label: str) -> None:
    try:
        decoded = base64.b64decode(str(value), validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError(f"{label} has no minisign signature") from error
    if not decoded.startswith(b"untrusted comment:"):
        raise ValueError(f"{label} has no minisign signature")


def _https_under(url: object, base: str, name: str, label: str) -> None:
    if url != f"{base}/{name}" or not base.startswith("https://"):
        raise ValueError(f"{label} must be {name} under {base} over HTTPS, not {url!r}")


def _download(item: object, base: str, name: str, label: str) -> int:
    if not isinstance(item, dict) or item.get("name") != name:
        raise ValueError(f"{label} must be the file {name}")
    _https_under(item.get("url"), base, name, label)
    size = item.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or not 0 < size < MAX_PUBLIC_ASSET_BYTES:
        raise ValueError(f"{label} needs a size below GitHub's 2 GiB file limit")
    if not SHA256_RE.fullmatch(str(item.get("sha256", ""))):
        raise ValueError(f"{label} needs a SHA-256 digest")
    _signature(item.get("signature"), label)
    return size


def verify_feed(feed_path: Path, commit: str, *, release_version: str | None = None) -> None:
    """Check that the feed serves every edition its own complete, signed update."""
    release_version = release_version or version()
    feed = json.loads(feed_path.read_text(encoding="utf-8"))
    if feed.get("version") != release_version:
        raise ValueError(f"the feed is for {feed.get('version')!r}, not {release_version!r}")
    match = PROTOCOL_RE.search((ROOT / "desktop/src-tauri/src/types.rs").read_text())
    protocol = int(match.group(1)) if match else None
    channel = "beta" if "-" in release_version else "stable"
    platforms = feed.get("platforms")
    expected = {platform_key(target): target for target in targets()}
    if not isinstance(platforms, dict) or set(platforms) != set(expected):
        raise ValueError(
            "the feed must have exactly one entry per release target: missing "
            f"{sorted(set(expected) - set(platforms or {}))}, unknown "
            f"{sorted(set(platforms or {}) - set(expected))}"
        )
    base = f"https://github.com/HerRei/local-upscale/releases/download/v{release_version}"
    bases = {str(entry.get("url", "")).rsplit("/", 1)[0] for entry in platforms.values()}
    if len(bases) == 1:
        base = bases.pop()
    for key, target in expected.items():
        entry = platforms[key]
        files = update_files(target, release_version)
        contract = entry.get("localsr")
        if not isinstance(contract, dict):
            raise ValueError(f"{key} has no LocalSR release contract")
        identity = engine_id(target, commit)
        if (
            contract.get("channel") != channel
            or contract.get("backend") != target["update"]["backend"]
            or contract.get("kind") != target["update"]["kind"]
            or contract.get("protocol") != protocol
            or contract.get("engine_id") != identity
        ):
            raise ValueError(
                f"{key} must be the {channel} {target['update']['backend']} "
                f"{target['update']['kind']} build with engine {identity} and protocol {protocol}"
            )
        size = contract.get("size")
        unpacked = contract.get("unpacked_size")
        if (
            not isinstance(size, int)
            or not isinstance(unpacked, int)
            or size <= 0
            or unpacked < size
            or not SHA256_RE.fullmatch(str(contract.get("sha256", "")))
        ):
            raise ValueError(f"{key} needs its package size, unpacked size and digest")
        _signature(entry.get("signature"), key)
        name = files["update_package"]
        parts = contract.get("parts")
        if parts is None:
            if size >= MAX_PUBLIC_ASSET_BYTES:
                raise ValueError(f"{key}: {name} exceeds GitHub's 2 GiB file limit")
            _https_under(entry.get("url"), base, name, key)
        else:
            if not isinstance(parts, list) or not 1 < len(parts) <= 64:
                raise ValueError(f"{key} must list between 2 and 64 package parts")
            total = sum(
                _download(part, base, f"{name}.part-{index:04d}", f"{key} part {index}")
                for index, part in enumerate(parts, start=1)
            )
            if total != size:
                raise ValueError(f"{key}: the package parts do not add up to the package")
            _https_under(entry.get("url"), base, f"{name}.part-0001", key)
        # Every release builds a new engine id, so every entry needs the engine package.
        engine = contract.get("engine")
        if not isinstance(engine, dict) or engine.get("id") != identity:
            raise ValueError(f"{key} has no engine package for {identity}")
        _download(engine.get("manifest"), base, files["engine_manifest"], f"{key} engine manifest")
        engine_parts = engine.get("parts")
        if not isinstance(engine_parts, list) or not 0 < len(engine_parts) <= 64:
            raise ValueError(f"{key} must list between 1 and 64 engine parts")
        for index, part in enumerate(engine_parts, start=1):
            _download(
                part, base, f"{files['engine_prefix']}.part-{index:04d}", f"{key} engine {index}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, nargs="?")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--feed", type=Path, help="Check this update feed (beta.json)")
    parser.add_argument("--version", help="Release version of the feed (default: pyproject)")
    args = parser.parse_args()
    if args.feed:
        verify_feed(args.feed, args.commit, release_version=args.version)
        print(f"Verified {len(targets())} update targets in {args.feed}")
    if args.output or args.manifest:
        if not (args.output and args.manifest):
            parser.error("checking a prepared release needs the output folder and --manifest")
        verify(args.output, args.manifest, args.commit)
    elif not args.feed:
        parser.error("give a prepared release output with --manifest, or --feed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
