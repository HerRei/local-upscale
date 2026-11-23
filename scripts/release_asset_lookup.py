#!/usr/bin/env python3
"""Find release assets by tag, including recently created drafts."""

import json
import subprocess
import sys
import time


def release_assets(repo: str, tag: str) -> list[dict]:
    for attempt in range(5):
        result = subprocess.run(
            ["gh", "api", "--paginate", "--slurp", f"repos/{repo}/releases?per_page=100"],
            check=True,
            capture_output=True,
            text=True,
        )
        pages = json.loads(result.stdout)
        # gh --slurp returns one array per page.
        releases = (
            [release for page in pages for release in page]
            if pages and isinstance(pages[0], list)
            else pages
        )
        matches = [release for release in releases if release.get("tag_name") == tag]
        if len(matches) > 1:
            raise ValueError(f"multiple releases match {tag}")
        if matches:
            return matches[0]["assets"]
        if attempt < 4:
            time.sleep(5)
    raise ValueError(f"release {tag} was not found after five attempts")


if __name__ == "__main__":
    for asset in release_assets(sys.argv[1], sys.argv[2]):
        print("\t".join(str(asset.get(key) or "") for key in ("name", "digest", "state")))
