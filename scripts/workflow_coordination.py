#!/usr/bin/env python3
"""Coordinate heavyweight self-hosted workflows without GitHub queue eviction.

GitHub concurrency groups retain at most one pending run.  Sharing one group
between CI, preview, and release workflows can therefore silently cancel a
required pending run.  This helper runs on a GitHub-hosted runner before any
self-hosted work and waits for higher-priority workflows through the read-only
Actions API.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

ACTIVE_STATUSES = frozenset({"queued", "in_progress", "waiting", "requested", "pending"})


@dataclass(frozen=True)
class ActiveRun:
    database_id: int
    name: str
    status: str
    url: str


def _request_runs(*, repository: str, token: str) -> list[dict[str, object]]:
    quoted_repo = urllib.parse.quote(repository, safe="/")
    request = urllib.request.Request(
        f"https://api.github.com/repos/{quoted_repo}/actions/runs?per_page=100",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "localsr-workflow-coordinator/1",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            payload = json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"GitHub Actions coordination query failed: {exc}") from exc
    runs = payload.get("workflow_runs") if isinstance(payload, dict) else None
    if not isinstance(runs, list):
        raise RuntimeError("GitHub Actions coordination response lacks workflow_runs[]")
    return [run for run in runs if isinstance(run, dict)]


def find_blocking_runs(
    runs: list[dict[str, object]],
    *,
    blocked_workflows: set[str],
    current_run_id: int,
    ignored_head_shas: set[str] | None = None,
    only_older_runs: bool = False,
    always_block_head_shas: set[str] | None = None,
) -> list[ActiveRun]:
    ignored_head_shas = ignored_head_shas or set()
    always_block_head_shas = always_block_head_shas or set()
    blocking: list[ActiveRun] = []
    for run in runs:
        database_id = run.get("id")
        name = run.get("name")
        status = run.get("status")
        head_sha = run.get("head_sha")
        if (
            not isinstance(database_id, int)
            or database_id == current_run_id
            or not isinstance(name, str)
            or name not in blocked_workflows
            or not isinstance(status, str)
            or status not in ACTIVE_STATUSES
            or (isinstance(head_sha, str) and head_sha in ignored_head_shas)
            or (
                only_older_runs
                and database_id > current_run_id
                and head_sha not in always_block_head_shas
            )
        ):
            continue
        html_url = run.get("html_url")
        blocking.append(
            ActiveRun(
                database_id=database_id,
                name=name,
                status=status,
                url=html_url if isinstance(html_url, str) else "",
            )
        )
    return sorted(blocking, key=lambda item: item.database_id)


def coordinate(
    *,
    repository: str,
    token: str,
    blocked_workflows: set[str],
    current_run_id: int,
    mode: str,
    timeout_seconds: int,
    poll_seconds: int,
    ignored_head_shas: set[str] | None = None,
    only_older_runs: bool = False,
    always_block_head_shas: set[str] | None = None,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        blocking = find_blocking_runs(
            _request_runs(repository=repository, token=token),
            blocked_workflows=blocked_workflows,
            current_run_id=current_run_id,
            ignored_head_shas=ignored_head_shas,
            only_older_runs=only_older_runs,
            always_block_head_shas=always_block_head_shas,
        )
        if not blocking:
            print("Mac-mini heavyweight workflow slot is clear.")
            return
        summary = ", ".join(
            f"{run.name} #{run.database_id} ({run.status}) {run.url}" for run in blocking
        )
        if mode == "fail":
            raise RuntimeError(f"Heavyweight workflow already active: {summary}")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError(
                f"Timed out waiting for the Mac-mini heavyweight workflow slot: {summary}"
            )
        print(f"Waiting for higher-priority workflow: {summary}", flush=True)
        time.sleep(min(poll_seconds, max(1, int(remaining))))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--current-run-id", required=True, type=int)
    parser.add_argument("--blocked-workflow", action="append", required=True)
    parser.add_argument("--mode", choices=("wait", "fail"), default="wait")
    parser.add_argument("--timeout-seconds", type=int, default=10_800)
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument(
        "--ignore-head-sha",
        action="append",
        default=[],
        help="Ignore a matching run head (used only to give same-commit CI priority)",
    )
    parser.add_argument(
        "--only-older-runs",
        action="store_true",
        help="Wait only for lower run IDs so peers form a deterministic FIFO",
    )
    parser.add_argument(
        "--always-block-head-sha",
        action="append",
        default=[],
        help="With --only-older-runs, still block a newer run for this exact head",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise SystemExit("GITHUB_TOKEN is required for read-only Actions coordination")
    if args.timeout_seconds < 1 or args.poll_seconds < 1:
        raise SystemExit("timeout and poll intervals must be positive")
    try:
        coordinate(
            repository=args.repository,
            token=token,
            blocked_workflows=set(args.blocked_workflow),
            current_run_id=args.current_run_id,
            mode=args.mode,
            timeout_seconds=args.timeout_seconds,
            poll_seconds=args.poll_seconds,
            ignored_head_shas=set(args.ignore_head_sha),
            only_older_runs=args.only_older_runs,
            always_block_head_shas=set(args.always_block_head_sha),
        )
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
