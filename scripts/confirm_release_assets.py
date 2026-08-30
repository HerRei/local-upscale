#!/usr/bin/env python3
"""Confirm the exact GitHub Release asset set and prepare safe stale-asset cleanup."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

SAFE_ASSET_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024**2), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compare(
    index: dict[str, object],
    release: dict[str, object],
    *,
    index_path: Path | None = None,
) -> dict[str, list[str]]:
    expected_items = index["assets"]
    actual_items = release["assets"]
    if not isinstance(expected_items, list) or not isinstance(actual_items, list):
        raise ValueError("release assets must be lists")
    expected_names = [item["filename"] for item in expected_items]
    if len(expected_names) != len(set(expected_names)):
        raise RuntimeError("Release index contains duplicate asset names")
    expected = {item["filename"]: item for item in expected_items}
    if index_path:
        expected["release-index.json"] = {
            "filename": "release-index.json",
            "size": index_path.stat().st_size,
            "sha256": sha256(index_path),
        }
    actual_names = [item["name"] for item in actual_items]
    if len(actual_names) != len(set(actual_names)):
        raise RuntimeError("GitHub reports duplicate release asset names")
    actual = {item["name"]: item for item in actual_items}
    return {
        "missing": sorted(set(expected) - set(actual)),
        "wrong_size": sorted(
            name
            for name in set(expected) & set(actual)
            if expected[name].get("size") is not None
            and expected[name]["size"] != actual[name]["size"]
        ),
        "wrong_digest": sorted(
            name
            for name in set(expected) & set(actual)
            if expected[name].get("sha256") is not None
            and actual[name].get("digest") != f"sha256:{expected[name]['sha256']}"
        ),
        "unexpected": sorted(set(actual) - set(expected)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--release-json", type=Path, required=True)
    parser.add_argument("--allow-unexpected", action="store_true")
    parser.add_argument("--unexpected-output", type=Path)
    args = parser.parse_args()
    index = json.loads(args.index.read_text(encoding="utf-8"))
    release = json.loads(args.release_json.read_text(encoding="utf-8"))
    result = compare(index, release, index_path=args.index)
    if result["missing"] or result["wrong_size"] or result["wrong_digest"]:
        raise RuntimeError(
            "release asset confirmation failed: "
            f"missing={result['missing']}, wrong_size={result['wrong_size']}, "
            f"wrong_digest={result['wrong_digest']}"
        )
    if args.unexpected_output:
        for name in result["unexpected"]:
            if not SAFE_ASSET_NAME.fullmatch(name):
                raise RuntimeError(f"refusing unsafe unexpected asset name: {name!r}")
        args.unexpected_output.write_text(
            "".join(name + "\n" for name in result["unexpected"]), encoding="utf-8"
        )
    if result["unexpected"] and not args.allow_unexpected:
        raise RuntimeError(f"release asset confirmation failed: unexpected={result['unexpected']}")
    print(
        f"Confirmed {len(index['assets'])} expected GitHub Release assets"
        + (f"; {len(result['unexpected'])} stale assets remain" if result["unexpected"] else "")
    )


if __name__ == "__main__":
    main()
