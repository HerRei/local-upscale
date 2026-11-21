from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("deps_triage", ROOT / "scripts" / "deps_triage.py")
triage = importlib.util.module_from_spec(spec)
sys.modules["deps_triage"] = triage  # dataclasses look their module up here
spec.loader.exec_module(triage)
NOW = dt.datetime(2026, 11, 13, 5, 0, tzinfo=dt.timezone.utc)


def run(conclusion: str | None, status: str = "completed", age_days: int = 1) -> dict:
    created = (NOW - dt.timedelta(days=age_days)).isoformat().replace("+00:00", "Z")
    return {"id": 7, "status": status, "conclusion": conclusion, "created_at": created}


def job(name: str, conclusion: str, failed_step: str | None = None) -> dict:
    steps = [{"name": failed_step, "conclusion": "failure"}] if failed_step else []
    return {"name": name, "conclusion": conclusion, "steps": steps}


def test_classify():
    assert triage.classify(None, [], NOW)[0] == "pending"
    assert triage.classify(run("success"), [], NOW)[0] == "green"
    assert triage.classify(run(None, "queued", age_days=2), [], NOW)[0] == "stuck"
    storage = job("Lint & format (Linux X64)", "failure", "Allocate isolated SSD workspace")
    assert triage.classify(run("failure"), [storage], NOW)[0] == "infra"
    assert triage.classify(run("cancelled"), [job("Wait", "cancelled")], NOW)[0] == "infra"
    tests = job("Test Linux X64", "failure", "Install and test in isolation")
    # One real failure makes the whole run a code failure, even next to infra trouble.
    assert triage.classify(run("failure"), [storage, tests], NOW)[0] == "code"


class FakeGitHub:
    repo = "HerRei/local-upscale"

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def request(self, path, data=None, method=None):
        self.calls.append((path, data))
        return {}, {}


def finding(number: int, state: str, sha: str, ages: tuple[int, ...] = (1,)) -> object:
    runs = [
        (100 * number + i, (NOW - dt.timedelta(days=a)).isoformat().replace("+00:00", "Z"))
        for i, a in enumerate(ages)
    ]
    return triage.Finding(number, f"PR {number}", sha, state, "x", runs if state == "infra" else [])


def test_reruns_once_per_head_every_workflow_and_flags_at_most_two():
    gh = FakeGitHub()
    worker = triage.Triage(gh, NOW, dry_run=False)
    memory = {"reran": [], "flagged": []}
    findings = [
        finding(1, "infra", "s1", ages=(1, 2)),  # CI and Preview both hit infrastructure
        finding(2, "code", "s2"),
        finding(3, "code", "s3"),
        finding(4, "code", "s4"),
        finding(5, "infra", "s5", ages=(40,)),
        finding(6, "stale", "s6"),
    ]
    reruns, flagged = worker.plan(findings, memory)
    assert reruns == [(1, [100, 101])] and flagged == [2, 3]
    assert gh.calls == []  # planning never calls GitHub
    assert "too old" in findings[4].detail
    worker.execute(reruns, flagged)
    rerun_calls = [p for p, _ in gh.calls if p.endswith("rerun-failed-jobs")]
    assert rerun_calls == [
        "/repos/HerRei/local-upscale/actions/runs/100/rerun-failed-jobs",
        "/repos/HerRei/local-upscale/actions/runs/101/rerun-failed-jobs",
    ]
    # Second week, same heads: no repeated rerun, PR 4 gets its turn.
    reruns, flagged = worker.plan(findings, memory)
    assert reruns == [] and flagged == [4]
    assert "already reran" in findings[0].detail


def test_memory_forgets_closed_heads():
    worker = triage.Triage(FakeGitHub(), NOW, dry_run=True)
    memory = {"reran": ["gone"], "flagged": ["gone", "s2"]}
    worker.plan([finding(2, "code", "s2")], memory)
    assert memory == {"reran": [], "flagged": ["s2"]}


def test_marker_round_trip():
    worker = triage.Triage(FakeGitHub(), NOW, dry_run=True)
    body = 'text\n<!-- deps-triage {"reran": ["a"], "flagged": []} -->\n'
    assert worker.state({"body": body}) == {"reran": ["a"], "flagged": []}
    assert worker.state(None) == {"reran": [], "flagged": []}
