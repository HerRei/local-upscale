#!/usr/bin/env python3
"""Weekly look at every open Dependabot PR; no AI involved.

For each PR it reads the latest CI and Preview runs of the PR's current head:
- an infrastructure failure (the Mac mini's storage guard, the release-priority wait, a
  cancelled or timed-out job) gets ONE rerun of the failed jobs per head commit;
- a real code failure gets the `claude-fix` label (at most MAX_FLAGS PRs per run), which
  is the cue for the deps-doctor skill;
- everything else is just reported.
The result goes into one issue labelled `deps-status`; a comment there notifies Hermes
when something needs him. State (which head already had its rerun) lives in a hidden
marker in that issue, so a rerun is never repeated for the same commit.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from required_checks import WATCHED, GitHub  # noqa: E402

BOT = "dependabot[bot]"
FIX_LABEL = "claude-fix"
STATUS_LABEL = "deps-status"
MAX_FLAGS = 2
INFRA_STEPS = (
    "isolated SSD workspace",  # storage_preflight refused the job
    "Wait without occupying",  # release-priority coordinator timed out
    "Wait for release priority",
    "scratch",
    "Set up job",  # the runner never started the job
)
MARKER = re.compile(r"<!-- deps-triage (\{.*?\}) -->", re.S)
RERUN_MAX_AGE = dt.timedelta(days=29)  # GitHub refuses reruns of runs older than 30 days


@dataclass
class Finding:
    number: int
    title: str
    sha: str
    state: str  # green | pending | infra | code | stale | main-red | stuck
    detail: str
    run_id: int | None = None
    run_created: str | None = None


def classify(run: dict | None, jobs: list[dict], now: dt.datetime) -> tuple[str, str]:
    if run is None:
        return "pending", "no run yet"
    if run["status"] != "completed":
        started = dt.datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
        if now - started > dt.timedelta(hours=12):
            return "stuck", f"{run['status']} for {(now - started).days}d (runner offline?)"
        return "pending", run["status"]
    if run["conclusion"] == "success":
        return "green", "passed"
    infra, code = [], []
    for job in jobs:
        if job.get("conclusion") in {"cancelled", "timed_out"}:
            infra.append(f"{job['name']}: {job['conclusion']}")
            continue
        if job.get("conclusion") != "failure":
            continue
        failed = [s["name"] for s in job.get("steps", []) if s.get("conclusion") == "failure"]
        if failed and all(any(p in step for p in INFRA_STEPS) for step in failed):
            infra.append(f"{job['name']}: {', '.join(failed)}")
        else:
            code.append(f"{job['name']}: {', '.join(failed) or 'failed'}")
    if code:
        return "code", "; ".join(code)
    if infra or run["conclusion"] in {"cancelled", "timed_out"}:
        return "infra", "; ".join(infra) or run["conclusion"]
    return "code", run["conclusion"]


def worst(states: list[tuple[str, str, dict | None]]) -> tuple[str, str, dict | None]:
    order = ["code", "infra", "stuck", "pending", "green"]  # "stale" is set after this
    return min(states, key=lambda s: order.index(s[0]))


def report(findings: list[Finding], flagged: list[int], reruns: list[int]) -> str:
    rows = ["| PR | State | Detail | Done this run |", "| --- | --- | --- | --- |"]
    for f in findings:
        done = []
        if f.number in reruns:
            done.append("reran failed jobs")
        if f.number in flagged:
            done.append(f"labelled `{FIX_LABEL}`")
        rows.append(
            f"| #{f.number} {f.title[:60]} | {f.state} | {f.detail[:120]} | {', '.join(done)} |"
        )
    return "\n".join(rows)


class Triage:
    def __init__(self, gh: GitHub, now: dt.datetime, dry_run: bool) -> None:
        self.gh, self.now, self.dry_run = gh, now, dry_run
        self.repo = gh.repo

    def status_issue(self) -> dict | None:
        issues = self.gh.paged(f"/repos/{self.repo}/issues?state=open&labels={STATUS_LABEL}")
        return issues[0] if issues else None

    def state(self, issue: dict | None) -> dict:
        match = MARKER.search((issue or {}).get("body") or "")
        return json.loads(match.group(1)) if match else {"reran": [], "flagged": []}

    def latest_runs(self, sha: str) -> list[dict]:
        runs = []
        for workflow in WATCHED:
            found = self.gh.paged(
                f"/repos/{self.repo}/actions/workflows/{workflow}/runs?head_sha={sha}",
                "workflow_runs",
            )
            if found:
                runs.append(max(found, key=lambda r: r["created_at"]))
        return runs

    def examine(self, pull: dict) -> Finding:
        sha = pull["head"]["sha"]
        states = []
        for run in self.latest_runs(sha) or [None]:
            jobs = (
                self.gh.paged(f"/repos/{self.repo}/actions/runs/{run['id']}/jobs", "jobs")
                if run and run["status"] == "completed" and run["conclusion"] != "success"
                else []
            )
            state, detail = classify(run, jobs, self.now)
            states.append((state, detail, run))
        state, detail, run = worst(states)
        if state == "code":
            behind = self.gh.request(f"/repos/{self.repo}/compare/main...{sha}")[0]["behind_by"]
            if behind:
                # Main may already fix this (it did for the Windows tests); don't spend an
                # AI run on an old base.
                state = "stale"
                detail += f" (behind main by {behind}: comment `@dependabot rebase` first)"
        return Finding(
            pull["number"],
            pull["title"],
            sha,
            state,
            detail,
            run["id"] if run else None,
            run["created_at"] if run else None,
        )

    def act(self, findings: list[Finding], memory: dict) -> tuple[list[int], list[int]]:
        reruns, flagged = [], []
        for f in findings:
            if f.state == "infra" and f.sha not in memory["reran"] and f.run_id:
                created = dt.datetime.fromisoformat(f.run_created.replace("Z", "+00:00"))
                if self.now - created > RERUN_MAX_AGE:
                    f.detail += " (too old to rerun: comment `@dependabot rebase`)"
                    continue
                if not self.dry_run:
                    self.gh.request(
                        f"/repos/{self.repo}/actions/runs/{f.run_id}/rerun-failed-jobs", {}
                    )
                memory["reran"].append(f.sha)
                reruns.append(f.number)
            elif f.state == "infra":
                f.detail += " (already reran once for this commit: needs Hermes)"
        for f in findings:
            if f.state == "code" and f.sha not in memory["flagged"] and len(flagged) < MAX_FLAGS:
                if not self.dry_run:
                    self.gh.request(
                        f"/repos/{self.repo}/issues/{f.number}/labels", {"labels": [FIX_LABEL]}
                    )
                memory["flagged"].append(f.sha)
                flagged.append(f.number)
        # Keep the memory small: only heads that are still open matter.
        open_heads = {f.sha for f in findings}
        memory["reran"] = [s for s in memory["reran"] if s in open_heads]
        memory["flagged"] = [s for s in memory["flagged"] if s in open_heads]
        return reruns, flagged

    def publish(self, issue: dict | None, body: str, comment: str | None) -> None:
        if self.dry_run:
            print(body)
            if comment:
                print("\nComment:\n" + comment)
            return
        if issue is None:
            issue, _ = self.gh.request(
                f"/repos/{self.repo}/issues",
                {"title": "Dependency updates: status", "body": body, "labels": [STATUS_LABEL]},
            )
        else:
            self.gh.request(f"/repos/{self.repo}/issues/{issue['number']}", {"body": body}, "PATCH")
        if comment:
            self.gh.request(
                f"/repos/{self.repo}/issues/{issue['number']}/comments", {"body": comment}
            )

    def run(self) -> int:
        pulls = [
            p
            for p in self.gh.paged(f"/repos/{self.repo}/pulls?state=open")
            if p["user"]["login"] == BOT
        ]
        issue = self.status_issue()
        memory = self.state(issue)
        findings = [self.examine(p) for p in sorted(pulls, key=lambda p: p["number"])]
        main_sha = self.gh.request(f"/repos/{self.repo}/commits/main")[0]["sha"]
        main_states = [classify(r, [], self.now)[0] for r in self.latest_runs(main_sha)]
        main_red = any(state not in {"green", "pending"} for state in main_states)
        if main_red:
            # Every PR inherits main's failures; an AI run per PR would only rediscover them.
            for f in findings:
                if f.state == "code":
                    f.state, f.detail = "main-red", f.detail + " (main itself is red)"
        reruns, flagged = self.act(findings, memory)
        table = report(findings, flagged, reruns)
        stamp = self.now.strftime("%Y-%m-%d %H:%M UTC")
        body = (
            f"Updated by `deps-triage.yml` on {stamp}. Green dev-tools PRs merge themselves;"
            f" `{FIX_LABEL}` PRs are for the deps-doctor skill; everything else needs a"
            " review (docs/dependency-updates.md).\n\n"
            f"{table if findings else 'No open Dependabot PRs.'}\n\n"
            f"<!-- deps-triage {json.dumps(memory)} -->\n"
        )
        attention = [f for f in findings if f.state in {"code", "stale", "stuck", "main-red"}]
        comment = None
        if attention:
            comment = (
                ("**main is red: fix main first.** " if main_red else "")
                + "Needs you this week: "
                + ", ".join(f"#{f.number} {f.state}" for f in attention)
                + (
                    f". Labelled `{FIX_LABEL}`: " + ", ".join(f"#{n}" for n in flagged)
                    if flagged
                    else ""
                )
                + f". Reran {len(reruns)} infrastructure failure(s)."
            )
        self.publish(issue, body, comment)
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN", "")
    now = dt.datetime.now(dt.timezone.utc)
    return Triage(GitHub(args.repo, token), now, args.dry_run).run()


if __name__ == "__main__":
    raise SystemExit(main())
