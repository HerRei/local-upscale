#!/usr/bin/env python3
"""Compile localsr-media, the macOS helper that decodes and encodes with the system codecs.

The helper is a single Swift source under packaging/macos/media-helper. It links
only Apple frameworks (AVFoundation, CoreMedia, CoreVideo, VideoToolbox), so it
adds no third-party code to a package. Release builds place it next to the
frozen worker; development builds go to build/media-helper/localsr-media.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "packaging" / "macos" / "media-helper"
DEFAULT_OUTPUT = ROOT / "build" / "media-helper" / "localsr-media"
DEPLOYMENT_TARGET = "14.0"


def sources() -> list[Path]:
    return sorted(SOURCE_DIR.glob("*.swift"))


def is_current(output: Path) -> bool:
    if not output.is_file():
        return False
    built = output.stat().st_mtime
    return all(source.stat().st_mtime <= built for source in sources() + [Path(__file__)])


def build(output: Path = DEFAULT_OUTPUT, *, arch: str | None = None, force: bool = False) -> Path:
    if sys.platform != "darwin":
        raise SystemExit("localsr-media is built on macOS only")
    if not force and is_current(output):
        return output
    swiftc = shutil.which("swiftc")
    if swiftc is None:
        raise SystemExit("swiftc is missing; install the Xcode Command Line Tools")
    target_arch = arch or os.environ.get("LOCALSR_TARGET_ARCH") or platform.machine()
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        swiftc,
        "-O",
        "-swift-version",
        "5",
        "-target",
        f"{target_arch}-apple-macos{DEPLOYMENT_TARGET}",
        "-framework",
        "AVFoundation",
        "-framework",
        "CoreMedia",
        "-framework",
        "CoreVideo",
        "-framework",
        "VideoToolbox",
        "-o",
        str(output),
        *map(str, sources()),
    ]
    subprocess.run(command, check=True)
    output.chmod(0o755)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--arch", help="arm64 or x86_64 (default: this machine)")
    parser.add_argument("--force", action="store_true", help="Rebuild even when up to date")
    args = parser.parse_args()
    built = build(args.output, arch=args.arch, force=args.force)
    print(f"localsr-media: {built}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
