from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location(
    "release_train", ROOT / "scripts" / "release_train.py"
)
train = importlib.util.module_from_spec(spec)
spec.loader.exec_module(train)
NOW = dt.datetime(2026, 11, 20, 5, 0, tzinfo=dt.timezone.utc)


def release(days_ago: int, tag: str = "v0.3.0-beta", draft: bool = False) -> dict:
    published = (NOW - dt.timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")
    return {"tag_name": tag, "draft": draft, "published_at": None if draft else published}


def decide(**overrides) -> tuple[str, str]:
    arguments = dict(
        now=NOW,
        release=release(15),
        main_version="0.3.0-beta",
        open_release_prs=[],
        main_status="success",
        subjects=["Fix the comparison zoom (#70)"],
    )
    arguments.update(overrides)
    return train.decide(**arguments)


def test_goes_when_everything_holds():
    assert decide()[0] == "go"


def test_waits_for_each_reason():
    assert decide(release=release(13))[0] == "wait"
    assert decide(release=None)[0] == "wait"
    assert decide(open_release_prs=[{"number": 80}])[1] == "candidate PR #80 is still open"
    assert "not published" in decide(main_version="0.3.1-beta")[1]
    assert "nothing to ship" in decide(subjects=[])[1]
    assert decide(main_status="failure")[0] == "wait"
    assert decide(main_status="pending")[0] == "wait"
    assert decide(main_status=None)[0] == "dispatch"


def test_drafts_never_count_as_published():
    picked = train.last_published([release(3, "v0.3.1-beta", draft=True), release(20)])
    assert picked["tag_name"] == "v0.3.0-beta"


def commit(subject: str, login: str) -> dict:
    return {"commit": {"message": subject + "\n\nbody"}, "author": {"login": login}}


def test_dev_tools_merges_alone_are_not_worth_a_release():
    dev = commit("Bump the dev-tools group in /desktop with 2 updates (#90)", "dependabot[bot]")
    runtime = commit("Bump the runtime group in /desktop with 3 updates (#91)", "dependabot[bot]")
    mine = commit("Keep the queue order after a restart (#92)", "HerRei")
    assert train.worth_shipping([dev]) == []
    assert train.worth_shipping([dev, runtime, mine]) == [
        "Bump the runtime group in /desktop with 3 updates (#91)",
        "Keep the queue order after a restart (#92)",
    ]
    # A dev-tools title from anyone but Dependabot still counts.
    assert train.worth_shipping([commit(dev["commit"]["message"], "HerRei")])
