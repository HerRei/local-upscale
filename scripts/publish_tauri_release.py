#!/usr/bin/env python3
"""Create or safely resume one immutable five-file Tauri prerelease draft."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path, PurePath

EXPECTED_PUBLIC_COUNT = 5


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def expected_assets(output: Path) -> dict[str, tuple[Path, str]]:
    listing = output / "release-files.txt"
    paths = [Path(line) for line in listing.read_text(encoding="utf-8").splitlines() if line]
    if len(paths) != EXPECTED_PUBLIC_COUNT:
        raise ValueError(f"expected exactly five public files, found {len(paths)}")
    result: dict[str, tuple[Path, str]] = {}
    for path in paths:
        resolved = path.resolve()
        if not resolved.is_relative_to(output.resolve()) or PurePath(path.name).name != path.name:
            raise ValueError(f"release file escaped prepared output: {path}")
        if not path.is_file() or path.name in result:
            raise ValueError(f"release file is missing or duplicated: {path.name}")
        result[path.name] = (path, sha256(path))
    if set(result) & {"release-files.txt", "matrix-selection.json"}:
        raise ValueError("private preparation manifests must not be public assets")
    if not {"SHA256SUMS", "release-index.json"}.issubset(result):
        raise ValueError("SHA256SUMS and release-index.json are required public assets")
    return result


def normalized_asset_digest(asset: dict[str, object]) -> str:
    value = str(asset.get("digest") or "")
    return value.removeprefix("sha256:")


def plan_draft_resume(
    release: dict[str, object],
    expected: dict[str, tuple[Path, str]],
    *,
    tag: str,
    commit: str,
) -> list[Path]:
    if release.get("tagName") != tag:
        raise ValueError("draft release tag does not match the requested tag")
    # GitHub ignores target_commitish when the tag already exists and commonly
    # reports the symbolic default branch (for example ``main``) instead.  A
    # concrete SHA is still fail-closed here; publish() independently peels the
    # local annotated tag and requires its exact commit before any draft work.
    target_commitish = str(release.get("targetCommitish") or "")
    if len(target_commitish) == 40 and target_commitish != commit:
        raise ValueError("draft release target commit does not match the requested commit")
    if release.get("isDraft") is not True:
        raise ValueError("published releases are immutable; refusing to modify the release")
    if release.get("isPrerelease") is not True:
        raise ValueError("existing draft is not marked as a prerelease")
    raw_assets = release.get("assets", [])
    if not isinstance(raw_assets, list):
        raise ValueError("GitHub returned malformed draft asset state")
    existing: dict[str, str] = {}
    for raw in raw_assets:
        if not isinstance(raw, dict):
            raise ValueError("GitHub returned malformed draft asset metadata")
        name = str(raw.get("name") or "")
        if name not in expected:
            raise ValueError(f"draft contains unexpected asset {name!r}")
        if name in existing:
            raise ValueError(f"draft contains duplicate asset {name!r}")
        digest = normalized_asset_digest(raw)
        if len(digest) != 64:
            raise ValueError(f"draft asset {name!r} has no SHA-256 digest")
        wanted = expected[name][1]
        if digest != wanted:
            raise ValueError(
                f"draft asset digest mismatch for {name}: existing={digest}, expected={wanted}"
            )
        existing[name] = digest
    return [path for name, (path, _digest) in expected.items() if name not in existing]


def verify_published_release(
    release: dict[str, object],
    expected: dict[str, tuple[Path, str]],
    *,
    tag: str,
    commit: str,
    title: str,
) -> None:
    if release.get("tagName") != tag or release.get("name") != title:
        raise ValueError("published release identity does not match the requested tag and title")
    if release.get("isDraft") is not False or release.get("isPrerelease") is not True:
        raise ValueError("GitHub release did not reach published prerelease state")
    # Reuse the strict asset and commitish validation by projecting the final
    # release into draft state. No network mutation happens in this helper.
    projected = dict(release)
    projected["isDraft"] = True
    if plan_draft_resume(projected, expected, tag=tag, commit=commit):
        raise ValueError("published release is missing expected assets")


def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=check, text=True, capture_output=True)


def view_release(tag: str) -> dict[str, object] | None:
    result = run(
        [
            "gh",
            "release",
            "view",
            tag,
            "--json",
            "tagName,name,isDraft,isPrerelease,targetCommitish,assets,url",
        ],
        check=False,
    )
    if result.returncode != 0:
        combined = (result.stdout + result.stderr).lower()
        if "release not found" in combined or "not found" in combined:
            return None
        raise RuntimeError(f"could not inspect GitHub release: {result.stderr.strip()}")
    value = json.loads(result.stdout)
    if not isinstance(value, dict):
        raise ValueError("GitHub release response is not an object")
    return value


def verify_local_tag(tag: str, commit: str) -> None:
    if len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit):
        raise ValueError("release commit must be a lowercase 40-character SHA")
    resolved = run(["git", "rev-parse", f"{tag}^{{commit}}"])
    if resolved.stdout.strip() != commit:
        raise ValueError(
            f"tag {tag} resolves to {resolved.stdout.strip()}, expected release commit {commit}"
        )


def verify_downloads(directory: Path, expected: dict[str, tuple[Path, str]]) -> None:
    downloaded = {path.name: path for path in directory.iterdir() if path.is_file()}
    if set(downloaded) != set(expected):
        raise ValueError(
            "downloaded release asset set differs: "
            f"found={sorted(downloaded)}, expected={sorted(expected)}"
        )
    for name, path in downloaded.items():
        if sha256(path) != expected[name][1]:
            raise ValueError(f"downloaded release asset digest mismatch: {name}")
    checksum_lines = downloaded["SHA256SUMS"].read_text(encoding="utf-8").splitlines()
    checksum_names: set[str] = set()
    for line in checksum_lines:
        digest, separator, name = line.partition("  ")
        if not separator or name not in downloaded or sha256(downloaded[name]) != digest:
            raise ValueError(f"invalid SHA256SUMS entry: {line!r}")
        checksum_names.add(name)
    installers = set(expected) - {"SHA256SUMS", "release-index.json"}
    if checksum_names != installers:
        raise ValueError("SHA256SUMS must cover exactly the three installers")


def publish(tag: str, commit: str, title: str, notes: Path, output: Path) -> str:
    expected = expected_assets(output)
    verify_local_tag(tag, commit)
    release = view_release(tag)
    if release is None:
        run(
            [
                "gh",
                "release",
                "create",
                tag,
                "--target",
                commit,
                "--title",
                title,
                "--notes-file",
                str(notes),
                "--verify-tag",
                "--prerelease",
                "--draft",
            ]
        )
        release = view_release(tag)
        if release is None:
            raise RuntimeError("GitHub did not return the newly created draft")
    missing = plan_draft_resume(release, expected, tag=tag, commit=commit)
    for path in missing:
        run(["gh", "release", "upload", tag, str(path)])
    import time
    for attempt in range(60):
        complete = view_release(tag)
        if complete is None:
            raise RuntimeError("GitHub draft disappeared during upload")
        try:
            if not plan_draft_resume(complete, expected, tag=tag, commit=commit):
                break
        except ValueError as e:
            if "has no SHA-256 digest" in str(e):
                time.sleep(10)
                continue
            raise
        time.sleep(10)
    else:
        raise RuntimeError("GitHub draft is still missing expected assets after upload (timed out waiting for digests)")
    with tempfile.TemporaryDirectory(prefix="localsr-release-verify-", dir=output) as temporary:
        directory = Path(temporary)
        run(["gh", "release", "download", tag, "--dir", str(directory)])
        verify_downloads(directory, expected)
    run(
        [
            "gh",
            "release",
            "edit",
            tag,
            "--title",
            title,
            "--notes-file",
            str(notes),
            "--draft=false",
            "--prerelease",
        ]
    )
    final = view_release(tag)
    if final is None:
        raise RuntimeError("GitHub release disappeared after publication")
    verify_published_release(final, expected, tag=tag, commit=commit, title=title)
    return str(final.get("url") or "")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--notes", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.notes.is_file():
        raise FileNotFoundError(args.notes)
    print(publish(args.tag, args.commit, args.title, args.notes, args.output.resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
