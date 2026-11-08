#!/usr/bin/env python3
"""Open a release-candidate PR when one is due.

A candidate is due when ALL of these hold:
- the last published release (draft releases don't count) is at least 14 days old;
- no candidate is pending: no open `release/v*` PR, and main's version is not already
  ahead of the last published tag (a merged but unpublished candidate);
- main's "Required checks" status for its exact commit is success (when it is missing,
  because GITHUB_TOKEN merges start no push CI, CI and Preview are dispatched on main
  and the train waits for the next day);
- something worth shipping landed since that tag: anything except Dependabot's
  dev-tools group merges.
Then it runs bump_version.py --next on a `release/vX` branch, opens the PR and dispatches
CI and Preview on it (PRs opened with GITHUB_TOKEN start no workflows by themselves).
Building, signing and publishing stay manual (docs/releasing.md).
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bump_version import next_patch, parse  # noqa: E402
from required_checks import CONTEXT, WATCHED, GitHub  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CADENCE = dt.timedelta(days=14)
BOT = "dependabot[bot]"


def last_published(releases: list[dict]) -> dict | None:
    published = [r for r in releases if not r["draft"] and r.get("published_at")]
    return max(published, key=lambda r: r["published_at"]) if published else None


def worth_shipping(commits: list[dict]) -> list[str]:
    """Subjects of the commits that justify a release (dev-tools merges don't)."""
    subjects = []
    for commit in commits:
        subject = commit["commit"]["message"].splitlines()[0]
        author = (commit.get("author") or {}).get("login")
        if author == BOT and "the dev-tools group" in subject:
            continue
        subjects.append(subject)
    return subjects


def decide(
    *,
    now: dt.datetime,
    release: dict | None,
    main_version: str,
    open_release_prs: list[dict],
    main_status: str | None,
    subjects: list[str],
) -> tuple[str, str]:
    """('go' | 'wait' | 'dispatch', reason)."""
    if release is None:
        return "wait", "no published release to measure from"
    published = dt.datetime.fromisoformat(release["published_at"].replace("Z", "+00:00"))
    if now - published < CADENCE:
        return "wait", f"{release['tag_name']} is only {(now - published).days} days old"
    if open_release_prs:
        return "wait", f"candidate PR #{open_release_prs[0]['number']} is still open"
    if parse(main_version) > parse(release["tag_name"].removeprefix("v")):
        return "wait", f"v{main_version} is merged but not published yet"
    if not subjects:
        return "wait", f"nothing to ship since {release['tag_name']}"
    if main_status is None:
        return "dispatch", "main has no Required checks status yet"
    if main_status != "success":
        return "wait", f"main's Required checks is {main_status}"
    return "go", f"{len(subjects)} change(s) since {release['tag_name']}"


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout


def prepare(gh: GitHub, main_sha: str, version: str, subjects: list[str], dry_run: bool) -> None:
    new = next_patch(version)
    branch = f"release/v{new}"
    notes = Path(tempfile.mkdtemp()) / "notes.md"  # outside the tree, so `git add -A` is exact
    notes.write_text("".join(f"- {s}\n" for s in subjects))
    git("switch", "-c", branch, main_sha)
    subprocess.run(
        [sys.executable, "scripts/bump_version.py", new, "--notes-file", str(notes)],
        cwd=ROOT,
        check=True,
    )
    subprocess.run(
        [sys.executable, "scripts/check_release_version.py", "--tag", f"v{new}"],
        cwd=ROOT,
        check=True,
    )
    subprocess.run([sys.executable, "scripts/check_beta_readiness.py"], cwd=ROOT, check=True)
    git("add", "-A")
    git(
        "-c",
        "user.name=github-actions[bot]",
        "-c",
        "user.email=41898282+github-actions[bot]@users.noreply.github.com",
        "commit",
        "-m",
        f"Version {new}",
    )
    if dry_run:
        print(git("show", "--stat", "HEAD"))
        return
    git("push", "origin", f"HEAD:refs/heads/{branch}")
    body = (
        f"Release candidate v{new}, opened by `release-train.yml`.\n\n"
        "Before merging: edit `docs/releases/v{new}.md` (it lists commit subjects only).\n"
        "After merging: build on the Mac from the merged commit (docs/releasing.md), then\n"
        "`scripts/bump_version.py --mark-published` once the packages are live.\n\n"
        "Changes since the last release:\n\n" + "".join(f"- {s}\n" for s in subjects)
    ).replace("{new}", new)
    pull, _ = gh.request(
        f"/repos/{gh.repo}/pulls",
        {"title": f"Version {new}", "head": branch, "base": "main", "body": body},
    )
    for workflow in WATCHED:
        gh.request(f"/repos/{gh.repo}/actions/workflows/{workflow}/dispatches", {"ref": branch})
    print(f"Opened #{pull['number']} for v{new}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="skip the 14-day wait (manual run)")
    args = parser.parse_args()
    gh = GitHub(args.repo, os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN", ""))
    now = dt.datetime.now(dt.timezone.utc)
    main_sha = gh.request(f"/repos/{gh.repo}/commits/main")[0]["sha"]
    with (ROOT / "pyproject.toml").open("rb") as stream:
        version = tomllib.load(stream)["project"]["version"]
    release = last_published(gh.paged(f"/repos/{gh.repo}/releases"))
    statuses = gh.request(f"/repos/{gh.repo}/commits/{main_sha}/status")[0]["statuses"]
    status = next((s["state"] for s in statuses if s["context"] == CONTEXT), None)
    commits = (
        gh.request(f"/repos/{gh.repo}/compare/{release['tag_name']}...{main_sha}")[0]["commits"]
        if release
        else []
    )
    prs = [
        p
        for p in gh.paged(f"/repos/{gh.repo}/pulls?state=open")
        if p["head"]["ref"].startswith("release/v")
    ]
    if args.force and release:
        release = dict(release, published_at="1970-01-01T00:00:00Z")
    verdict, reason = decide(
        now=now,
        release=release,
        main_version=version,
        open_release_prs=prs,
        main_status=status,
        subjects=worth_shipping(commits),
    )
    print(f"{verdict}: {reason}")
    if verdict == "dispatch" and not args.dry_run:
        for workflow in WATCHED:
            gh.request(f"/repos/{gh.repo}/actions/workflows/{workflow}/dispatches", {"ref": "main"})
    if verdict == "go":
        prepare(gh, main_sha, version, worth_shipping(commits), args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
