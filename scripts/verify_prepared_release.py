#!/usr/bin/env python3
"""Check the complete target matrix and exact set of bounded public downloads.

With --feed, check the update feed instead: one entry per release target, each
with its edition's backend, kind, engine identity and an engine package, files below
GitHub's size limit, and AppImage parts that add up to the package. Every signature is
verified against the updater public key, over the local copies of the files (--files)
and over each entry's two signed release statements (format 1 and format 2).
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import re
from collections.abc import Iterable, Iterator
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
PUBLIC_KEY = ROOT / "packaging/updates/production.pub"

# Minisign verification as the app does it (minisign-verify, prehashed signatures only):
# Ed25519 after RFC 8032 over the BLAKE2b-512 digest that tauri signer signs.
_P = 2**255 - 19
_Q = 2**252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)
_Point = tuple[int, int, int, int]


def _add(a: _Point, b: _Point) -> _Point:
    p1 = (a[1] - a[0]) * (b[1] - b[0]) % _P
    p2 = (a[1] + a[0]) * (b[1] + b[0]) % _P
    c = 2 * a[3] * b[3] * _D % _P
    d = 2 * a[2] * b[2] % _P
    e, f, g, h = p2 - p1, d - c, d + c, p2 + p1
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _mul(scalar: int, point: _Point) -> _Point:
    result: _Point = (0, 1, 1, 0)
    while scalar:
        if scalar & 1:
            result = _add(result, point)
        point = _add(point, point)
        scalar >>= 1
    return result


def _recover_x(y: int, sign: int) -> int | None:
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P:
        return None
    return _P - x if (x & 1) != sign else x


def _decompress(data: bytes) -> _Point | None:
    y = int.from_bytes(data, "little")
    sign, y = y >> 255, y & ((1 << 255) - 1)
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % _P)


_GY = 4 * pow(5, _P - 2, _P) % _P
_GX = _recover_x(_GY, 0) or 0
_G: _Point = (_GX, _GY, 1, _GX * _GY % _P)


def _ed25519(public: bytes, message: bytes, signature: bytes) -> bool:
    a, r = _decompress(public), _decompress(signature[:32])
    s = int.from_bytes(signature[32:], "little")
    if a is None or r is None or s >= _Q:
        return False
    # Reject small-order public keys (including identity) before the equation.
    cofactored = _mul(8, a)
    if cofactored[0] == 0 and (cofactored[1] - cofactored[2]) % _P == 0:
        return False
    h = int.from_bytes(hashlib.sha512(signature[:32] + public + message).digest(), "little")
    left, right = _mul(s, _G), _add(r, _mul(h % _Q, a))
    return (left[0] * right[2] - right[0] * left[2]) % _P == 0 and (
        left[1] * right[2] - right[1] * left[2]
    ) % _P == 0


def minisign_verify(public_key: str, signature: str, chunks: Iterable[bytes]) -> bool:
    """Whether ``signature`` (tauri signer's base64 text) signs the bytes of ``chunks``."""
    try:
        key = base64.b64decode(base64.b64decode(public_key).decode().splitlines()[-1])
        lines = base64.b64decode(signature, validate=True).decode().splitlines()
        blob, trusted, global_signature = (
            base64.b64decode(lines[1]),
            lines[2],
            base64.b64decode(lines[3]),
        )
    except (binascii.Error, ValueError, IndexError, UnicodeDecodeError):
        return False
    if (
        len(key) != 42
        or key[:2] != b"Ed"
        or len(blob) != 74
        or blob[:2] != b"ED"
        or blob[2:10] != key[2:10]
        or not trusted.startswith("trusted comment: ")
        or len(global_signature) != 64
    ):
        return False
    digest = hashlib.blake2b(digest_size=64)
    for chunk in chunks:
        digest.update(chunk)
    comment = trusted.removeprefix("trusted comment: ").encode()
    return _ed25519(key[10:], digest.digest(), blob[10:]) and _ed25519(
        key[10:], blob[10:] + comment, global_signature
    )


def _read(paths: list[Path]) -> Iterator[bytes]:
    for path in paths:
        with path.open("rb") as stream:
            yield from iter(lambda: stream.read(4 * 1024 * 1024), b"")


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
    checksums = parse_checksums((output / "SHA256SUMS").read_text().splitlines())
    if checksums != {
        name: digest
        for name, (_path, digest) in assets.items()
        if name not in {"SHA256SUMS", "release-index.json"}
    }:
        raise ValueError("prepared checksums do not cover exactly the public payloads")
    print(f"Verified {len(wanted)} backend distributions and {len(assets)} public files")


def parse_checksums(lines: list[str]) -> dict[str, str]:
    """`<sha256>  <name>` lines; a repeated or malformed line is an error, never a winner."""
    checksums: dict[str, str] = {}
    for line in lines:
        match = re.fullmatch(r"([0-9a-f]{64})  ([^\s/]+)", line)
        if not match or match[2] in checksums:
            raise ValueError(f"bad or repeated line in the checksum file: {line!r}")
        checksums[match[2]] = match[1]
    return checksums


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


class _LocalFiles:
    """The release files on this computer, found by name below the given folders."""

    def __init__(self, folders: list[Path]):
        self.paths: dict[str, set[Path]] = {}
        for folder in folders:
            for path in folder.rglob("*"):
                if path.is_file():
                    self.paths.setdefault(path.name, set()).add(path.resolve())

    def __call__(self, name: str) -> Path:
        found = self.paths.get(name, set())
        if len(found) != 1:
            raise ValueError(f"expected exactly one local copy of {name}, found {len(found)}")
        return next(iter(found))


def _signed_file(item: dict, paths: list[Path], public_key: str, label: str) -> None:
    """The local bytes have the entry's size and digest, and its signature signs them."""
    digest = hashlib.sha256()
    size = 0
    for chunk in _read(paths):
        digest.update(chunk)
        size += len(chunk)
    if (digest.hexdigest(), size) != (item.get("sha256"), item.get("size")):
        raise ValueError(f"{label}: the local file does not match its feed entry")
    if not minisign_verify(public_key, str(item.get("signature", "")), _read(paths)):
        raise ValueError(f"{label} has no valid signature from the updater key")


def _stated(file: object) -> dict:
    file = file if isinstance(file, dict) else {}
    return {"name": file.get("name"), "size": file.get("size"), "sha256": file.get("sha256")}


def _signed_statement(key: str, contract: dict, field: str, public_key: str) -> dict:
    try:
        data = base64.b64decode(str(contract.get(field, "")), validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError(f"{key} has no signed release statement ({field})") from error
    if not data or not minisign_verify(
        public_key, str(contract.get(f"{field}_signature", "")), [data]
    ):
        raise ValueError(f"{key} has no signed release statement from the updater key ({field})")
    return json.loads(data)


def _statement(
    key: str, contract: dict, release_version: str, channel: str, public_key: str
) -> None:
    """The entry carries both signed release statements, and each states exactly what the
    entry says: format 1 for clients before 0.3.8, format 2 (which also covers the package
    size and the part lists) for the current app."""
    engine = contract.get("engine") or {}
    common = {
        "version": release_version,
        "channel": channel,
        "target": key,
        "backend": contract.get("backend"),
        "kind": contract.get("kind"),
        "protocol": contract.get("protocol"),
        "engine_id": contract.get("engine_id"),
        "unpacked_size": contract.get("unpacked_size"),
        "sha256": contract.get("sha256"),
    }
    expected = {
        "statement": {
            **common,
            "schema_version": 1,
            "engine_manifest_sha256": (engine.get("manifest") or {}).get("sha256"),
        },
        "statement_v2": {
            **common,
            "schema_version": 2,
            "size": contract.get("size"),
            "parts": [_stated(part) for part in contract.get("parts") or []],
            "engine_manifest": _stated(engine["manifest"]) if engine.get("manifest") else None,
            "engine_parts": [_stated(part) for part in engine.get("parts") or []],
        },
    }
    for field, wanted in expected.items():
        if _signed_statement(key, contract, field, public_key) != wanted:
            raise ValueError(f"{key}: the signed release statement does not match the entry")


def _engine_manifest(key: str, contract: dict, engine_parts: list, path: Path) -> None:
    """The engine manifest the client will read lists the parts the feed names, and the
    entry reserves room for the engine it unpacks."""
    payload = json.loads(path.read_bytes())
    listed = [
        (part.get("filename"), part.get("size"), part.get("sha256"))
        for part in payload.get("parts", [])
    ]
    named = [(part["name"], part["size"], part["sha256"]) for part in engine_parts]
    if listed != named:
        raise ValueError(
            f"{key}: the engine manifest does not list the engine parts the feed names"
        )
    if contract["unpacked_size"] < payload.get("unpacked_bytes", 0):
        raise ValueError(f"{key}: unpacked_size is below the engine's unpacked size")


def verify_feed(
    feed_path: Path,
    commit: str,
    *,
    files: list[Path],
    release_version: str | None = None,
    public_key: str | None = None,
    host: str | None = None,
) -> None:
    """Check that the feed serves every edition its own complete, signed update.

    Every file is expected under the release's own download URL, or under `host` when
    that is named; never under whatever prefix the feed happens to use.
    """
    release_version = release_version or version()
    public_key = public_key or PUBLIC_KEY.read_text(encoding="utf-8").strip()
    local = _LocalFiles(files)
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
    base = host or f"https://github.com/HerRei/local-upscale/releases/download/v{release_version}"
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
        # Every entry carries an engine package for clients whose installed id differs.
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
        # The shape is right; now the signatures, over the bytes that will be published.
        _statement(key, contract, release_version, channel, public_key)
        _engine_manifest(key, contract, engine_parts, local(files["engine_manifest"]))
        package = [local(part["name"]) for part in parts] if parts else [local(name)]
        _signed_file(
            {"size": size, "sha256": contract["sha256"], "signature": entry.get("signature")},
            package,
            public_key,
            key,
        )
        for item in [*(parts or []), engine["manifest"], *engine_parts]:
            _signed_file(item, [local(item["name"])], public_key, f"{key}: {item['name']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, nargs="?")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--feed", type=Path, help="Check this update feed (beta.json)")
    parser.add_argument(
        "--files",
        type=Path,
        action="append",
        default=[],
        help="Folder holding local copies of the feed's files (repeatable)",
    )
    parser.add_argument("--version", help="Release version of the feed (default: pyproject)")
    parser.add_argument(
        "--host", help="Download URL prefix of the feed (default: the GitHub release)"
    )
    parser.add_argument("--public-key", type=Path, help="Updater public key (default: production)")
    args = parser.parse_args()
    if args.feed:
        if not args.files:
            parser.error("--feed needs --files: the signatures are checked over the files")
        verify_feed(
            args.feed,
            args.commit,
            files=args.files,
            release_version=args.version,
            public_key=args.public_key.read_text().strip() if args.public_key else None,
            host=args.host,
        )
        print(f"Verified {len(targets())} update targets and their signatures in {args.feed}")
    if args.output or args.manifest:
        if not (args.output and args.manifest):
            parser.error("checking a prepared release needs the output folder and --manifest")
        verify(args.output, args.manifest, args.commit)
    elif not args.feed:
        parser.error("give a prepared release output with --manifest, or --feed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
