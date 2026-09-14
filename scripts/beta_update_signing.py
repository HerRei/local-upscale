#!/usr/bin/env python3
"""Keep the direct-edition updater key in the local macOS Keychain.

Prepare once; sign immutable artifacts without exporting the retained private
key to a repository or passing it on a command line. This does not publish feeds.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "sign"))
    parser.add_argument("artifact", type=Path, nargs="?")
    args = parser.parse_args()
    if args.action == "prepare":
        print(json.dumps(prepare(), indent=2))
        return
    if args.artifact is None or not args.artifact.is_file():
        parser.error("sign requires an existing artifact")
    result = subprocess.run(
        [str(TAURI), "signer", "sign", str(args.artifact.resolve())],
        env=signing_environment(),
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("Artifact signing failed; signing environment was not logged")
    print(json.dumps({"artifact": str(args.artifact), "signature": str(args.artifact) + ".sig"}))


if __name__ == "__main__":
    main()
