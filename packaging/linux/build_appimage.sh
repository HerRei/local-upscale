#!/bin/sh
set -eu

PROJECT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)"
APP_SOURCE="${1:-$PROJECT_DIR/dist/LocalSR}"
APPIMAGETOOL="${2:-appimagetool}"
APPDIR="$PROJECT_DIR/build/appimage/LocalSR.AppDir"
OUTPUT_DIR="$PROJECT_DIR/release"

if [ ! -x "$APP_SOURCE/LocalSR" ]; then
    echo "Packaged LocalSR executable not found at $APP_SOURCE/LocalSR" >&2
    exit 1
fi

rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/lib/localsr" "$APPDIR/usr/share/applications" "$OUTPUT_DIR"
cp -a "$APP_SOURCE/." "$APPDIR/usr/lib/localsr/"
cp "$PROJECT_DIR/packaging/linux/AppRun" "$APPDIR/AppRun"
cp "$PROJECT_DIR/packaging/linux/localsr.desktop" "$APPDIR/localsr.desktop"
cp "$PROJECT_DIR/packaging/linux/localsr.desktop" "$APPDIR/usr/share/applications/localsr.desktop"
cp "$PROJECT_DIR/packaging/icons/LocalSR.png" "$APPDIR/localsr.png"
chmod +x "$APPDIR/AppRun"

ARCH=x86_64 "$APPIMAGETOOL" "$APPDIR" "$OUTPUT_DIR/LocalSR-Linux-x86_64.AppImage"
