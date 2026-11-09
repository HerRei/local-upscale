from __future__ import annotations

import datetime as dt
import importlib.util
import json
import re
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
    # Exercise a fresh release bump, independent of notes already drafted for it.
    changelog = tmp_path / "CHANGELOG.md"
    text = changelog.read_text()
    next_heading = f"## [{bump_version.next_patch(VERSION)}] - Unreleased\n"
    if next_heading in text:
        before, after = text.split(next_heading, 1)
        text = before + after[after.index(f"## [{VERSION}]") :]
        changelog.write_text(text)
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
    # The previous version may never have been published: the notice names the version
    # the README download labels still show.
    published = bump_version.published_version(tree)
    assert f"the downloads below are still v{published}" in readme
    assert f"v{published}:**" in readme
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


def test_bump_keeps_existing_unreleased_notes_for_the_new_version(tree: Path):
    new = bump_version.next_patch(VERSION)
    changelog = tree / "CHANGELOG.md"
    previous_notes = changelog.read_text().split("## [0.3.7-beta]", 1)[1]
    changelog.write_text(
        f"# Changelog\n\n## [{new}] - Unreleased\n\n### Added\n\n- Existing notes.\n\n"
        + f"## [0.3.7-beta]{previous_notes}",
        encoding="utf-8",
    )

    bump_version.bump(tree, new, "- New notes.", dt.date(2026, 11, 16))

    updated = changelog.read_text()
    assert updated.count(f"## [{new}] - Unreleased") == 1
    assert "- Existing notes." in updated


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


def test_notice_names_the_last_published_version_not_the_previous_one(tree: Path):
    readme = tree / "README.md"
    readme.write_text(
        re.sub(
            r"\*\*(macOS|Windows|Linux) v[^:*]+:\*\*", r"**\1 v0.2.5-beta:**", readme.read_text()
        )
    )
    assert bump_version.published_version(tree) == "0.2.5-beta"

    new = bump_version.next_patch(VERSION)
    assert VERSION != "0.2.5-beta"
    bump_version.bump(tree, new, "- x", dt.date(2026, 11, 16))
    text = readme.read_text()
    assert f"v{new} is in preparation; the downloads below are still v0.2.5-beta." in text
    assert f"still v{VERSION}" not in text


def test_bump_moves_an_unreleased_section_under_the_new_version(tree: Path):
    new = bump_version.next_patch(VERSION)
    changelog = tree / "CHANGELOG.md"
    changelog.write_text(
        changelog.read_text().replace(
            "# Changelog\n\n", "# Changelog\n\n## [Unreleased]\n\n### Fixed\n\n- Real fix.\n\n", 1
        ),
        encoding="utf-8",
    )
    bump_version.bump(tree, new, "- Dependency updates.", dt.date(2026, 11, 16))

    updated = changelog.read_text()
    assert "[Unreleased]" not in updated
    assert updated.count(f"## [{new}] - Unreleased") == 1
    section = updated.split(f"## [{new}] - Unreleased")[1].split("\n## [")[0]
    assert "- Real fix." in section
    assert "- Dependency updates." in section
