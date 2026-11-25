"""Gather the Windows and Linux files for signing from the draft release, not from CI artifacts.

The signing step (`beta_update_signing.py platforms`) needs every package next to its
reports. Downloading the full run artifacts is about 45 GB, and during the 0.6.0 release
GitHub answered `503 Egress is over the account limit` for them (#180). The packages are
already on the draft release (the workflows attach them with `release_tag`); only the small
reports exist only in the runs, as the `reports-<target>` artifacts.

Usage:
  fetch_release_platforms.py --tag v0.6.1-beta --run <windows run> --run <linux run> \\
      --out build/release-<version>/platforms

Each target gets its own folder (`windows-x86_64-cpu`, …) as with `gh run download`. A file
both in the reports and on the release must be identical, and every release file is checked
against the digest GitHub recorded for it. The signing step then checks each package against
its own checksum file and smoke report, as before.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = "HerRei/local-upscale"
REPORT_ARTIFACT_PREFIX = "reports-"
# Report files are named "<package>.<suffix>"; their package name is what to fetch.
REPORT_SUFFIXES = (
    ".smoke.backend.json",
    ".smoke.json",
    ".architecture.json",
    ".codec-policy.json",
    ".nvidia.json",
    ".libc.json",
    ".dependencies.json",
    ".offline-window.json",
    ".offline-window.png",
    ".engine-payload.json",
    ".engine-update.json",
    ".sha256",
    ".json",
)


def gh(*arguments: str) -> str:
    completed = subprocess.run(["gh", *arguments], capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise SystemExit(f"gh {' '.join(arguments)} failed: {completed.stderr.strip()}")
    return completed.stdout


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def package_stems(folder: Path) -> set[str]:
    stems = set()
    for path in folder.iterdir():
        for suffix in REPORT_SUFFIXES:
            if path.name.endswith(suffix) and len(path.name) > len(suffix):
                stems.add(path.name[: -len(suffix)])
                break
    stems.discard("dependency-wheelhouse")
    return stems


def assets_for(stems: set[str], assets: list[dict]) -> list[dict]:
    """Release assets of these packages, without signatures (the Mac makes those)."""
    wanted = []
    for asset in assets:
        name = asset["name"]
        if name.endswith(".sig"):
            continue
        if any(name == stem or name.startswith(stem + ".") for stem in stems):
            wanted.append(asset)
    return wanted


def fetch_reports(runs: list[str], out: Path, repo: str) -> list[Path]:
    folders = []
    with tempfile.TemporaryDirectory(prefix="localsr-reports-") as scratch:
        for run in runs:
            target = Path(scratch) / run
            gh(
                "run",
                "download",
                run,
                "--repo",
                repo,
                "-p",
                f"{REPORT_ARTIFACT_PREFIX}*",
                "-D",
                str(target),
            )
            for artifact in sorted(target.iterdir()):
                if not artifact.name.startswith(REPORT_ARTIFACT_PREFIX):
                    continue
                folder = out / artifact.name[len(REPORT_ARTIFACT_PREFIX) :]
                folder.mkdir(parents=True, exist_ok=True)
                for item in artifact.iterdir():
                    shutil.move(str(item), folder / item.name)
                folders.append(folder)
    if not folders:
        raise SystemExit(f"no {REPORT_ARTIFACT_PREFIX}* artifacts in runs {', '.join(runs)}")
    return folders


def fetch_assets(tag: str, folder: Path, wanted: list[dict], repo: str) -> int:
    fetched = 0
    for asset in wanted:
        name = asset["name"]
        digest = (asset.get("digest") or "").removeprefix("sha256:")
        local = folder / name
        if local.exists():
            # A checksum file comes with the reports too; both copies must agree.
            if digest and sha256(local) != digest:
                raise SystemExit(f"{folder.name}/{name} differs from the release's copy")
            continue
        # Download next to the target and move it in only once it is complete and checked: a
        # cut-off download must not stay behind under the final name, where the next run
        # would take it for a report's copy and refuse it as different.
        with tempfile.TemporaryDirectory(prefix=".fetch-", dir=folder) as partial:
            gh("release", "download", tag, "--repo", repo, "-p", name, "-D", partial)
            downloaded = Path(partial) / name
            if not downloaded.is_file():
                raise SystemExit(f"{name} was not downloaded from {tag}")
            if digest and sha256(downloaded) != digest:
                raise SystemExit(f"{name} does not match the digest GitHub recorded for it")
            downloaded.replace(local)
        fetched += 1
    return fetched


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tag", required=True)
    parser.add_argument("--run", action="append", required=True, help="workflow run id")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--repo", default=REPO)
    args = parser.parse_args(argv)
    assets = json.loads(gh("release", "view", args.tag, "--repo", args.repo, "--json", "assets"))[
        "assets"
    ]
    folders = fetch_reports(args.run, args.out, args.repo)
    problems = []
    for folder in folders:
        wanted = assets_for(package_stems(folder), assets)
        packages = [a for a in wanted if not a["name"].endswith((".json", ".sha256"))]
        if not packages:
            problems.append(f"{folder.name}: no package of this target is on {args.tag}")
            continue
        fetched = fetch_assets(args.tag, folder, wanted, args.repo)
        print(f"{folder.name}: {fetched} file(s) from {args.tag}, {len(wanted)} expected")
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
