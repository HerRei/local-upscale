#!/usr/bin/env python3
"""Update from older releases with their own code, before a release ships.

The 0.5.2 incident (docs/postmortems/2026-11-23-updates-from-0.5.0.md): every 0.5.0
install refused the in-app update to 0.5.2, because the feed named an engine manifest
0.5.0 cannot read, and a Windows CUDA host since 0.5.2 ignores the engine 0.5.0 installs.
No test had run an older release's update code. This check does:

1. Package this release's engine for one edition as a release does, and sign the files and
   a feed entry with a disposable key (nothing here is published).
2. For every older release (--from): check out its tag, build its update code with a probe
   test injected (scripts/upgrade_check/old_release_probe.rs) and run it on those files:
   the contract check, the signed release statement and the engine package installation.
3. This release's host must then record the engine that updater installed and start it
   (a real handshake), as it does at its first start after such an update.

Example (macOS, from the engine of a built app):

    python scripts/upgrade_check.py --target macos-arm64-mps --version 0.6.0-beta \\
        --engine path/to/LocalSR.app/Contents/Resources/engine
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from beta_update_signing import RELEASE_HOST, _sha256, _sign_statement
from disposable_signing import SigningKey
from prepare_engine_payload import prepare_for_target
from release_targets import (
    engine_id,
    expected_update_identity,
    protocol_version,
    target,
    update_files,
    version,
)

ROOT = Path(__file__).resolve().parents[1]
PROBE = Path(__file__).resolve().parent / "upgrade_check" / "old_release_probe.rs"
# Releases whose updaters are in use and share the probe's API. Add each new release.
OLDER_RELEASES = ("v0.4.0-beta", "v0.5.0-beta", "v0.5.2-beta", "v0.6.0-beta", "v0.6.1-beta")
NEW_HOST_TEST = (
    "updates::tests::"
    "upgrade_check_the_new_host_records_and_starts_the_engine_an_older_updater_installed"
)


def _run(command: list[str], *, cwd: Path | None = None, env: dict | None = None) -> str:
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, check=False)
    output = result.stdout + result.stderr
    if result.returncode:
        raise RuntimeError(
            f"{' '.join(command[:4])} failed ({result.returncode}):\n{output[-6000:]}"
        )
    return output


def candidate_feed(
    target_id: str,
    release_version: str,
    work: Path,
    key: SigningKey,
    *,
    engine: Path | None,
    payload: Path | None,
    as_in_052: bool = False,
) -> tuple[Path, Path, dict]:
    """The candidate's engine package and a signed feed entry for it, as a release has them.

    Returns the feed, the folder of the files the old updater reads, and the contract.
    """
    entry = target(target_id)
    commit = _run(["git", "rev-parse", "HEAD"], cwd=ROOT).strip()
    files = update_files(entry, release_version)
    folder = work / "candidate"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    if payload is not None:
        # Links to the release's files in a folder of its own: the disposable signatures
        # must never land beside, or replace, the release's real ones.
        listed = json.loads(payload.read_bytes())["parts"]
        shutil.copyfile(payload, folder / payload.name)  # rewritten below: never a link
        for source in (payload.parent / part["filename"] for part in listed):
            try:
                os.link(source, folder / source.name)
            except OSError:
                shutil.copyfile(source, folder / source.name)
        prepare_for_target(
            target_id,
            folder,
            reuse=folder / payload.name,
            release_version=release_version,
            commit=commit,
        )
    else:
        prepare_for_target(
            target_id, folder, engine=engine, release_version=release_version, commit=commit
        )
    manifest = json.loads((folder / files["engine_manifest"]).read_bytes())
    update = folder / files["engine_update_manifest"]
    parts = [folder / part["filename"] for part in manifest["parts"]]
    host = RELEASE_HOST.format(version=release_version)

    def download(path: Path) -> dict:
        key.sign_file(path)
        digest, size = _sha256([path])
        signature = path.with_name(path.name + ".sig").read_text(encoding="utf-8")
        return {
            "name": path.name,
            "url": f"{host}/{path.name}",
            "size": size,
            "sha256": digest,
            "signature": signature,
        }

    package = work / files["update_package"]
    package.write_bytes(b"stands in for the application package")
    identity = engine_id(entry, commit)
    contract: dict = {
        "channel": "beta" if "-" in release_version else "stable",
        "backend": entry["update"]["backend"],
        "kind": entry["update"]["kind"],
        "protocol": protocol_version(),
        "engine_id": identity,
        "size": package.stat().st_size,
        "unpacked_size": package.stat().st_size + 2 * manifest["unpacked_bytes"],
        "sha256": _sha256([package])[0],
        "engine": {
            "id": identity,
            # --as-in-0.5.2 names the full manifest, as the 0.5.2 feed did: the check must fail.
            "manifest": download(folder / files["engine_manifest"] if as_in_052 else update),
            "parts": [download(part) for part in parts],
        },
    }
    feed_key = expected_update_identity(entry, commit)["target"]
    statements = work / "statements"
    statements.mkdir(exist_ok=True)
    _sign_statement(contract, release_version, feed_key, statements, key.sign_file)
    feed = work / "feed.json"
    feed.write_text(
        json.dumps(
            {
                "version": release_version,
                "platforms": {
                    feed_key: {
                        "url": f"{host}/{package.name}",
                        "signature": key.signature(package.read_bytes(), package.name),
                        "localsr": contract,
                    }
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return feed, folder, contract | {"feed_key": feed_key}


def _checkout(tag: str, destination: Path) -> None:
    if subprocess.run(
        ["git", "rev-parse", "-q", "--verify", f"refs/tags/{tag}"], cwd=ROOT, capture_output=True
    ).returncode:
        _run(["git", "fetch", "--depth", "1", "origin", "tag", tag, "--no-tags"], cwd=ROOT)
    if destination.exists():
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(destination)],
            cwd=ROOT,
            capture_output=True,
        )
        shutil.rmtree(destination, ignore_errors=True)
    _run(["git", "worktree", "add", "--detach", "--force", str(destination), tag], cwd=ROOT)


def check_from(
    tag: str,
    feed: Path,
    files: Path,
    contract: dict,
    key: SigningKey,
    work: Path,
    cargo_target: Path,
) -> list[str]:
    """Update from release `tag` with its own code; returns the problems found."""
    source = work / f"source-{tag}"
    profile = work / f"from-{tag}"
    shutil.rmtree(profile, ignore_errors=True)
    next_root = profile / "LocalSR" / "next"
    next_root.mkdir(parents=True)
    _checkout(tag, source)
    try:
        crate = source / "desktop" / "src-tauri"
        shutil.copyfile(PROBE, crate / "src" / "upgrade_probe.rs")
        with (crate / "src" / "updates.rs").open("a", encoding="utf-8") as updates:
            updates.write('\n#[cfg(test)]\n#[path = "upgrade_probe.rs"]\nmod upgrade_probe;\n')
        # The app's build script embeds the frontend; the probe does not need a real one.
        dist = source / "desktop" / "dist"
        dist.mkdir(exist_ok=True)
        (dist / "index.html").write_text("<!doctype html><title>probe</title>\n")
        env = os.environ | {
            "CARGO_TARGET_DIR": str(cargo_target),
            "LSR_PROBE_FEED": str(feed),
            "LSR_PROBE_KEY": contract["feed_key"],
            "LSR_PROBE_PUBLIC_KEY": key.public_key,
            "LSR_PROBE_INSTALLED": tag.removeprefix("v"),
            "LSR_PROBE_NEXT_ROOT": str(next_root),
            "LSR_PROBE_FILES": str(files),
        }
        try:
            output = _run(
                [
                    "cargo",
                    "test",
                    "--manifest-path",
                    str(crate / "Cargo.toml"),
                    "--lib",
                    "upgrade_probe",
                    "--",
                    "--nocapture",
                    "--test-threads",
                    "1",
                ],
                env=env,
            )
        except RuntimeError as error:
            failed = [line for line in str(error).splitlines() if "PROBE FAILED" in line]
            return [f"{tag}'s updater: {failed[0] if failed else str(error)[-2000:]}"]
        if "LSR_PROBE_WORKER=" not in output:
            return [f"{tag}'s updater: the probe did not run (no LSR_PROBE_WORKER line)"]
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(source)], cwd=ROOT, capture_output=True
        )
    # This release's host, at its first start after that update.
    try:
        output = _run(
            [
                "cargo",
                "test",
                "--manifest-path",
                str(ROOT / "desktop/src-tauri/Cargo.toml"),
                "--lib",
                "--",
                "--ignored",
                "--exact",
                NEW_HOST_TEST,
                "--nocapture",
            ],
            env=os.environ
            | {
                "LSR_PROBE_ROOT": str(profile),
                "LSR_PROBE_ENGINE_ID": contract["engine_id"],
                "LSR_PROBE_BACKEND": contract["backend"],
            },
        )
    except RuntimeError as error:
        failed = [line for line in str(error).splitlines() if "PROBE FAILED" in line]
        return [f"new host after {tag}'s update: {failed[0] if failed else str(error)[-2000:]}"]
    if "LSR_PROBE_HOST=" not in output:
        return [f"new host after {tag}'s update: the check did not run"]
    return []


def _older(tag: str, candidate: str) -> bool:
    """Whether release `tag` is older than `candidate` (a beta sorts before its release)."""

    def key(text: str) -> tuple:
        core, _, pre = text.removeprefix("v").partition("-")
        return (*(int(part) for part in core.split(".")), 0 if pre else 1, pre)

    return key(tag) < key(candidate)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--target", required=True, help="Release target, e.g. windows-x86_64-cuda")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--engine", type=Path, help="Frozen engine directory to package")
    source.add_argument(
        "--payload",
        type=Path,
        help="An engine-payload.json with its parts beside it, or their folder (CI)",
    )
    parser.add_argument("--version", help="Version of the candidate (default: pyproject)")
    parser.add_argument("--from", dest="tags", action="append", help="Older release tag")
    parser.add_argument("--work", type=Path, default=ROOT / "build" / "upgrade-check")
    parser.add_argument("--cargo-target", type=Path, help="Target folder for the old builds")
    parser.add_argument(
        "--as-in-0.5.2",
        dest="as_in_052",
        action="store_true",
        help="Build the feed as 0.5.2 did (the incident); the check must then fail",
    )
    args = parser.parse_args()
    release_version = args.version or version()
    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    cargo_target = (args.cargo_target or work / "cargo-target").resolve()
    if args.payload is not None and args.payload.is_dir():
        names = update_files(target(args.target), release_version)
        args.payload = args.payload / names["engine_manifest"]
    key = SigningKey()
    feed, files, contract = candidate_feed(
        args.target,
        release_version,
        work,
        key,
        engine=args.engine,
        payload=args.payload,
        as_in_052=args.as_in_052,
    )
    problems: list[str] = []
    for tag in args.tags or OLDER_RELEASES:
        if not _older(tag, release_version):
            print(f"skip update {tag} -> {release_version}: not an older release")
            continue
        found = check_from(tag, feed, files, contract, key, work, cargo_target)
        print(f"{'FAIL' if found else 'ok  '} update {tag} -> {release_version} ({args.target})")
        for problem in found:
            print(f"     {problem}")
        problems += found
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
