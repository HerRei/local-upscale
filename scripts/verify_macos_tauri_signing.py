#!/usr/bin/env python3
"""Fail unless a Tauri DMG contains a Developer ID app with a stapled notarization ticket."""

from __future__ import annotations

import argparse
import json
import plistlib
import re
import subprocess
from pathlib import Path


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, capture_output=True, text=True)


def identity_details(text: str) -> tuple[str, str]:
    authorities = re.findall(r"^Authority=(.+)$", text, flags=re.MULTILINE)
    team_match = re.search(r"^TeamIdentifier=(.+)$", text, flags=re.MULTILINE)
    developer = next(
        (
            authority
            for authority in authorities
            if authority.startswith("Developer ID Application:")
        ),
        "",
    )
    if not developer or not team_match:
        raise ValueError("the app is not signed with a Developer ID Application identity")
    return developer, team_match.group(1).strip()


def verify(dmg: Path, expected_team_id: str) -> dict[str, object]:
    attach = run(["hdiutil", "attach", "-nobrowse", "-readonly", "-plist", str(dmg)])
    payload = plistlib.loads(attach.stdout.encode())
    entities = payload.get("system-entities", [])
    mounts = [entry.get("mount-point") for entry in entities if entry.get("mount-point")]
    devices = [entry.get("dev-entry") for entry in entities if entry.get("dev-entry")]
    if not mounts or not devices:
        raise ValueError("hdiutil did not report a mounted signed app")
    try:
        apps = sorted(Path(mounts[-1]).glob("*.app"))
        if len(apps) != 1:
            raise ValueError(f"expected one signed app, found {len(apps)}")
        app = apps[0]
        run(["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app)])
        details_result = subprocess.run(
            ["codesign", "--display", "--verbose=4", str(app)],
            check=True,
            capture_output=True,
            text=True,
        )
        developer_id, team_id = identity_details(
            details_result.stdout + "\n" + details_result.stderr
        )
        if team_id != expected_team_id:
            raise ValueError(f"signed Team ID {team_id!r} does not match expected Team ID")
        run(["xcrun", "stapler", "validate", str(app)])
        run(["spctl", "--assess", "--type", "execute", "--verbose=4", str(app)])
        run(["xcrun", "stapler", "validate", str(dmg)])
    finally:
        run(["hdiutil", "detach", str(devices[-1])])
    return {
        "status": "developer-id-notarized",
        "developer_id": developer_id,
        "team_id": team_id,
        "notarized": True,
        "stapled": True,
        "gatekeeper_accepted": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dmg", type=Path)
    parser.add_argument("--team-id", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = verify(args.dmg.resolve(), args.team_id.strip())
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
