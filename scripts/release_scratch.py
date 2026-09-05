#!/usr/bin/env python3
"""Cross-platform bounded scratch retention for native release jobs."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT_MARKER = ".localsr-release-root"
RUN_MARKER = ".localsr-release-run.json"
RETAINED_MARKER = ".retained.json"
COMPONENT = re.compile(r"^[A-Za-z0-9._-]+$")
ALLOWED_ROOT_NAMES = {"ci-scratch", "lsr-ci", "CISCRATCH"}


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


def atomic_json(path: Path, value: object) -> None:
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def managed_root(path: Path, *, initialize: bool = False) -> Path:
    root = path.resolve()
    if root == Path(root.anchor) or root.name not in ALLOWED_ROOT_NAMES:
        raise ValueError(f"refusing unmanaged release scratch root: {root}")
    if initialize:
        root.mkdir(parents=True, exist_ok=True)
        marker = root / ROOT_MARKER
        if not marker.exists():
            atomic_json(marker, {"schema_version": 1, "created_at": timestamp()})
    if not (root / ROOT_MARKER).is_file():
        raise ValueError(f"release scratch root is not initialized: {root}")
    return root


def safe_component(value: str, label: str) -> str:
    if value in {"", ".", ".."} or COMPONENT.fullmatch(value) is None:
        raise ValueError(f"unsafe {label}: {value!r}")
    return value


def run_directory(
    root: Path,
    run_id: str,
    attempt: str,
    platform: str,
    flavor: str,
) -> Path:
    managed = managed_root(root)
    components = (
        safe_component(run_id, "run id"),
        safe_component(attempt, "attempt"),
        safe_component(platform[0], "platform"),
        safe_component(flavor.split("-")[-1], "flavor"),
    )
    target = managed.joinpath("r", *components).resolve()
    if not target.is_relative_to(managed / "r"):
        raise ValueError(f"release scratch escaped its managed root: {target}")
    return target


def start(
    root: Path,
    run_id: str,
    attempt: str,
    platform: str,
    flavor: str,
) -> Path:
    managed_root(root, initialize=True)
    target = run_directory(root, run_id, attempt, platform, flavor)
    if target.exists():
        raise FileExistsError(f"release scratch already exists: {target}")
    for name in ("tmp", "staging", "environment", "build"):
        (target / name).mkdir(parents=True, exist_ok=True)
    atomic_json(
        target / RUN_MARKER,
        {
            "schema_version": 1,
            "created_at": timestamp(),
            "run_id": run_id,
            "attempt": attempt,
            "platform": platform,
            "flavor": flavor,
        },
    )
    return target


def validate_existing_target(target: Path, root: Path) -> Path:
    managed = managed_root(root)
    resolved = target.resolve()
    if not resolved.is_relative_to(managed / "r"):
        raise ValueError(f"refusing release scratch target outside managed runs: {resolved}")
    if resolved.is_symlink() or not (resolved / RUN_MARKER).is_file():
        raise ValueError(f"release scratch target has no ownership marker: {resolved}")
    return resolved


def remove_secrets(target: Path, secrets: list[str]) -> None:
    for relative in secrets:
        component = safe_component(relative, "secret filename")
        path = target / component
        if path.is_symlink():
            raise ValueError(f"refusing symlinked release secret: {path}")
        if path.is_file():
            path.unlink()


def finish(
    target: Path,
    root: Path,
    status: str,
    *,
    retain_hours: float = 48.0,
    secrets: list[str] | None = None,
) -> Path | None:
    resolved = validate_existing_target(target, root)
    remove_secrets(resolved, secrets or [])
    if status == "success":
        shutil.rmtree(resolved)
        return None
    if status not in {"failure", "cancelled"}:
        raise ValueError(f"unsupported release scratch status: {status!r}")
    expires = datetime.now(UTC) + timedelta(hours=retain_hours)
    atomic_json(
        resolved / RETAINED_MARKER,
        {
            "schema_version": 1,
            "status": status,
            "retained_at": timestamp(),
            "expires_at": expires.isoformat(),
            "contains_secrets": False,
        },
    )
    return resolved


def cleanup(root: Path, *, now: datetime | None = None, force: bool = False) -> list[Path]:
    managed = managed_root(root)
    moment = now or datetime.now(UTC)
    removed: list[Path] = []
    runs = managed / "r"
    if not runs.exists():
        return removed
    for marker in sorted(runs.rglob(RETAINED_MARKER)):
        target = validate_existing_target(marker.parent, managed)
        value = json.loads(marker.read_text(encoding="utf-8"))
        expires = datetime.fromisoformat(str(value.get("expires_at", "")))
        if expires.tzinfo is None:
            raise ValueError(f"retention expiry must contain a timezone: {marker}")
        if force or expires <= moment:
            shutil.rmtree(target)
            removed.append(target)
    return removed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    initialize = subparsers.add_parser("init")
    initialize.add_argument("--root", type=Path, required=True)
    begin = subparsers.add_parser("start")
    for subparser in (begin,):
        subparser.add_argument("--root", type=Path, required=True)
        subparser.add_argument("--run-id", required=True)
        subparser.add_argument("--attempt", required=True)
        subparser.add_argument("--platform", choices=("linux", "windows", "macos"), required=True)
        subparser.add_argument("--flavor", required=True)
    begin.add_argument("--github-output", type=Path)
    end = subparsers.add_parser("finish")
    end.add_argument("--root", type=Path, required=True)
    end.add_argument("--target", type=Path, required=True)
    end.add_argument("--status", choices=("success", "failure", "cancelled"), required=True)
    end.add_argument("--retain-hours", type=float, default=48.0)
    end.add_argument("--secret", action="append", default=[])
    prune = subparsers.add_parser("cleanup")
    prune.add_argument("--root", type=Path, required=True)
    prune.add_argument("--force", action="store_true", help="Force cleanup ignoring expiry")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "init":
        print(managed_root(args.root, initialize=True))
    elif args.command == "start":
        target = start(args.root, args.run_id, args.attempt, args.platform, args.flavor)
        if args.github_output:
            with args.github_output.open("a", encoding="utf-8") as stream:
                stream.write(f"run_root={target}\n")
        print(target)
    elif args.command == "finish":
        retained = finish(
            args.target,
            args.root,
            args.status,
            retain_hours=args.retain_hours,
            secrets=args.secret,
        )
        print(retained or "removed")
    else:
        for path in cleanup(args.root, force=args.force):
            print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
