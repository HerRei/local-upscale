#!/usr/bin/env python3
"""Manage bounded, lock-protected LocalSR scratch directories on SSD storage."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT_MARKER = ".localsr-ci-root"
COMPONENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")
STATE_MARKERS = (".active", ".complete", ".failed", ".abandoned")


def now() -> str:
    return datetime.now(UTC).isoformat()


def atomic_json(path: Path, value: object) -> None:
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def validate_component(value: str, label: str) -> str:
    if not COMPONENT_RE.fullmatch(value):
        raise ValueError(f"Unsafe {label}: {value!r}")
    return value


def managed_root(path: Path, initialize: bool = False) -> Path:
    root = path.expanduser().resolve()
    if root == Path(root.anchor):
        raise ValueError(f"Refusing broad scratch root: {root}")
    if initialize:
        root.mkdir(parents=True, exist_ok=True)
        marker = root / ROOT_MARKER
        if not marker.exists():
            atomic_json(marker, {"schema_version": 1, "created_at": now()})
    if not (root / ROOT_MARKER).is_file():
        raise ValueError(f"Scratch root is not initialized: {root}")
    return root


def run_directory(args: argparse.Namespace) -> Path:
    root = managed_root(args.root)
    values = [
        validate_component(str(args.run_id), "run id"),
        validate_component(str(args.attempt), "run attempt"),
        validate_component(args.platform, "platform"),
        validate_component(args.flavor, "flavor"),
    ]
    result = root / "runs"
    for value in values:
        result /= value
    resolved = result.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"Run directory escaped managed root: {resolved}")
    return resolved


def lock_is_held(path: Path) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(stream, fcntl.LOCK_UN)
    return False


def command_init(args: argparse.Namespace) -> None:
    root = managed_root(args.root, initialize=True)
    for name in ("runs", "cache", "metrics"):
        (root / name).mkdir(exist_ok=True)
    print(root)


def command_start(args: argparse.Namespace) -> None:
    directory = run_directory(args)
    if directory.exists() and lock_is_held(directory / ".lock"):
        raise RuntimeError(f"Scratch directory is already active: {directory}")
    if directory.exists() and any((directory / name).exists() for name in STATE_MARKERS):
        raise RuntimeError(f"Scratch directory already has run state: {directory}")
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("tmp", "build", "dist", "environment", "wheels", "staging"):
        (directory / name).mkdir(exist_ok=True)
    atomic_json(
        directory / ".active",
        {
            "schema_version": 1,
            "created_at": now(),
            "run_id": str(args.run_id),
            "attempt": str(args.attempt),
            "platform": args.platform,
            "flavor": args.flavor,
        },
    )
    log = (directory / "monitor.log").open("ab")
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "monitor", str(directory)],
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    atomic_json(directory / ".monitor.json", {"pid": process.pid, "started_at": now()})
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as stream:
            stream.write(f"run_root={directory}\n")
    print(directory)


def _monitor_metrics(directory: Path, initial_free: int, minimum_free: int) -> dict[str, object]:
    root = directory
    while not (root / ROOT_MARKER).exists() and root != root.parent:
        root = root.parent
    current_free = shutil.disk_usage(root).free
    return {
        "schema_version": 1,
        "updated_at": now(),
        "initial_free_bytes": initial_free,
        "minimum_free_bytes": minimum_free,
        "current_free_bytes": current_free,
        "peak_consumed_bytes": max(0, initial_free - minimum_free),
    }


def command_monitor(args: argparse.Namespace) -> None:
    directory = args.directory.resolve()
    if not directory.is_dir():
        raise ValueError(f"Monitor directory does not exist: {directory}")
    root = directory
    while not (root / ROOT_MARKER).exists() and root != root.parent:
        root = root.parent
    if not (root / ROOT_MARKER).is_file():
        raise ValueError(f"Monitor directory is outside an initialized root: {directory}")
    lock_path = directory / ".lock"
    with lock_path.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        initial_free = shutil.disk_usage(root).free
        minimum_free = initial_free
        deadline = time.monotonic() + args.max_hours * 3600

        def stop(_signum: int, _frame: object) -> None:
            (directory / ".stop").touch()

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        while not (directory / ".stop").exists() and time.monotonic() < deadline:
            minimum_free = min(minimum_free, shutil.disk_usage(root).free)
            atomic_json(
                directory / ".metrics.json", _monitor_metrics(directory, initial_free, minimum_free)
            )
            atomic_json(directory / ".heartbeat", {"timestamp": now(), "pid": os.getpid()})
            time.sleep(args.interval)
        minimum_free = min(minimum_free, shutil.disk_usage(root).free)
        atomic_json(
            directory / ".metrics.json", _monitor_metrics(directory, initial_free, minimum_free)
        )
        if time.monotonic() >= deadline and (directory / ".active").exists():
            atomic_json(directory / ".abandoned", {"timestamp": now(), "reason": "monitor-timeout"})


def stop_monitor(directory: Path, timeout: float = 20.0) -> None:
    (directory / ".stop").touch()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not lock_is_held(directory / ".lock"):
            return
        time.sleep(0.25)
    raise TimeoutError(f"Scratch monitor did not stop: {directory}")


def command_finish(args: argparse.Namespace) -> None:
    directory = run_directory(args)
    if not directory.is_dir() or not (directory / ".active").is_file():
        raise ValueError(f"Scratch directory is not active: {directory}")
    stop_monitor(directory)
    metrics = directory / ".metrics.json"
    root = managed_root(args.root)
    if metrics.is_file():
        destination = (
            root / "metrics" / (f"{args.run_id}-{args.attempt}-{args.platform}-{args.flavor}.json")
        )
        destination.parent.mkdir(exist_ok=True)
        shutil.copy2(metrics, destination)
    (directory / ".active").unlink()
    marker = ".complete" if args.status == "complete" else ".failed"
    atomic_json(directory / marker, {"timestamp": now(), "status": args.status})
    if args.cleanup and args.status == "complete":
        shutil.rmtree(directory)
        print(f"Removed verified completed scratch: {directory}")
    else:
        print(directory)


def trim_cache(root: Path, maximum_bytes: int, dry_run: bool) -> int:
    cache = root / "cache"
    if not cache.exists():
        return 0
    files = [path for path in cache.rglob("*") if path.is_file() and not path.is_symlink()]
    total = sum(path.stat().st_size for path in files)
    removed = 0
    for path in sorted(files, key=lambda item: item.stat().st_mtime):
        if total <= maximum_bytes:
            break
        size = path.stat().st_size
        print(f"cache-evict {path} ({size} bytes)")
        if not dry_run:
            path.unlink()
        total -= size
        removed += size
    return removed


def command_cleanup(args: argparse.Namespace) -> None:
    root = managed_root(args.root)
    current = time.time()
    removed: list[str] = []
    runs = root / "runs"
    if runs.exists():
        for state in sorted(runs.rglob(".complete")) + sorted(runs.rglob(".failed")):
            directory = state.parent
            if lock_is_held(directory / ".lock"):
                continue
            age_hours = (current - state.stat().st_mtime) / 3600
            minimum = args.complete_hours if state.name == ".complete" else args.failed_hours
            if age_hours < minimum:
                continue
            print(f"scratch-remove {directory} state={state.name} age_hours={age_hours:.1f}")
            if not args.dry_run:
                shutil.rmtree(directory)
            removed.append(str(directory))
        for state in sorted(runs.rglob(".active")):
            directory = state.parent
            if lock_is_held(directory / ".lock"):
                continue
            age_hours = (current - state.stat().st_mtime) / 3600
            if age_hours < args.abandoned_hours:
                continue
            print(f"scratch-remove {directory} state=abandoned age_hours={age_hours:.1f}")
            if not args.dry_run:
                shutil.rmtree(directory)
            removed.append(str(directory))
    evicted = trim_cache(root, int(args.cache_max_gib * 1024**3), args.dry_run)
    print(json.dumps({"removed_scratch": removed, "evicted_cache_bytes": evicted}))


def common_run_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--attempt", required=True)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--flavor", required=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    initialize = subparsers.add_parser("init")
    initialize.add_argument("--root", type=Path, required=True)
    initialize.set_defaults(func=command_init)
    start = subparsers.add_parser("start")
    common_run_arguments(start)
    start.add_argument("--github-output", type=Path)
    start.set_defaults(func=command_start)
    monitor = subparsers.add_parser("monitor")
    monitor.add_argument("directory", type=Path)
    monitor.add_argument("--interval", type=float, default=15.0)
    monitor.add_argument("--max-hours", type=float, default=12.0)
    monitor.set_defaults(func=command_monitor)
    finish = subparsers.add_parser("finish")
    common_run_arguments(finish)
    finish.add_argument("--status", choices=("complete", "failed"), required=True)
    finish.add_argument("--cleanup", action="store_true")
    finish.set_defaults(func=command_finish)
    cleanup = subparsers.add_parser("cleanup")
    cleanup.add_argument("--root", type=Path, required=True)
    cleanup.add_argument("--complete-hours", type=float, default=1.0)
    cleanup.add_argument("--failed-hours", type=float, default=48.0)
    cleanup.add_argument("--abandoned-hours", type=float, default=48.0)
    cleanup.add_argument("--cache-max-gib", type=float, default=8.0)
    cleanup.add_argument("--dry-run", action="store_true")
    cleanup.set_defaults(func=command_cleanup)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
