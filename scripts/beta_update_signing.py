#!/usr/bin/env python3
"""Keep the direct-edition updater key in the local macOS Keychain.

Prepare once; sign immutable artifacts without exporting the retained private
key to a repository or passing it on a command line. This does not publish feeds.

`platforms DOWNLOADS --feed build/release-<version>/beta.json --commit <sha>` takes
the Windows and Linux build artifacts as `gh run download` leaves them (one folder
per release target), checks each package against its checksum, its installed smoke
report and its engine payload, signs the updater files and adds their feed entries
next to the macOS entry that release_macos_beta.sh wrote.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from release_targets import engine_id, expected_update_identity, targets, update_files, version

ROOT = Path(__file__).resolve().parents[1]
KEY_ROOT = Path.home() / ".local/share/localsr-release-signing"
HELPER = KEY_ROOT / "update-keychain"
SERVICE = "com.localsr.release.updater"
ACCOUNT = "production-v1"
IDENTITY = "Developer ID Application: Hermes Reisner (Z2TU844D84)"
PUBLIC = ROOT / "packaging/updates/production.pub"
TAURI = ROOT / "desktop/node_modules/.bin/tauri"


def load_identity() -> dict[str, str] | None:
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
    if KEY_ROOT.is_symlink() or KEY_ROOT.stat().st_mode & 0o077:
        raise ValueError("The release signing directory must be private (mode 0700)")
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
    subprocess.run(
        [
            "codesign",
            "--verify",
            "--strict",
            "-R",
            '=identifier "com.localsr.release.update-keychain" and anchor apple generic '
            'and certificate leaf[subject.OU] = "Z2TU844D84"',
            str(HELPER),
        ],
        check=True,
        capture_output=True,
        timeout=30,
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
MAX_ASSET_BYTES = 2 * 1024**3
PART_RE = re.compile(r"\.part-(\d{4})$")


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
            for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
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


def _read_json(path: Path) -> dict:
    if not path.is_file():
        raise ValueError(f"{path.parent.name} has no {path.name}")
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must hold a JSON object")
    return value


def _package_files(directory: Path, name: str) -> list[Path]:
    """The package, or the 1,900 MiB parts CI keeps of a package over GitHub's limit."""
    whole = directory / name
    parts = sorted(path for path in directory.glob(f"{name}.part-*") if PART_RE.search(path.name))
    if whole.is_file() == bool(parts):
        raise ValueError(f"{directory.name} must hold either {name} or its parts")
    if parts and [int(PART_RE.search(p.name).group(1)) for p in parts] != list(
        range(1, len(parts) + 1)
    ):
        raise ValueError(f"{directory.name} is missing a part of {name}")
    return parts or [whole]


def _engine_payload(directory: Path, files: dict[str, str]) -> tuple[Path, dict, list[Path]]:
    manifest_path = directory / files["engine_manifest"]
    if not manifest_path.is_file():
        raise ValueError(f"{directory.name} has no engine payload {manifest_path.name}")
    manifest = _read_json(manifest_path)
    parts = manifest.get("parts")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("format") != "tar.gz.parts"
        or manifest.get("backend") != files["payload_backend"]
        or not isinstance(manifest.get("unpacked_bytes"), int)
        or manifest["unpacked_bytes"] <= 0
        or not isinstance(parts, list)
        or not parts
    ):
        raise ValueError(f"{directory.name} has an invalid engine payload manifest")
    paths = []
    for part in parts:
        path = directory / str(part.get("filename", ""))
        if (
            not path.name.startswith(files["engine_prefix"] + ".part-")
            or not path.is_file()
            or not 0 < part.get("size", 0) < MAX_ASSET_BYTES
            or _sha256([path]) != (part.get("sha256"), part.get("size"))
        ):
            raise ValueError(f"{directory.name}: {path.name} does not match its engine payload")
        paths.append(path)
    return manifest_path, manifest, paths


def platform_entries(
    downloads: Path,
    release_version: str,
    commit: str,
    *,
    host: str,
    sign: Callable[[Path], None],
) -> tuple[dict[str, dict], list[Path]]:
    """Check, sign and describe every Windows and Linux edition's updater files."""
    protocol = protocol_version()
    checked = []
    for target in targets():
        if target["platform"] == "macos":
            continue
        directory = downloads / target["id"]
        if not directory.is_dir():
            raise ValueError(f"no downloaded build artifact for {target['id']} in {downloads}")
        files = update_files(target, release_version)
        name = files["update_package"]
        package = _package_files(directory, name)
        digest, size = _sha256(package)
        checksum_path = directory / f"{name}.sha256"
        if not checksum_path.is_file():
            raise ValueError(f"{target['id']} has no {checksum_path.name}")
        if checksum_path.read_text(encoding="utf-8-sig").split() != [digest, name]:
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
        checked.append(
            (target, directory, files, package, digest, size, _engine_payload(directory, files))
        )

    entries: dict[str, dict] = {}
    signatures: list[Path] = []

    def download(path: Path, signature: str, known: dict | None = None) -> dict[str, object]:
        if known is None:
            file_digest, file_size = _sha256([path])
        else:
            file_digest, file_size = known["sha256"], known["size"]
        return {
            "name": path.name,
            "url": f"{host}/{path.name}",
            "size": file_size,
            "sha256": file_digest,
            "signature": signature,
        }

    def signed(path: Path) -> str:
        signature = _signed(path, sign)
        signatures.append(path.with_name(path.name + ".sig"))
        return signature

    for target, directory, files, package, digest, size, engine in checked:
        name = files["update_package"]
        if len(package) == 1:
            whole_signature = signed(package[0])
            parts = []
        else:
            # Sign the whole AppImage, which the app verifies after joining the parts. It
            # exists here only for signing: GitHub cannot host a file this large.
            with tempfile.TemporaryDirectory(prefix=".localsr-signing-", dir=directory) as temp:
                joined = Path(temp) / name
                with joined.open("wb") as output:
                    for part in package:
                        with part.open("rb") as stream:
                            for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
                                output.write(chunk)
                if _sha256([joined]) != (digest, size):
                    raise ValueError(f"{target['id']}: the parts changed while they were read")
                whole_signature = _signed(joined, sign)
                whole_sig = directory / f"{name}.sig"
                joined.with_name(joined.name + ".sig").replace(whole_sig)
                signatures.append(whole_sig)
            parts = [download(part, signed(part)) for part in package]
        manifest_path, manifest, engine_parts = engine
        identity = engine_id(target, commit)
        engine_unpacked = manifest["unpacked_bytes"]
        # Room to install: the package itself (an NSIS installer unpacks its bundled
        # engine as well) and the versioned engine the updater installs next to it.
        bundled = target["platform"] == "windows" and not target.get("external_engine")
        unpacked = size + engine_unpacked * (2 if bundled else 1)
        contract: dict[str, object] = {
            "channel": "beta",
            "backend": target["update"]["backend"],
            "kind": target["update"]["kind"],
            "protocol": protocol,
            "engine_id": identity,
            "size": size,
            "unpacked_size": unpacked,
            "sha256": digest,
        }
        if parts:
            contract["parts"] = parts
        contract["engine"] = {
            "id": identity,
            "manifest": download(manifest_path, signed(manifest_path)),
            "parts": [
                download(path, signed(path), known)
                for path, known in zip(engine_parts, manifest["parts"], strict=True)
            ],
        }
        entries[expected_update_identity(target, commit)["target"]] = {
            "url": f"{host}/{(parts[0]['name'] if parts else name)}",
            "signature": whole_signature,
            "localsr": contract,
        }
    return entries, signatures


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
    temporary = feed.with_name(feed.name + ".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    temporary.replace(feed)
    return signatures


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("action", choices=("prepare", "sign", "platforms"))
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
        parser.error("sign requires an existing artifact")
    sign_artifact(args.artifact)
    print(json.dumps({"artifact": str(args.artifact), "signature": str(args.artifact) + ".sig"}))


if __name__ == "__main__":
    main()
