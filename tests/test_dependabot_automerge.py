from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location(
    "dependabot_automerge", ROOT / "scripts" / "dependabot_automerge.py"
)
automerge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(automerge)
REPO = "HerRei/local-upscale"
SHA = "a" * 40


def commit(sha: str = SHA, login: str = "dependabot[bot]", verified: bool = True) -> dict:
    return {
        "sha": sha,
        "author": {"login": login},
        "commit": {"verification": {"verified": verified}},
    }


def pull(author: str = "dependabot[bot]", head: str = SHA) -> dict:
    return {
        "user": {"login": author},
        "base": {"ref": "main"},
        "head": {"sha": head, "repo": {"full_name": REPO}},
    }


def case(**overrides) -> list[str]:
    arguments = dict(
        pull=pull(),
        event_sha=SHA,
        group="dev-tools",
        ecosystem="npm_and_yarn",
        dependencies=[
            {"dependencyName": "vitest", "updateType": "version-update:semver-minor"},
            {
                "dependencyName": "@testing-library/svelte",
                "updateType": "version-update:semver-patch",
            },
        ],
        commits=[commit()],
        files=["desktop/package.json", "desktop/package-lock.json"],
        repo=REPO,
    )
    arguments.update(overrides)
    return automerge.verdict(**arguments)


def test_an_allowlisted_dev_tools_group_is_eligible():
    assert case() == []


def test_github_as_committer_is_fine_but_a_foreign_author_is_not():
    assert case(commits=[commit(), commit("b" * 40, login="HerRei")]) != []
    assert any("not authored" in r for r in case(commits=[commit(login="HerRei")]))
    assert any("not verified" in r for r in case(commits=[commit(verified=False)]))


def test_fails_closed():
    assert any(
        "not on the allowlist" in r
        for r in case(
            dependencies=[{"dependencyName": "vite", "updateType": "version-update:semver-patch"}]
        )
    )
    assert any(
        "major" in r
        for r in case(
            dependencies=[{"dependencyName": "vitest", "updateType": "version-update:semver-major"}]
        )
    )
    assert any("group" in r for r in case(group="runtime"))
    assert any("group" in r for r in case(group=""))
    assert any("metadata" in r for r in case(dependencies=[]))
    assert any("ecosystem" in r for r in case(ecosystem="github_actions"))
    assert any("unexpected files" in r for r in case(files=["desktop/vite.config.ts"]))
    assert any("head moved" in r for r in case(pull=pull(head="c" * 40)))
    assert any("Dependabot" in r for r in case(pull=pull(author="HerRei")))


def test_allowlist_matches_the_dependabot_dev_tools_groups():
    config = (ROOT / ".github/dependabot.yml").read_text()
    # Every pattern in a dev-tools group must be something the script allows, so a
    # group can never contain a package the script would refuse (or the reverse).
    patterns: set[str] = set()
    lines = config.splitlines()
    for i, line in enumerate(lines):
        if line.strip() != "dev-tools:":
            continue
        indent = len(line) - len(line.lstrip())
        for inner in lines[i + 1 :]:
            if inner.strip() and len(inner) - len(inner.lstrip()) <= indent:
                break
            if "update-types" not in inner and not inner.strip().startswith("#"):
                patterns |= set(re.findall(r'"([^"]+)"', inner))
    allowed = set().union(*automerge.ALLOWLIST.values()) | {
        prefix + "*" for prefixes in automerge.ALLOWED_PREFIXES.values() for prefix in prefixes
    }
    assert patterns == allowed
