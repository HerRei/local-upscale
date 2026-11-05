#!/usr/bin/env python3
"""Set the one commit status the main ruleset requires: "Required checks".

The ruleset can't require CI and Tauri Preview directly: Preview only runs when its
paths change, and a required check that never reports blocks a PR forever. This script
decides which of the watched workflows apply to a commit (from their own path
filters), takes the latest run of each for that exact SHA and reports
success / failure / pending. .github/workflows/required.yml runs it from main's trusted
copy whenever a watched workflow finishes, so reruns update the status too.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTEXT = "Required checks"
# Workflow file -> display name. Every workflow that must pass for a merge.
WATCHED = {"ci.yml": "CI", "tauri-preview.yml": "Tauri Next Preview"}
GOOD = {"success"}
PENDING = {None, "", "neutral"}  # conclusion while queued or running


def trigger_paths(workflow_text: str, event: str) -> list[str] | None:
    """The `paths:` list under `on.<event>`; None means the workflow has no path filter.

    Deliberately tiny: these workflow files write `paths:` as one quoted item per line.
    tests/test_required_checks.py pins that against the real files.
    """
    block = re.search(
        rf"^  {re.escape(event)}:\n((?:    .*\n|\n)*)", workflow_text, flags=re.MULTILINE
    )
    if not block:
        return None
    paths = re.search(r"^    paths:\n((?:      - .*\n)+)", block.group(1), flags=re.MULTILINE)
    if not paths:
        return None
    return [line.split("- ", 1)[1].strip().strip("\"'") for line in paths.group(1).splitlines()]


def glob_regex(pattern: str) -> re.Pattern[str]:
    """GitHub's path globs: ** crosses directories, * and ? don't."""
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def applies(workflow_text: str, event: str, files: list[str]) -> bool:
    patterns = trigger_paths(workflow_text, event)
    if patterns is None:
        return True
    compiled = [glob_regex(p) for p in patterns]
    return any(rx.match(f) for f in files for rx in compiled)


def decide(runs_by_workflow: dict[str, dict | None]) -> tuple[str, str]:
    """(state, description) from the latest run of every applicable workflow."""
    waiting, failed = [], []
    for name, run in runs_by_workflow.items():
        if run is None or run.get("status") != "completed" or run.get("conclusion") in PENDING:
            waiting.append(name)
        elif run.get("conclusion") not in GOOD:
            failed.append(f"{name}: {run.get('conclusion')}")
    if failed:
        return "failure", "; ".join(failed)[:140]
    if waiting:
        return "pending", ("waiting for " + ", ".join(waiting))[:140]
    return "success", "all applicable workflows passed: " + ", ".join(runs_by_workflow)


class GitHub:
    def __init__(self, repo: str, token: str) -> None:
        self.repo, self.token = repo, token

    def request(
        self, path: str, data: dict | None = None, method: str | None = None
    ) -> tuple[object, dict]:
        url = path if path.startswith("https://") else f"https://api.github.com{path}"
        request = urllib.request.Request(
            url,
            data=json.dumps(data).encode() if data is not None else None,
            method=method or ("POST" if data is not None else "GET"),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read() or b"null"), dict(response.headers)

    def paged(self, path: str, key: str | None = None) -> list:
        items: list = []
        url: str | None = path + ("&" if "?" in path else "?") + "per_page=100"
        while url:
            body, headers = self.request(url)
            items += body[key] if key else body
            match = re.search(r'<([^>]+)>;\s*rel="next"', headers.get("Link", ""))
            url = match.group(1) if match else None
        return items


def changed_files(gh: GitHub, sha: str) -> tuple[str, list[str]]:
    """('pull_request', files of the open PR whose head is sha) or ('push', commit files)."""
    pulls = gh.paged(f"/repos/{gh.repo}/commits/{sha}/pulls")
    for pull in pulls:
        if pull["state"] == "open" and pull["head"]["sha"] == sha:
            files = gh.paged(f"/repos/{gh.repo}/pulls/{pull['number']}/files")
            return "pull_request", [f["filename"] for f in files]
    commit, _ = gh.request(f"/repos/{gh.repo}/commits/{sha}")
    return "push", [f["filename"] for f in commit.get("files", [])]


def latest_run(gh: GitHub, workflow: str, sha: str) -> dict | None:
    runs = gh.paged(
        f"/repos/{gh.repo}/actions/workflows/{workflow}/runs?head_sha={sha}", "workflow_runs"
    )
    # Dispatched runs count too: the release train dispatches CI on its PR branch.
    runs = [r for r in runs if r["event"] in {"pull_request", "push", "workflow_dispatch"}]
    return max(runs, key=lambda r: r["created_at"]) if runs else None


def evaluate(gh: GitHub, sha: str, workflows_dir: Path) -> tuple[str, str]:
    event, files = changed_files(gh, sha)
    runs: dict[str, dict | None] = {}
    for workflow, name in WATCHED.items():
        if applies((workflows_dir / workflow).read_text(), event, files):
            runs[name] = latest_run(gh, workflow, sha)
    return decide(runs)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sha")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--dry-run", action="store_true", help="print, don't set the status")
    args = parser.parse_args()
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN", "")
    gh = GitHub(args.repo, token)
    state, description = evaluate(gh, args.sha, ROOT / ".github/workflows")
    print(f"{args.sha[:12]} {state}: {description}")
    if not args.dry_run:
        server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
        run_id = os.environ.get("GITHUB_RUN_ID")
        target = f"{server}/{args.repo}/actions/runs/{run_id}" if run_id else None
        gh.request(
            f"/repos/{args.repo}/statuses/{urllib.parse.quote(args.sha)}",
            {"state": state, "context": CONTEXT, "description": description, "target_url": target},
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
