"""Validate macOS application metadata that affects normal GUI interaction."""

from __future__ import annotations

import os
import plistlib
import sys
import tomllib
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: smoke_macos_bundle.py /path/to/LocalSR.app")

    bundle = Path(sys.argv[1]).resolve()
    with (bundle / "Contents" / "Info.plist").open("rb") as stream:
        metadata = plistlib.load(stream)

    project_root = Path(__file__).resolve().parent.parent
    with (project_root / "pyproject.toml").open("rb") as stream:
        expected_version = tomllib.load(stream)["project"]["version"]
    expected_bundle_version = expected_version.split("-", 1)[0]
    expected_build_number = str(int(expected_bundle_version.rsplit(".", 1)[-1]))

    if metadata.get("LSBackgroundOnly") is True:
        raise SystemExit("LocalSR.app is incorrectly marked as a background-only app")
    if metadata.get("CFBundlePackageType") != "APPL":
        raise SystemExit("LocalSR.app is missing the APPL bundle type")
    if metadata.get("CFBundleExecutable") != "LocalSR":
        raise SystemExit("LocalSR.app does not point at the GUI executable")
    if metadata.get("CFBundleShortVersionString") != expected_bundle_version:
        raise SystemExit("LocalSR.app short version does not match pyproject.toml")
    if metadata.get("CFBundleVersion") != expected_build_number:
        raise SystemExit("LocalSR.app bundle version does not match pyproject.toml")
    if metadata.get("LocalSRReleaseVersion") != expected_version:
        raise SystemExit("LocalSR.app exact release identity does not match pyproject.toml")

    dialog_helper = bundle / "Contents" / "Frameworks" / "LocalSRDialog"
    if not dialog_helper.is_file() or not os.access(dialog_helper, os.X_OK):
        raise SystemExit("LocalSR.app is missing its executable native dialog helper")

    print("macOS foreground metadata and native dialog helper succeeded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
