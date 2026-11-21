#!/usr/bin/env python3
"""Keep the direct-edition updater key in the local macOS Keychain.

Prepare once; sign immutable artifacts without exporting the retained private
key to a repository or passing it on a command line. This does not publish feeds.

`platforms DOWNLOADS --feed build/release-<version>/beta.json --commit <sha>` takes
the Windows and Linux build artifacts as `gh run download` leaves them (one folder
per release target). It first checks every package against its checksum, its
installed smoke report and its engine payload, and signs nothing unless all pass;
then it signs private copies that match what it checked, and adds the feed entries,
each with a signed release statement, next to the macOS entry.

`statement FEED` signs the release statements of every entry that lacks either; the macOS
release script runs it on the feed entry it writes. Each entry carries two: `statement`
(format 1, for clients before 0.3.8) and `statement_v2` (format 2, which also covers the
package size and the part lists; see docs/updates.md).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from prepare_engine_payload import validate_payload
from release_targets import engine_id, expected_update_identity, targets, update_files, version

ROOT = Path(__file__).resolve().parents[1]
SHA256_RE = re.compile(r"[0-9a-f]{64}")
KEY_ROOT = Path.home() / ".local/share/localsr-release-signing"
HELPER = KEY_ROOT / "update-keychain"
SERVICE = "com.localsr.release.updater"
ACCOUNT = "production-v1"
IDENTITY = "Developer ID Application: Hermes Reisner (Z2TU844D84)"
PUBLIC = ROOT / "packaging/updates/production.pub"
TAURI = ROOT / "desktop/node_modules/.bin/tauri"


HELPER_REQUIREMENT = (
    '=identifier "com.localsr.release.update-keychain" and anchor apple generic '
    'and certificate leaf[subject.OU] = "Z2TU844D84"'
)


def _private_key_folder() -> None:
    if KEY_ROOT.is_symlink() or not KEY_ROOT.is_dir() or KEY_ROOT.stat().st_mode & 0o077:
        raise ValueError("The release signing directory must be private (mode 0700)")


def _verify_helper() -> None:
    subprocess.run(
        ["codesign", "--verify", "--strict", "-R", HELPER_REQUIREMENT, str(HELPER)],
        check=True,
        capture_output=True,
        timeout=30,
    )


def load_identity() -> dict[str, str] | None:
    # Every read of the key first checks who may reach it and what program reads it.
    _private_key_folder()
    _verify_helper()
    result = subprocess.run(
        [str(HELPER), "read", SERVICE, ACCOUNT], capture_output=True, check=False
    )
    if result.returncode == 44:
        return None
    if result.returncode:
        raise RuntimeError("The release Keychain item is unavailable; no key was changed")
    identity = json.loads(result.stdout)
    if set(identity) != {"private_key", "public_key"}:
        raise ValueError("The release Keychain item has an unexpected format")
    return identity


def signing_environment() -> dict[str, str]:
    identity = load_identity()
    if identity is None:
        raise RuntimeError("Prepare the release updater key first")
    if PUBLIC.read_text().strip() != identity["public_key"].strip():
        raise ValueError("The embedded public key disagrees with the retained signing key")
    return {
        **os.environ,
        "TAURI_SIGNING_PRIVATE_KEY": identity["private_key"].strip(),
        "TAURI_SIGNING_PRIVATE_KEY_PASSWORD": "",
        "LOCALSR_UPDATE_PUBLIC_KEY": identity["public_key"].strip(),
    }


def prepare() -> dict[str, str]:
    KEY_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    _private_key_folder()
    if not HELPER.exists():
        subprocess.run(
            [
                "xcrun",
                "swiftc",
                str(ROOT / "packaging/macos/update-keychain.swift"),
                "-o",
                str(HELPER),
            ],
            check=True,
            capture_output=True,
        )
        # Preserve this designated identity: the Keychain creator ACL follows
        # it across helper rebuilds. Never change it after storing the key.
        subprocess.run(
            [
                "codesign",
                "--force",
                "--sign",
                IDENTITY,
                "--identifier",
                "com.localsr.release.update-keychain",
                "--options",
                "runtime",
                "--timestamp",
                str(HELPER),
            ],
            check=True,
            capture_output=True,
            timeout=300,
        )
    identity = load_identity()
    created = identity is None
    if created:
        if PUBLIC.exists():
            raise ValueError("Public key exists without its Keychain item; refusing key rotation")
        with tempfile.TemporaryDirectory(prefix="key-generation-", dir=KEY_ROOT) as temp:
            key = Path(temp) / "updater.key"
            result = subprocess.run(
                [str(TAURI), "signer", "generate", "--ci", "--write-keys", str(key)],
                capture_output=True,
                check=False,
            )
            if result.returncode:
                # CLI output can contain key material. Do not include it in exceptions.
                raise RuntimeError("Updater key generation failed; no key output was logged")
            identity = {
                "private_key": key.read_text(),
                "public_key": key.with_suffix(".key.pub").read_text(),
            }
            result = subprocess.run(
                [str(HELPER), "store", SERVICE, ACCOUNT],
                input=json.dumps(identity).encode(),
                capture_output=True,
                check=False,
            )
            if result.returncode:
                raise RuntimeError("Could not retain the generated updater key in Keychain")
            if load_identity() != identity:
                raise RuntimeError("Keychain round-trip verification failed")
    assert identity is not None
    if PUBLIC.exists() and PUBLIC.read_text().strip() != identity["public_key"].strip():
        raise ValueError("Existing public key differs; refusing to overwrite it")
    PUBLIC.parent.mkdir(parents=True, exist_ok=True)
    PUBLIC.write_text(identity["public_key"].strip() + "\n")
    return {
        "status": "created" if created else "existing key verified",
        "keychain_service": SERVICE,
        "keychain_account": ACCOUNT,
        "public_key_sha256": hashlib.sha256(base64.b64decode(identity["public_key"])).hexdigest(),
        "public_key_file": str(PUBLIC.relative_to(ROOT)),
        "backup": "Independent protected backup remains a release decision",
    }


def sign_artifact(path: Path) -> None:
    """Write <path>.sig with the retained key; the key never reaches a file or an argument."""
    result = subprocess.run(
        [str(TAURI), "signer", "sign", str(path.resolve())],
        env=signing_environment(),
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("Artifact signing failed; signing environment was not logged")


RELEASE_HOST = "https://github.com/HerRei/local-upscale/releases/download/v{version}"
PART_RE = re.compile(r"\.part-(\d{4})$")
CHUNK = 4 * 1024 * 1024


def protocol_version() -> int:
    source = (ROOT / "desktop/src-tauri/src/types.rs").read_text(encoding="utf-8")
    match = re.search(r"pub const PROTOCOL_VERSION: u32 = (\d+);", source)
    if not match:
        raise ValueError("the worker protocol version is missing from types.rs")
    return int(match.group(1))


def _sha256(paths: list[Path]) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    for path in paths:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(CHUNK), b""):
                digest.update(chunk)
                size += len(chunk)
    return digest.hexdigest(), size


def _signed(path: Path, sign: Callable[[Path], None]) -> str:
    signature = path.with_name(path.name + ".sig")
    signature.unlink(missing_ok=True)
    sign(path)
    text = signature.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"signing {path.name} wrote no signature")
    return text


def _regular(path: Path) -> Path:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{path.parent.name} has no regular file {path.name}")
    return path


def _read_json(path: Path) -> dict:
    value = json.loads(_regular(path).read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must hold a JSON object")
    return value


def _package_files(directory: Path, name: str) -> list[Path]:
    """The package, or the 1,900 MiB parts CI keeps of a package over GitHub's limit."""
    whole = directory / name
    parts = sorted(path for path in directory.glob(f"{name}.part-*") if PART_RE.search(path.name))
    if whole.exists() == bool(parts):
        raise ValueError(f"{directory.name} must hold either {name} or its parts")
    if parts and [int(PART_RE.search(p.name).group(1)) for p in parts] != list(
        range(1, len(parts) + 1)
    ):
        raise ValueError(f"{directory.name} is missing a part of {name}")
    return [_regular(path) for path in parts or [whole]]


def _check_target(downloads: Path, target: dict, release_version: str, commit: str) -> dict:
    """Everything about one edition that must hold before anything is signed."""
    directory = downloads / target["id"]
    if not directory.is_dir():
        raise ValueError(f"no downloaded build artifact for {target['id']} in {downloads}")
    files = update_files(target, release_version)
    name = files["update_package"]
    package = _package_files(directory, name)
    part_digests = [_sha256([path]) for path in package] if len(package) > 1 else []
    digest, size = _sha256(package)
    checksum = _regular(directory / f"{name}.sha256")
    if checksum.read_text(encoding="utf-8-sig").split() != [digest, name]:
        raise ValueError(f"{target['id']}: {name} does not match its checksum file")
    smoke = _read_json(directory / f"{name}.smoke.json")
    if (
        smoke.get("passed") is not True
        or smoke.get("worker") != "ready"
        or smoke.get("version") != release_version
        or smoke.get("update") != expected_update_identity(target, commit)
    ):
        raise ValueError(
            f"{target['id']}: the installed smoke report does not show this release's "
            "update identity"
        )
    if smoke.get("commit") != commit:
        raise ValueError(
            f"{target['id']}: the build was taken for commit {smoke.get('commit')!r}, "
            f"not the release commit {commit}"
        )
    manifest_path = _regular(directory / files["engine_manifest"])
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    engine_parts = validate_payload(
        manifest, directory, files["engine_prefix"], files["payload_backend"], files["worker"]
    )
    # Where the smoke test installed the engine manifest (Windows CUDA), the published
    # engine package must be that one, not merely a valid one.
    installed = smoke.get("engine_payload_sha256")
    if (target.get("external_engine") or installed is not None) and installed != hashlib.sha256(
        manifest_bytes
    ).hexdigest():
        raise ValueError(
            f"{target['id']}: the engine payload is not the one the installed package was "
            "tested with"
        )
    return {
        "target": target,
        "directory": directory,
        "files": files,
        "package": package,
        "part_digests": part_digests,
        "digest": (digest, size),
        "manifest_path": manifest_path,
        "manifest_digest": (hashlib.sha256(manifest_bytes).hexdigest(), len(manifest_bytes)),
        "manifest": manifest,
        "engine_parts": engine_parts,
    }


def _copy_range(stream, destination: Path, count: int) -> None:
    with destination.open("xb") as output:
        while count:
            chunk = stream.read(min(CHUNK, count))
            if not chunk:
                raise ValueError(f"{destination.name} ended early")
            output.write(chunk)
            count -= len(chunk)


def _snapshot(sources: list[Path], destination: Path, expected: tuple[str, int]) -> None:
    """A private copy that is exactly the bytes checked before; only copies get signed."""
    with destination.open("xb") as output:
        for source in sources:
            with source.open("rb") as stream:
                shutil.copyfileobj(stream, output, CHUNK)
    if _sha256([destination]) != expected:
        raise ValueError(f"{sources[0].name} changed after it was checked; nothing was published")


def _stated(file: dict) -> dict:
    return {"name": file["name"], "size": file["size"], "sha256": file["sha256"]}


def _statement_bytes(statement: dict) -> bytes:
    return json.dumps(statement, sort_keys=True, separators=(",", ":")).encode()


def release_statement(release_version: str, key: str, contract: dict) -> bytes:
    """Format 1, kept unchanged for clients before 0.3.8, which accept only these exact
    fields. What the app verifies is release_statement_v2 (desktop/src-tauri/src/updates.rs)."""
    engine = contract.get("engine")
    return _statement_bytes(
        {
            "schema_version": 1,
            "version": release_version,
            "channel": contract["channel"],
            "target": key,
            "backend": contract["backend"],
            "kind": contract["kind"],
            "protocol": contract["protocol"],
            "engine_id": contract["engine_id"],
            "unpacked_size": contract["unpacked_size"],
            "sha256": contract["sha256"],
            "engine_manifest_sha256": engine["manifest"]["sha256"] if engine else None,
        }
    )


def release_statement_v2(release_version: str, key: str, contract: dict) -> bytes:
    """Format 2: format 1 plus everything the app would otherwise learn only from the
    unsigned feed before it downloads: the package size, and name, size and digest of every
    application part and of the engine manifest and parts."""
    engine = contract.get("engine")
    return _statement_bytes(
        {
            "schema_version": 2,
            "version": release_version,
            "channel": contract["channel"],
            "target": key,
            "backend": contract["backend"],
            "kind": contract["kind"],
            "protocol": contract["protocol"],
            "engine_id": contract["engine_id"],
            "size": contract["size"],
            "unpacked_size": contract["unpacked_size"],
            "sha256": contract["sha256"],
            "parts": [_stated(part) for part in contract.get("parts") or []],
            "engine_manifest": _stated(engine["manifest"]) if engine else None,
            "engine_parts": [_stated(part) for part in (engine or {}).get("parts", [])],
        }
    )


def _sign_statement(
    contract: dict, release_version: str, key: str, workspace: Path, sign: Callable[[Path], None]
) -> None:
    for data, name, field in (
        (release_statement(release_version, key, contract), "statement", "statement"),
        (release_statement_v2(release_version, key, contract), "statement-v2", "statement_v2"),
    ):
        path = workspace / f"{key}.{name}.json"
        path.write_bytes(data)
        contract[field] = base64.b64encode(data).decode()
        contract[f"{field}_signature"] = _signed(path, sign)
        path.with_name(path.name + ".sig").unlink()
        path.unlink()


def platform_entries(
    downloads: Path,
    release_version: str,
    commit: str,
    *,
    host: str,
    sign: Callable[[Path], None],
) -> tuple[dict[str, dict], list[Path]]:
    """Check every Windows and Linux edition, then sign copies of exactly those bytes."""
    protocol = protocol_version()
    checked = [
        _check_target(downloads, target, release_version, commit)
        for target in targets()
        if target["platform"] != "macos"
    ]
    entries: dict[str, dict] = {}
    signatures: list[Path] = []

    def download(name: str, known: tuple[str, int], signature: str) -> dict[str, object]:
        return {
            "name": name,
            "url": f"{host}/{name}",
            "size": known[1],
            "sha256": known[0],
            "signature": signature,
        }

    with tempfile.TemporaryDirectory(prefix="localsr-signing-") as temporary:
        workspace = Path(temporary)

        def sign_copy(copy: Path, directory: Path) -> str:
            signature = _signed(copy, sign)
            published = directory / f"{copy.name}.sig"
            copy.with_name(copy.name + ".sig").replace(published)
            signatures.append(published)
            copy.unlink()
            return signature

        for item in checked:
            target, directory, files = item["target"], item["directory"], item["files"]
            name = files["update_package"]
            # The whole package, joined from its parts if it has them; the part copies
            # are cut from that checked copy, so every signature covers checked bytes.
            whole = workspace / name
            _snapshot(item["package"], whole, item["digest"])
            parts = []
            if item["part_digests"]:
                with whole.open("rb") as stream:
                    for path, known in zip(item["package"], item["part_digests"], strict=True):
                        piece = workspace / path.name
                        _copy_range(stream, piece, known[1])
                        if _sha256([piece]) != known:
                            raise ValueError(f"{path.name} changed after it was checked")
                        parts.append(download(path.name, known, sign_copy(piece, directory)))
            whole_signature = sign_copy(whole, directory)
            manifest_copy = workspace / item["manifest_path"].name
            _snapshot([item["manifest_path"]], manifest_copy, item["manifest_digest"])
            engine_files = [
                download(
                    item["manifest_path"].name,
                    item["manifest_digest"],
                    sign_copy(manifest_copy, directory),
                )
            ]
            for path, part in zip(item["engine_parts"], item["manifest"]["parts"], strict=True):
                known = (part["sha256"], part["size"])
                copy = workspace / path.name
                _snapshot([path], copy, known)
                engine_files.append(download(path.name, known, sign_copy(copy, directory)))
            identity = engine_id(target, commit)
            engine_unpacked = item["manifest"]["unpacked_bytes"]
            # Room to install: the package itself (an NSIS installer unpacks its bundled
            # engine as well) and the versioned engine the updater installs next to it.
            bundled = target["platform"] == "windows" and not target.get("external_engine")
            size = item["digest"][1]
            contract: dict[str, object] = {
                "channel": "beta" if "-" in release_version else "stable",
                "backend": target["update"]["backend"],
                "kind": target["update"]["kind"],
                "protocol": protocol,
                "engine_id": identity,
                "size": size,
                "unpacked_size": size + engine_unpacked * (2 if bundled else 1),
                "sha256": item["digest"][0],
            }
            if parts:
                contract["parts"] = parts
            contract["engine"] = {
                "id": identity,
                "manifest": engine_files[0],
                "parts": engine_files[1:],
            }
            key = expected_update_identity(target, commit)["target"]
            _sign_statement(contract, release_version, key, workspace, sign)
            entries[key] = {
                "url": f"{host}/{(parts[0]['name'] if parts else name)}",
                "signature": whole_signature,
                "localsr": contract,
            }
    return entries, signatures


def _write_feed(feed: Path, data: dict) -> None:
    temporary = feed.with_name(feed.name + ".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    temporary.replace(feed)


def add_platforms(
    downloads: Path,
    feed: Path,
    commit: str,
    *,
    release_version: str | None = None,
    host: str | None = None,
    sign: Callable[[Path], None] = sign_artifact,
) -> list[Path]:
    """Add the Windows and Linux entries to the Mac build's feed; returns the signatures."""
    release_version = release_version or version()
    if not feed.is_file():
        raise ValueError(f"{feed} is missing: run release_macos_beta.sh for this release first")
    data = json.loads(feed.read_text(encoding="utf-8"))
    if data.get("version") != release_version or "darwin-aarch64-mps-native" not in data.get(
        "platforms", {}
    ):
        raise ValueError(f"{feed} is not the macOS feed of version {release_version}")
    entries, signatures = platform_entries(
        downloads,
        release_version,
        commit,
        host=host or RELEASE_HOST.format(version=release_version),
        sign=sign,
    )
    data["platforms"].update(entries)
    _write_feed(feed, data)
    return signatures


def add_statements(feed: Path, sign: Callable[[Path], None] = sign_artifact) -> list[str]:
    """Sign both release statements of every feed entry that lacks either; returns their keys."""
    data = json.loads(feed.read_text(encoding="utf-8"))
    added = []
    with tempfile.TemporaryDirectory(prefix="localsr-signing-") as temporary:
        for key, entry in data["platforms"].items():
            contract = entry["localsr"]
            if not all(
                contract.get(field)
                for field in (
                    "statement",
                    "statement_signature",
                    "statement_v2",
                    "statement_v2_signature",
                )
            ):
                digest = ((contract.get("engine") or {}).get("manifest") or {}).get("sha256")
                if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
                    raise ValueError(f"{key} has no engine package to sign a statement for")
                _sign_statement(contract, data["version"], key, Path(temporary), sign)
                added.append(key)
    _write_feed(feed, data)
    return added


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("action", choices=("prepare", "sign", "platforms", "statement"))
    parser.add_argument("artifact", type=Path, nargs="?")
    parser.add_argument("--feed", type=Path, help="platforms: the Mac build's beta.json")
    parser.add_argument("--commit", help="platforms: the release commit every build came from")
    parser.add_argument("--host", help="platforms: download base URL (default: the GitHub release)")
    args = parser.parse_args()
    if args.action == "prepare":
        print(json.dumps(prepare(), indent=2))
        return
    if args.action == "platforms":
        if args.artifact is None or not args.artifact.is_dir() or not args.feed or not args.commit:
            parser.error("platforms requires the downloads folder, --feed and --commit")
        signatures = add_platforms(args.artifact, args.feed, args.commit, host=args.host)
        print(
            json.dumps(
                {
                    "feed": str(args.feed),
                    "upload": [str(path) for path in signatures],
                },
                indent=2,
            )
        )
        return
    if args.artifact is None or not args.artifact.is_file():
        parser.error(f"{args.action} requires an existing file")
    if args.action == "statement":
        print(json.dumps({"feed": str(args.artifact), "signed": add_statements(args.artifact)}))
        return
    sign_artifact(args.artifact)
    print(json.dumps({"artifact": str(args.artifact), "signature": str(args.artifact) + ".sig"}))


if __name__ == "__main__":
    main()
