#!/usr/bin/env python3
"""Build the LocalSR macOS disk image with the drag-to-Applications window.

Opening the image shows one Finder window without toolbar or sidebar: the
LocalSR icon on the left, the Applications shortcut on the right and the
terracotta arrow between them (packaging/macos/installer/background*.png,
rendered by build_background.py from the layout constants below). dmgbuild
writes the window layout into the volume's .DS_Store and combines the 1x and
2x backgrounds into one Retina TIFF.

Needs the "macos-release" extra (dmgbuild) in the environment that runs it:

    .venv/bin/python scripts/make_macos_dmg.py --app path/LocalSR.app \
        --out path/LocalSR.dmg --volname "LocalSR 0.1.5-beta"

The image is not signed here; the release script signs, notarizes and staples
it afterwards.
"""

from __future__ import annotations

import argparse
import plistlib
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "packaging" / "macos" / "installer"
VOLUME_ICON = ROOT / "packaging" / "icons" / "LocalSR.icns"

# Window layout in points. The background is taller than the visible content
# (640x360 below a 32 pt macOS 26 title bar) so older, shorter title bars never
# uncover an unpainted strip. Icon positions are icon centres.
WINDOW_SIZE = (640, 392)
WINDOW_ORIGIN = (400, 300)  # Finder measures y from the bottom of the screen
BACKGROUND_SIZE = (640, 400)
ICON_SIZE = 128
APP_ICON_CENTER = (160, 170)
APPLICATIONS_ICON_CENTER = (480, 170)


def settings(app: Path) -> dict:
    # dmgbuild's defaults already hide the toolbar, sidebar, path and status
    # bars and put labels below the icons.
    return {
        "format": "UDZO",
        "files": [str(app)],
        "symlinks": {"Applications": "/Applications"},
        "icon": str(VOLUME_ICON),
        "background": str(INSTALLER / "background.png"),
        "window_rect": (WINDOW_ORIGIN, WINDOW_SIZE),
        "icon_size": float(ICON_SIZE),
        "text_size": 13.0,
        "icon_locations": {
            app.name: APP_ICON_CENTER,
            "Applications": APPLICATIONS_ICON_CENTER,
        },
    }


def verify(dmg: Path, app_name: str, signed: bool = True) -> None:
    """Mount the finished image read-only and check what Finder will show."""
    with tempfile.TemporaryDirectory(prefix="localsr-dmg-verify-") as mount:
        out = subprocess.run(
            [
                "hdiutil",
                "attach",
                "-readonly",
                "-nobrowse",
                "-noautoopen",
                "-plist",
                "-mountpoint",
                mount,
                str(dmg),
            ],
            check=True,
            capture_output=True,
        ).stdout
        devices = [e["dev-entry"] for e in plistlib.loads(out)["system-entities"]]
        try:
            volume = Path(mount)
            found = {p.name for p in volume.iterdir()} - {".fseventsd", ".Trashes"}
            expected = {
                app_name,
                "Applications",
                ".DS_Store",
                ".background.tiff",
                ".VolumeIcon.icns",
            }
            if found != expected:
                raise SystemExit(f"unexpected volume contents: {sorted(found ^ expected)}")
            if (volume / "Applications").readlink() != Path("/Applications"):
                raise SystemExit("Applications is not a link to /Applications")
            if signed:
                subprocess.run(
                    ["codesign", "--verify", "--deep", "--strict", str(volume / app_name)],
                    check=True,
                )
        finally:
            subprocess.run(["hdiutil", "detach", "-quiet", devices[0]], check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--app", required=True, type=Path, help="signed LocalSR.app")
    parser.add_argument("--out", required=True, type=Path, help="disk image to write")
    parser.add_argument("--volname", required=True, help="volume name shown in Finder")
    parser.add_argument(
        "--unsigned", action="store_true", help="skip the code signature check (test apps)"
    )
    args = parser.parse_args()

    import dmgbuild  # the "macos-release" extra

    app = args.app.resolve()
    if not (app / "Contents" / "Info.plist").is_file():
        raise SystemExit(f"not an app bundle: {app}")
    args.out.unlink(missing_ok=True)
    dmgbuild.build_dmg(str(args.out), args.volname, settings=settings(app))
    verify(args.out, app.name, signed=not args.unsigned)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
