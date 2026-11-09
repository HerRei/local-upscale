#!/usr/bin/env python3
"""Generate and validate the complete desktop release matrix from one registry."""

from __future__ import annotations

import argparse
import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "ci" / "tauri-targets.json"
PUBLIC_KEY = ROOT / "packaging" / "updates" / "production.pub"
# UpdateConfig::target() in desktop/src-tauri/src/updates.rs builds the same key from
# std::env::consts: <os>-<arch>-<backend>-<kind>, with "darwin" for macOS.
UPDATE_OS = {"macos": "darwin", "windows": "windows", "linux": "linux"}
UPDATE_ARCH = {"arm64": "aarch64", "x86_64": "x86_64"}
UPDATE_KINDS = {"native", "portable"}
COMMIT_RE = re.compile(r"[0-9a-f]{40}")


def _valid_update(target: dict) -> bool:
    update = target.get("update")
    return (
        isinstance(update, dict)
        and set(update) == {"backend", "kind"}
        and isinstance(update["backend"], str)
        and re.fullmatch(r"[a-z0-9]+", update["backend"]) is not None
        and update["kind"] in UPDATE_KINDS
        and target.get("platform") in UPDATE_OS
        and target.get("architecture") in UPDATE_ARCH
    )


def targets() -> list[dict]:
    data = json.loads(REGISTRY.read_text())
    entries = data["targets"]
    if data.get("schema_version") != 1 or len({x["id"] for x in entries}) != len(entries):
        raise ValueError("invalid or duplicate release target IDs")
    for entry in entries:
        if not _valid_update(entry):
            raise ValueError(f"{entry['id']} has no valid update identity (backend, kind)")
    keys = [platform_key(entry) for entry in entries]
    if len(set(keys)) != len(keys):
        raise ValueError("two release targets share an update platform key")
    return entries


def target(target_id: str) -> dict:
    for entry in targets():
        if entry["id"] == target_id:
            return entry
    raise ValueError(f"unknown release target {target_id!r}")


def platform_key(entry: dict) -> str:
    """The feed's platform key for this edition, as the installed app looks it up."""
    update = entry["update"]
    return "-".join(
        (
            UPDATE_OS[entry["platform"]],
            UPDATE_ARCH[entry["architecture"]],
            update["backend"],
            update["kind"],
        )
    )


def engine_id(entry: dict, commit: str) -> str:
    """The bundled engine's identity: one per edition and release commit, as on macOS."""
    if not COMMIT_RE.fullmatch(commit):
        raise ValueError(f"release commit must be a full lowercase SHA, not {commit!r}")
    return f"{entry['id']}-{commit[:12]}"


def expected_update_identity(entry: dict, commit: str) -> dict[str, object]:
    """What the installed host's headless smoke report says about its update identity."""
    return {
        "enabled": True,
        "target": platform_key(entry),
        "backend": entry["update"]["backend"],
        "kind": entry["update"]["kind"],
        "engine_id": engine_id(entry, commit),
    }


def update_environment(target_id: str, commit: str, root: Path = ROOT) -> dict[str, str]:
    """The compile-time variables that give a build its updater key and feed target."""
    entry = target(target_id)
    key = (root / PUBLIC_KEY.relative_to(ROOT)).read_text(encoding="utf-8").strip()
    if not key or any(character.isspace() for character in key):
        raise ValueError("the updater public key must be one base64 line")
    return {
        "LOCALSR_UPDATE_PUBLIC_KEY": key,
        "LOCALSR_UPDATE_BACKEND": entry["update"]["backend"],
        "LOCALSR_UPDATE_KIND": entry["update"]["kind"],
        "LOCALSR_ENGINE_ID": engine_id(entry, commit),
    }


def update_files(entry: dict, release_version: str) -> dict[str, str]:
    """Public names of an edition's package, its updater file and its engine package."""
    package = f"LocalSR-v{release_version}-{entry['suffix']}"
    stem = package.rsplit(".", 1)[0]
    return {
        "package": package,
        # macOS updates from the app archive; NSIS installers and AppImages update themselves.
        "update_package": f"{stem}.app.tar.gz" if entry["platform"] == "macos" else package,
        "engine_prefix": f"{stem}.engine.tar.gz",
        "engine_manifest": f"{stem}.engine-payload.json",
        "payload_backend": entry["update"]["backend"].upper(),
        "worker": "localsr-worker.exe" if entry["platform"] == "windows" else "localsr-worker",
    }


def version(root: Path = ROOT) -> str:
    with (root / "pyproject.toml").open("rb") as stream:
        return tomllib.load(stream)["project"]["version"]


def artifact_entries(release_version: str) -> list[dict]:
    return [
        {
            "id": target["id"],
            "filename": f"LocalSR-v{release_version}-{target['suffix']}",
            "platform": target["platform"],
            "architecture": target["architecture"],
            "backend": target["backend"],
            "signing": target["signing"],
            "update_target": platform_key(target),
            **({"external_engine": True} if target.get("external_engine") else {}),
        }
        for target in targets()
    ]


def validate_manifest(manifest: dict) -> None:
    entries = manifest.get("artifacts")
    if not isinstance(entries, list) or not entries:
        raise ValueError("release manifest has no targets")
    expected = artifact_entries(str(manifest["version"]))
    if entries != expected:
        raise ValueError("release manifest must match every target in ci/tauri-targets.json")


def matrix(platform: str, release_version: str) -> dict:
    return {
        "include": [
            target | {"pkg_name": f"LocalSR-v{release_version}-{target['suffix']}"}
            for target in targets()
            if target["platform"] == platform
        ]
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-manifests", action="store_true")
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--platform", choices=("windows", "linux", "macos"))
    parser.add_argument(
        "--update-env",
        metavar="TARGET",
        help="Print the build identity of TARGET as NAME=VALUE lines for $GITHUB_ENV",
    )
    parser.add_argument("--commit", help="Release commit for --update-env")
    args = parser.parse_args()
    if args.update_env:
        if not args.commit:
            parser.error("--update-env requires --commit")
        for name, value in update_environment(args.update_env, args.commit).items():
            print(f"{name}={value}")
        return
    current = version()
    if args.write_manifests:
        data = {
            "schema_version": 1,
            "version": current,
            "artifacts": artifact_entries(current),
        }
        (ROOT / "ci" / "tauri-release-artifacts.json").write_text(json.dumps(data, indent=2) + "\n")
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as stream:
            for platform in ("linux", "windows"):
                stream.write(f"{platform}={json.dumps(matrix(platform, current))}\n")
            stream.write(f"version={current}\n")
    if args.platform:
        print(json.dumps(matrix(args.platform, current)))


if __name__ == "__main__":
    main()
