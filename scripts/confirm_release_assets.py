#!/usr/bin/env python3
"""Confirm that GitHub reports every prepared release asset at the expected size."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--release-json", type=Path, required=True)
    args = parser.parse_args()
    expected_data = json.loads(args.index.read_text(encoding="utf-8"))
    expected = {item["filename"]: item["size"] for item in expected_data["assets"]}
    release = json.loads(args.release_json.read_text(encoding="utf-8"))
    actual = {item["name"]: item["size"] for item in release["assets"]}
    missing = sorted(set(expected) - set(actual))
    wrong_size = sorted(
        name
        for name in set(expected) & set(actual)
        if expected[name] is not None and expected[name] != actual[name]
    )
    if missing or wrong_size:
        raise RuntimeError(
            f"release asset confirmation failed: missing={missing}, wrong_size={wrong_size}"
        )
    print(f"Confirmed {len(expected)} GitHub Release assets")


if __name__ == "__main__":
    main()
