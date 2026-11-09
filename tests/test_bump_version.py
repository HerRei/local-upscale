from __future__ import annotations

import datetime as dt
import importlib.util
import json
import shutil
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
FILES = [
    "pyproject.toml",
    "uv.lock",
    "src/localsr/__init__.py",
    "README.md",
    "CHANGELOG.md",
    "KNOWN_LIMITATIONS.md",
    "docs/releasing.md",
    "docs/platforms.md",
    f"docs/releases/v{VERSION}.md",
    "desktop/package.json",
    "desktop/package-lock.json",
    "desktop/src-tauri/Cargo.toml",
    "desktop/src-tauri/Cargo.lock",
    "desktop/src-tauri/tauri.conf.json",
    "desktop/src/lib/demo.ts",
    "ci/tauri-targets.json",
    "ci/tauri-release-artifacts.json",
    "ci/public-beta-release.json",
    "ci/public-beta-readiness.json",
    "packaging/updates/production.pub",
]


def load(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    original = sys.path[:]
    try:
        sys.path.insert(0, str(path.parent))
        spec = importlib.util.spec_from_file_location(f"{name}_under_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path[:] = original


bump_version = load("bump_version")
release_version = load("check_release_version")


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    for relative in FILES:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    return tmp_path


def test_pep440_and_next_patch():
    assert bump_version.pep440("0.3.1-beta") == "0.3.1b0"
    assert bump_version.pep440("1.2.3-beta.4") == "1.2.3b4"
    assert bump_version.next_patch("0.3.0-beta") == "0.3.1-beta"
    with pytest.raises(ValueError):
        bump_version.parse("0.3.1")


def test_bump_produces_a_candidate_the_release_checks_accept(tree: Path):
    new = bump_version.next_patch(VERSION)
    bump_version.bump(tree, new, "- Dependency updates.", dt.date(2026, 11, 16))

    assert release_version.check(f"v{new}", tree) == new
    lock = tomllib.loads((tree / "uv.lock").read_text())
    assert {p["version"] for p in lock["package"] if p["name"] == "localsr"} == {
        bump_version.pep440(new)
    }
    plan = json.loads((tree / "ci/public-beta-release.json").read_text())
    assert {target["package"] for target in plan["targets"]} == {"candidate"}
    readme = (tree / "README.md").read_text()
    assert f"the downloads below are still v{VERSION}" in readme
    assert f"## [{new}] - Unreleased" in (tree / "CHANGELOG.md").read_text()
    # Prose about published packages is left alone: it still describes the old release.
    assert (tree / "docs/platforms.md").read_text() == (ROOT / "docs/platforms.md").read_text()


def test_bump_appends_notes_to_an_existing_unreleased_section(tree: Path):
    new = bump_version.next_patch(VERSION)
    bump_version.bump(tree, new, "- Dependency updates.", dt.date(2026, 11, 16))
    changelog = (tree / "CHANGELOG.md").read_text()

    assert changelog.count(f"## [{new}] - Unreleased") == 1
    assert "- Add fixed-seed property tests" in changelog
    assert "- Dependency updates." in changelog


def test_bump_refuses_older_versions_and_existing_notes(tree: Path):
    with pytest.raises(ValueError, match="not newer"):
        bump_version.bump(tree, VERSION, "- x", dt.date.today())
    new = bump_version.next_patch(VERSION)
    (tree / f"docs/releases/v{new}.md").write_text("exists")
    with pytest.raises(ValueError, match="already exists"):
        bump_version.bump(tree, new, "- x", dt.date.today())


def test_mark_published_removes_candidate_notices(tree: Path):
    new = bump_version.next_patch(VERSION)
    bump_version.bump(tree, new, "- Dependency updates.", dt.date(2026, 11, 16))
    stale = bump_version.mark_published(tree, dt.date(2026, 11, 18))

    assert f"## [{new}] - 2026-11-18" in (tree / "CHANGELOG.md").read_text()
    plan = json.loads((tree / "ci/public-beta-release.json").read_text())
    assert {target["package"] for target in plan["targets"]} == {"published"}
    assert bump_version.CANDIDATE_START not in (tree / "README.md").read_text()
    assert "not published yet" not in (tree / f"docs/releases/v{new}.md").read_text()
    assert (tree / "docs/releasing.md").read_text().count(f"Current release: **v{new}**") == 1
    # The downloads prose still names the old version, so a human must update it
    # before the release checks pass again.
    assert "README.md" in stale
    with pytest.raises(ValueError, match="README.md"):
        release_version.check(f"v{new}", tree)


def test_a_second_bump_replaces_the_previous_candidate_notice(tree: Path):
    first = bump_version.next_patch(VERSION)
    bump_version.bump(tree, first, "- one", dt.date(2026, 11, 16))
    second = bump_version.next_patch(first)
    bump_version.bump(tree, second, "- two", dt.date(2026, 11, 30))
    readme = (tree / "README.md").read_text()
    assert readme.count(bump_version.CANDIDATE_START) == 1
    assert f"v{second} is in preparation" in readme


def test_notes_with_backslashes_are_inserted_literally(tree: Path):
    new = bump_version.next_patch(VERSION)
    bump_version.bump(tree, new, r"- Fix C:\Users\hermes paths and \1 groups", dt.date.today())
    assert r"C:\Users\hermes paths and \1 groups" in (tree / "CHANGELOG.md").read_text()
