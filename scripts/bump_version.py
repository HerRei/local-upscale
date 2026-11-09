#!/usr/bin/env python3
"""Move every release-facing version string to a new beta candidate, or mark it published.

``bump_version.py 0.3.1-beta`` (or ``--next``) prepares a candidate: the Python, npm,
Cargo and Tauri versions, both lock files' own entries, the release plan and readiness
register, the artifact manifest, a CHANGELOG section and a release-notes draft. The
packages of a candidate are not out yet, so the release plan marks every target
``candidate`` and README/docs say which version is still the published one.

``bump_version.py --mark-published`` runs after the packages are live: targets become
``published``, the CHANGELOG section gets its date and the candidate notices go away.
Prose that describes published packages (README downloads, docs/platforms.md,
KNOWN_LIMITATIONS.md) is listed for a human to update; this script never invents test
results.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import tomllib
from pathlib import Path

from release_targets import artifact_entries

ROOT = Path(__file__).resolve().parents[1]
BETA = re.compile(r"(\d+)\.(\d+)\.(\d+)-beta(?:\.(\d+))?")
CANDIDATE_START = "<!-- release-candidate -->"
CANDIDATE_END = "<!-- /release-candidate -->"
PROSE_FILES = ("README.md", "docs/platforms.md", "KNOWN_LIMITATIONS.md")


def parse(version: str) -> tuple[int, int, int, int]:
    match = BETA.fullmatch(version)
    if not match:
        raise ValueError(f"not a beta version: {version!r} (expected X.Y.Z-beta or X.Y.Z-beta.N)")
    major, minor, patch, pre = match.groups()
    return int(major), int(minor), int(patch), int(pre or 0)


def pep440(version: str) -> str:
    major, minor, patch, pre = parse(version)
    return f"{major}.{minor}.{patch}b{pre}"


def next_patch(version: str) -> str:
    major, minor, patch, _ = parse(version)
    return f"{major}.{minor}.{patch + 1}-beta"


def current_version(root: Path) -> str:
    with (root / "pyproject.toml").open("rb") as stream:
        return str(tomllib.load(stream)["project"]["version"])


def replace(root: Path, relative: str, old: str, new: str, count: int = 1) -> None:
    """Replace ``old`` exactly ``count`` times; anything else means the file layout changed."""
    path = root / relative
    text = path.read_text(encoding="utf-8")
    found = text.count(old)
    if found != count:
        raise ValueError(f"{relative}: expected {count} x {old!r}, found {found}")
    path.write_text(text.replace(old, new), encoding="utf-8")


def sub(root: Path, relative: str, pattern: str, new: str) -> None:
    path = root / relative
    text = path.read_text(encoding="utf-8")
    # A callable keeps `new` literal: commit subjects may contain backslashes.
    result, found = re.subn(pattern, lambda _: new, text, count=1, flags=re.MULTILINE | re.DOTALL)
    if found != 1:
        raise ValueError(f"{relative}: pattern {pattern!r} not found")
    path.write_text(result, encoding="utf-8")


def write_manifest(root: Path, version: str) -> None:
    data = {"schema_version": 1, "version": version, "artifacts": artifact_entries(version)}
    (root / "ci/tauri-release-artifacts.json").write_text(json.dumps(data, indent=2) + "\n")


def candidate_notice(current: str, new: str) -> str:
    return (
        f"{CANDIDATE_START}\n"
        f"v{new} is in preparation; the downloads below are still v{current}.\n"
        f"[Draft release notes](docs/releases/v{new}.md).\n"
        f"{CANDIDATE_END}"
    )


def set_readme_notice(root: Path, notice: str | None) -> None:
    path = root / "README.md"
    text = path.read_text(encoding="utf-8")
    block = re.compile(
        re.escape(CANDIDATE_START) + r".*?" + re.escape(CANDIDATE_END) + r"\n?\n?", re.S
    )
    text = block.sub("", text)
    if notice is not None:
        anchor = "\n## Download\n"
        if text.count(anchor) != 1:
            raise ValueError("README.md: expected exactly one '## Download' heading")
        text = text.replace(anchor, f"\n{notice}\n{anchor}")
    path.write_text(text, encoding="utf-8")


def bump(root: Path, new: str, notes: str, today: dt.date) -> str:
    current = current_version(root)
    if parse(new) <= parse(current):
        raise ValueError(f"{new} is not newer than {current}")
    if (root / f"docs/releases/v{new}.md").exists():
        raise ValueError(f"docs/releases/v{new}.md already exists")
    old_pep, new_pep = pep440(current), pep440(new)

    replace(root, "pyproject.toml", f'version = "{current}"', f'version = "{new}"')
    replace(root, "src/localsr/__init__.py", f'__version__ = "{current}"', f'__version__ = "{new}"')
    replace(
        root,
        "src/localsr/__init__.py",
        f"normalizes it to {old_pep}.",
        f"normalizes it to {new_pep}.",
    )
    replace(
        root,
        "uv.lock",
        f'name = "localsr"\nversion = "{old_pep}"',
        f'name = "localsr"\nversion = "{new_pep}"',
    )
    replace(root, "desktop/package.json", f'"version": "{current}"', f'"version": "{new}"')
    # The lock repeats the root package version twice: top level and packages[""].
    replace(
        root, "desktop/package-lock.json", f'"version": "{current}"', f'"version": "{new}"', count=2
    )
    replace(root, "desktop/src-tauri/Cargo.toml", f'version = "{current}"', f'version = "{new}"')
    replace(
        root,
        "desktop/src-tauri/Cargo.lock",
        f'name = "localsr-next"\nversion = "{current}"',
        f'name = "localsr-next"\nversion = "{new}"',
    )
    replace(
        root, "desktop/src-tauri/tauri.conf.json", f'"version": "{current}"', f'"version": "{new}"'
    )
    replace(root, "desktop/src/lib/demo.ts", f"app_version: '{current}'", f"app_version: '{new}'")

    plan = root / "ci/public-beta-release.json"
    replace(root, "ci/public-beta-release.json", f'"version": "{current}"', f'"version": "{new}"')
    plan.write_text(plan.read_text().replace('"package": "published"', '"package": "candidate"'))
    replace(root, "ci/public-beta-readiness.json", f'"release": "{current}"', f'"release": "{new}"')
    write_manifest(root, new)

    sub(
        root,
        "docs/releasing.md",
        r"^Current release: \*\*v[^*]+\*\*(?:; release candidate: \*\*v[^*]+\*\*)?",
        f"Current release: **v{current}**; release candidate: **v{new}**",
    )
    set_readme_notice(root, candidate_notice(current, new))
    changelog_path = root / "CHANGELOG.md"
    changelog = changelog_path.read_text(encoding="utf-8")
    heading = f"## [{new}] - Unreleased"
    if heading in changelog:
        if changelog.count(heading) != 1:
            raise ValueError(f"CHANGELOG.md: expected one {heading!r} heading")
        start = changelog.index(heading)
        next_section = changelog.find("\n## ", start + len(heading))
        if next_section < 0:
            next_section = len(changelog)
        section = changelog[start:next_section].rstrip()
        remainder = changelog[next_section:].lstrip("\n")
        changelog = f"{changelog[:start]}{section}\n\n{notes.strip()}\n\n{remainder}"
        changelog_path.write_text(changelog, encoding="utf-8")
    else:
        sub(
            root,
            "CHANGELOG.md",
            r"\A# Changelog\n\n",
            f"# Changelog\n\n## [{new}] - Unreleased\n\n{notes.strip()}\n\n",
        )
    (root / f"docs/releases/v{new}.md").write_text(
        f"# LocalSR v{new}\n\n"
        f"Release candidate, not published yet (prepared {today.isoformat()}).\n\n"
        f"## What changed\n\n{notes.strip()}\n",
        encoding="utf-8",
    )
    return current


def mark_published(root: Path, today: dt.date) -> list[str]:
    version = current_version(root)
    replace(
        root,
        "CHANGELOG.md",
        f"## [{version}] - Unreleased",
        f"## [{version}] - {today.isoformat()}",
    )
    plan = root / "ci/public-beta-release.json"
    plan.write_text(plan.read_text().replace('"package": "candidate"', '"package": "published"'))
    sub(
        root,
        "docs/releasing.md",
        r"^Current release: \*\*v[^*]+\*\*; release candidate: \*\*v[^*]+\*\*",
        f"Current release: **v{version}**",
    )
    set_readme_notice(root, None)
    sub(
        root, f"docs/releases/v{version}.md", r"^Release candidate, not published yet[^\n]*\n\n", ""
    )
    # README must still name the version for check_public_beta; the downloads prose does.
    return [
        relative for relative in PROSE_FILES if f"v{version}" not in (root / relative).read_text()
    ]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("version", nargs="?", help="new version, e.g. 0.3.1-beta")
    group.add_argument("--next", action="store_true", help="next patch version")
    group.add_argument("--mark-published", action="store_true")
    parser.add_argument("--notes-file", type=Path, help="Markdown bullets for CHANGELOG and notes")
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args()
    today = dt.date.today()
    if args.mark_published:
        stale = mark_published(args.root, today)
        print(f"Marked v{current_version(args.root)} published.")
        if stale:
            print("Update the published-package prose by hand: " + ", ".join(stale))
        return 0
    new = next_patch(current_version(args.root)) if args.next else args.version
    notes = (
        args.notes_file.read_text(encoding="utf-8") if args.notes_file else "- Dependency updates."
    )
    previous = bump(args.root, new, notes, today)
    print(f"Prepared v{new} (was v{previous}). Next: uv lock --check, the release checks, a PR.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
