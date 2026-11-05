#!/usr/bin/env python3
"""Decide whether a Dependabot PR may merge itself once "Required checks" passes.

Only the "dev-tools" groups of .github/dependabot.yml qualify, and only when every
updated dependency is on ALLOWLIST, every update is minor or patch, every commit on
the PR is a verified Dependabot commit and only the expected manifest/lock files
changed. Anything unknown fails closed. Run by .github/workflows/dependabot-automerge.yml
(pull_request_target, no checkout of the PR), which enables or disables GitHub's
auto-merge from the verdict.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from required_checks import GitHub  # noqa: E402

BOT = "dependabot[bot]"
COMMITTERS = {"web-flow", BOT}
# Test and lint tools only; build tools and anything shipped stay manual.
ALLOWLIST = {
    "uv": {"pytest", "pytest-timeout"},
    "npm_and_yarn": {
        "@types/node",
        "jsdom",
        "knip",
        "prettier",
        "prettier-plugin-svelte",
        "svelte-check",
        "typescript",
        "vitest",
    },
}
ALLOWED_PREFIXES = {"npm_and_yarn": ("@testing-library/",)}
FILES = {
    "uv": {"pyproject.toml", "uv.lock"},
    "npm_and_yarn": {"desktop/package.json", "desktop/package-lock.json"},
}
UPDATE_TYPES = {"version-update:semver-minor", "version-update:semver-patch"}


def allowed(ecosystem: str, name: str) -> bool:
    return name in ALLOWLIST.get(ecosystem, set()) or name.startswith(
        ALLOWED_PREFIXES.get(ecosystem, ())
    )


def verdict(
    *,
    pull: dict,
    event_sha: str,
    group: str,
    ecosystem: str,
    dependencies: list[dict],
    commits: list[dict],
    files: list[str],
    repo: str,
) -> list[str]:
    """Reasons against auto-merge; empty means eligible."""
    reasons = []
    if pull["user"]["login"] != BOT:
        reasons.append("PR not opened by Dependabot")
    if pull["base"]["ref"] != "main" or pull["head"]["repo"]["full_name"] != repo:
        reasons.append("not a same-repo PR into main")
    if pull["head"]["sha"] != event_sha:
        reasons.append("head moved since this event")
    if group != "dev-tools":
        reasons.append(f"group {group or 'none'} is not dev-tools")
    if ecosystem not in ALLOWLIST:
        reasons.append(f"ecosystem {ecosystem or 'unknown'} has no auto-merge allowlist")
    if not dependencies:
        reasons.append("no dependency metadata")
    for dep in dependencies:
        name = dep.get("dependencyName", "")
        if not allowed(ecosystem, name):
            reasons.append(f"{name or '?'} is not on the allowlist")
        if dep.get("updateType") not in UPDATE_TYPES:
            reasons.append(f"{name or '?'}: {dep.get('updateType') or 'unknown'} update")
    if not commits:
        reasons.append("no commits")
    for commit in commits:
        # GitHub itself commits Dependabot's work ("GitHub <noreply@github.com>"),
        # so the author is what identifies Dependabot, plus GitHub's signature.
        if (commit.get("author") or {}).get("login") != BOT:
            reasons.append(f"commit {commit['sha'][:12]} not authored by Dependabot")
        # GitHub verifies the committer's signature, so the committer must be GitHub
        # itself (web-flow) or Dependabot; anyone else could sign a commit that only
        # claims Dependabot as its author.
        if (commit.get("committer") or {}).get("login") not in COMMITTERS:
            reasons.append(f"commit {commit['sha'][:12]} not committed by GitHub or Dependabot")
        if not commit["commit"].get("verification", {}).get("verified"):
            reasons.append(f"commit {commit['sha'][:12]} not verified")
    if commits and commits[-1]["sha"] != event_sha:
        reasons.append("last commit is not the evaluated head")
    unexpected = sorted(set(files) - FILES.get(ecosystem, set()))
    if unexpected:
        reasons.append("unexpected files: " + ", ".join(unexpected[:5]))
    return reasons


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("number", type=int)
    parser.add_argument("event_sha")
    args = parser.parse_args()
    repo = os.environ["GITHUB_REPOSITORY"]
    gh = GitHub(repo, os.environ["GITHUB_TOKEN"])
    pull, _ = gh.request(f"/repos/{repo}/pulls/{args.number}")
    reasons = verdict(
        pull=pull,
        event_sha=args.event_sha,
        group=os.environ.get("DEPENDENCY_GROUP", ""),
        ecosystem=os.environ.get("PACKAGE_ECOSYSTEM", ""),
        dependencies=json.loads(os.environ.get("UPDATED_DEPENDENCIES_JSON") or "[]"),
        commits=gh.paged(f"/repos/{repo}/pulls/{args.number}/commits"),
        files=[f["filename"] for f in gh.paged(f"/repos/{repo}/pulls/{args.number}/files")],
        repo=repo,
    )
    eligible = not reasons
    print("eligible" if eligible else "not eligible:\n- " + "\n- ".join(reasons))
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as stream:
            stream.write(f"eligible={'true' if eligible else 'false'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
