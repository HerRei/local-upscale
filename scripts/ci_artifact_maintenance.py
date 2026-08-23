#!/usr/bin/env python3
"""Safely prune LocalSR HDD staging without touching active or sole artifacts."""

from __future__ import annotations

import argparse
import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

GIB = 1024**3
PROTECTED_GLOBS = (".active", ".copying*", ".validating", ".publishing")


@dataclass(frozen=True)
class Attempt:
    path: Path
    modified: float
    state: str


def managed_root(path: Path) -> Path:
    root = path.resolve()
    if root == Path(root.anchor) or root.name != "ci-artifacts":
        raise ValueError(f"Refusing unmanaged artifact root: {root}")
    if not root.is_dir():
        raise ValueError(f"Artifact root does not exist: {root}")
    return root


def protected(path: Path) -> bool:
    return any(any(path.rglob(pattern)) for pattern in PROTECTED_GLOBS)


def state_for(path: Path) -> str:
    if protected(path):
        return "protected"
    if (path / ".published").exists():
        return "published"
    if (path / ".complete").exists():
        return "complete"
    if (path / ".failed").exists():
        return "failed"
    return "incomplete"


def attempts(root: Path) -> list[Attempt]:
    result: list[Attempt] = []
    for run in root.iterdir():
        if not run.is_dir() or not run.name.isdigit():
            continue
        for attempt in run.iterdir():
            if not attempt.is_dir() or not attempt.name.isdigit():
                continue
            result.append(Attempt(attempt, attempt.stat().st_mtime, state_for(attempt)))
    return sorted(result, key=lambda item: item.modified, reverse=True)


def remove_attempt(item: Attempt, root: Path, dry_run: bool) -> None:
    if not item.path.resolve().is_relative_to(root):
        raise ValueError(f"Attempt escaped artifact root: {item.path}")
    if protected(item.path):
        raise RuntimeError(f"Attempt became active while pruning: {item.path}")
    print(f"artifact-remove state={item.state} path={item.path}")
    if dry_run:
        return
    shutil.rmtree(item.path)
    run_dir = item.path.parent
    if not any(run_dir.iterdir()):
        run_dir.rmdir()


def trim_partial_files(root: Path, older_than_hours: float, dry_run: bool) -> int:
    threshold = time.time() - older_than_hours * 3600
    removed = 0
    for path in root.rglob("*.part"):
        if not path.is_file() or path.stat().st_mtime >= threshold or protected(path.parent):
            continue
        size = path.stat().st_size
        print(f"partial-remove path={path} size={size}")
        if not dry_run:
            path.unlink()
        removed += size
    return removed


def choose_expired(
    entries: list[Attempt],
    keep_complete: int,
    keep_incomplete: int,
    keep_published: int,
    max_age_days: float,
) -> list[Attempt]:
    cutoff = time.time() - max_age_days * 86400
    selected: list[Attempt] = []
    groups = {
        "complete": [item for item in entries if item.state == "complete"],
        "published": [item for item in entries if item.state == "published"],
        "incomplete": [item for item in entries if item.state in {"failed", "incomplete"}],
    }
    keeps = {
        "complete": keep_complete,
        "published": keep_published,
        "incomplete": keep_incomplete,
    }
    for group, values in groups.items():
        for item in values[keeps[group] :]:
            if item.modified < cutoff:
                selected.append(item)
    priority = {"failed": 0, "incomplete": 0, "complete": 1, "published": 2}
    return sorted(selected, key=lambda item: (priority[item.state], item.modified))


def pressure_candidates(entries: list[Attempt], selected: list[Attempt]) -> list[Attempt]:
    already = {item.path for item in selected}
    priority = {"failed": 0, "incomplete": 0, "complete": 1, "published": 2}
    return sorted(
        [item for item in entries if item.state != "protected" and item.path not in already],
        key=lambda item: (priority[item.state], item.modified),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/mnt/hdd/ci-artifacts"))
    parser.add_argument("--keep-complete", type=int, default=5)
    parser.add_argument("--keep-incomplete", type=int, default=2)
    parser.add_argument("--keep-published", type=int, default=2)
    parser.add_argument("--max-age-days", type=float, default=7.0)
    parser.add_argument("--min-free-gib", type=float, default=100.0)
    parser.add_argument("--min-free-percent", type=float, default=20.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    root = managed_root(args.root)
    partial_bytes = trim_partial_files(root, 24.0, args.dry_run)
    entries = attempts(root)
    selected = choose_expired(
        entries,
        args.keep_complete,
        args.keep_incomplete,
        args.keep_published,
        args.max_age_days,
    )
    for item in selected:
        remove_attempt(item, root, args.dry_run)

    total, _used, free = shutil.disk_usage(root)
    reserve = max(int(args.min_free_gib * GIB), int(total * args.min_free_percent / 100))
    pressure_removed: list[str] = []
    if free < reserve:
        for item in pressure_candidates(entries, selected):
            # Preserve the newest entry in each state even under pressure.
            same_state = [candidate for candidate in entries if candidate.state == item.state]
            if same_state and item.path == same_state[0].path:
                continue
            remove_attempt(item, root, args.dry_run)
            pressure_removed.append(str(item.path))
            if not args.dry_run:
                total, _used, free = shutil.disk_usage(root)
            if free >= reserve:
                break
    if free < reserve and not args.dry_run:
        raise RuntimeError(
            f"HDD reserve remains violated after safe cleanup: free={free}, reserve={reserve}"
        )
    print(
        json.dumps(
            {
                "partial_bytes_removed": partial_bytes,
                "expired_attempts": [str(item.path) for item in selected],
                "pressure_attempts": pressure_removed,
                "free_bytes": free,
                "reserve_bytes": reserve,
                "dry_run": args.dry_run,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
