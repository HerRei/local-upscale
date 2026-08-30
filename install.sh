#!/usr/bin/env bash
# LocalSR verified installer for macOS and Linux.
set -euo pipefail

REPO="HerRei/local-upscale"
DEFAULT_RELEASE_TAG="@LOCALSR_RELEASE_TAG@"
RELEASE_TAG="${LOCALSR_RELEASE_TAG:-$DEFAULT_RELEASE_TAG}"
REQUESTED_FLAVOR="${LOCALSR_FLAVOR:-}"
INSTALL_DIR_LINUX="${LOCALSR_INSTALL_DIR:-$HOME/.local/share/localsr}"
BIN_DIR_LINUX="$HOME/.local/bin"
DESKTOP_DIR_LINUX="$HOME/.local/share/applications"
TMP_DIR=""
ROLLBACK_TARGET=""
ROLLBACK_BACKUP=""

usage() {
    cat <<'EOF'
Usage: bash Install-LocalSR.sh [--tag TAG] [--flavor FLAVOR] [--install-dir PATH]

The installer detects the safest matching backend, downloads only that logical
bundle, verifies every downloaded part using SHA256SUMS, verifies the assembled
archive using release-index.json, and then replaces the existing installation.

Supported flavor overrides:
  Linux-CPU-x86_64       Linux-CUDA-x86_64
  Linux-Intel-x86_64     Linux-ROCm-x86_64
  macOS-x86_64           macOS-arm64
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --tag)
            [ "$#" -ge 2 ] || { echo "Missing value for --tag" >&2; exit 2; }
            RELEASE_TAG="$2"
            shift 2
            ;;
        --flavor)
            [ "$#" -ge 2 ] || { echo "Missing value for --flavor" >&2; exit 2; }
            REQUESTED_FLAVOR="$2"
            shift 2
            ;;
        --install-dir)
            [ "$#" -ge 2 ] || { echo "Missing value for --install-dir" >&2; exit 2; }
            INSTALL_DIR_LINUX="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

cleanup() {
    if [ -n "$ROLLBACK_BACKUP" ] && [ -e "$ROLLBACK_BACKUP" ] && [ ! -e "$ROLLBACK_TARGET" ]; then
        mv "$ROLLBACK_BACKUP" "$ROLLBACK_TARGET"
    fi
    if [ -n "$TMP_DIR" ] && [ -d "$TMP_DIR" ]; then
        rm -rf "$TMP_DIR"
    fi
}
trap cleanup EXIT HUP INT TERM

safe_name() {
    case "$1" in
        ""|.*|*[!A-Za-z0-9._+-]*|*/*) return 1 ;;
        *) return 0 ;;
    esac
}

if [ "$RELEASE_TAG" = "$DEFAULT_RELEASE_TAG" ] && [ "${DEFAULT_RELEASE_TAG#@}" != "$DEFAULT_RELEASE_TAG" ]; then
    if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
        RELEASE_TAG=$(gh release list --repo "$REPO" --limit 20 \
            --json tagName,isDraft,publishedAt \
            --jq '[.[] | select(.isDraft == false)] | sort_by(.publishedAt) | reverse | .[0].tagName')
    else
        echo "This source-tree installer needs --tag TAG, or an authenticated GitHub CLI." >&2
        exit 1
    fi
fi
safe_name "$RELEASE_TAG" || { echo "Unsafe release tag: $RELEASE_TAG" >&2; exit 1; }

OS=$(uname -s)
ARCH=$(uname -m)
if [ -n "$REQUESTED_FLAVOR" ]; then
    FLAVOR="$REQUESTED_FLAVOR"
elif [ "$OS" = "Darwin" ]; then
    case "$ARCH" in
        arm64) FLAVOR="macOS-arm64" ;;
        x86_64) FLAVOR="macOS-x86_64" ;;
        *) echo "Unsupported macOS architecture: $ARCH" >&2; exit 1 ;;
    esac
elif [ "$OS" = "Linux" ]; then
    case "$ARCH" in
        x86_64|amd64) ;;
        *) echo "This alpha publishes Linux x86_64 bundles only; detected $ARCH." >&2; exit 1 ;;
    esac
    if command -v nvidia-smi >/dev/null 2>&1 && [ -e /dev/nvidia0 ]; then
        FLAVOR="Linux-CUDA-x86_64"
    elif [ -e /dev/kfd ] && command -v lspci >/dev/null 2>&1 \
        && lspci 2>/dev/null | grep -qiE 'AMD.*(Radeon|VGA|Display)'; then
        FLAVOR="Linux-ROCm-x86_64"
    elif [ -d /dev/dri ] && command -v lspci >/dev/null 2>&1 \
        && lspci 2>/dev/null | grep -qiE 'Intel.*(Arc|Iris|Graphics|Xe)'; then
        FLAVOR="Linux-Intel-x86_64"
    else
        FLAVOR="Linux-CPU-x86_64"
    fi
else
    echo "Unsupported operating system: $OS" >&2
    exit 1
fi

case "$FLAVOR" in
    Linux-CPU-x86_64|Linux-CUDA-x86_64|Linux-Intel-x86_64|Linux-ROCm-x86_64|macOS-x86_64|macOS-arm64) ;;
    *) echo "Unsupported LocalSR flavor: $FLAVOR" >&2; exit 1 ;;
esac

case "$FLAVOR" in
    Linux-CPU-x86_64) BUNDLE_NAME="LocalSR-Linux-CPU-x86_64.tar.gz" ;;
    Linux-CUDA-x86_64) BUNDLE_NAME="LocalSR-Linux-CUDA-x86_64.tar.gz" ;;
    Linux-Intel-x86_64) BUNDLE_NAME="LocalSR-Linux-Intel-x86_64.tar.gz" ;;
    Linux-ROCm-x86_64) BUNDLE_NAME="LocalSR-Linux-ROCm-x86_64.tar.gz" ;;
    macOS-x86_64) BUNDLE_NAME="LocalSR-macOS-x86_64.tar.gz" ;;
    macOS-arm64) BUNDLE_NAME="LocalSR-macOS-arm64.tar.gz" ;;
esac

TMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/localsr-install.XXXXXX")

download_asset() {
    asset_name="$1"
    safe_name "$asset_name" || { echo "Unsafe release asset name: $asset_name" >&2; exit 1; }
    destination="$TMP_DIR/$asset_name"
    if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
        gh release download "$RELEASE_TAG" --repo "$REPO" \
            --pattern "$asset_name" --dir "$TMP_DIR" --clobber
        [ -f "$destination" ] || { echo "GitHub did not provide $asset_name" >&2; exit 1; }
        return
    fi
    command -v curl >/dev/null 2>&1 || {
        echo "Install curl or authenticate GitHub CLI with: gh auth login" >&2
        exit 1
    }
    curl --fail --location --proto '=https' --tlsv1.2 \
        --output "$destination" \
        "https://github.com/$REPO/releases/download/$RELEASE_TAG/$asset_name"
}

hash_file() {
    if command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | awk '{print $1}'
    elif command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    else
        echo "No SHA-256 tool is available (need shasum or sha256sum)." >&2
        exit 1
    fi
}

download_asset "release-index.json"
download_asset "SHA256SUMS"

BUNDLE_DATA="$TMP_DIR/bundle-data.txt"
if command -v python3 >/dev/null 2>&1; then
    python3 - "$TMP_DIR/release-index.json" "$BUNDLE_NAME" "$RELEASE_TAG" > "$BUNDLE_DATA" <<'PY'
import json
import sys

index = json.load(open(sys.argv[1], encoding="utf-8"))
if index.get("schema_version") != 2:
    raise SystemExit("Unsupported release-index schema")
if index.get("release_tag") != sys.argv[3]:
    raise SystemExit("Release index tag does not match the requested release")
if index.get("checksum_file") != "SHA256SUMS":
    raise SystemExit("Release index names an unexpected checksum file")
matches = [bundle for bundle in index["bundles"] if bundle["filename"] == sys.argv[2]]
if len(matches) != 1:
    raise SystemExit(f"Expected one bundle named {sys.argv[2]}; found {len(matches)}")
bundle = matches[0]
print(bundle["filename"])
print(bundle["sha256"])
for asset in bundle["assets"]:
    print(asset)
PY
elif command -v jq >/dev/null 2>&1; then
    jq -er --arg name "$BUNDLE_NAME" --arg tag "$RELEASE_TAG" \
        'if .schema_version != 2 or .release_tag != $tag or .checksum_file != "SHA256SUMS" then error("release index header mismatch") else . end | .bundles | map(select(.filename == $name)) | if length == 1 then .[0] else error("bundle not found") end | .filename, .sha256, .assets[]' \
        "$TMP_DIR/release-index.json" > "$BUNDLE_DATA"
else
    echo "The installer needs python3 or jq to read the release manifest." >&2
    exit 1
fi

INDEX_NAME=$(sed -n '1p' "$BUNDLE_DATA")
EXPECTED_BUNDLE_HASH=$(sed -n '2p' "$BUNDLE_DATA")
[ "$INDEX_NAME" = "$BUNDLE_NAME" ] || { echo "Release index selected the wrong bundle." >&2; exit 1; }
case "$EXPECTED_BUNDLE_HASH" in
    ""|*[!0-9a-fA-F]*) echo "Release index has an invalid bundle digest." >&2; exit 1 ;;
esac
[ "${#EXPECTED_BUNDLE_HASH}" -eq 64 ] || {
    echo "Release index has an invalid bundle digest." >&2
    exit 1
}

ASSETS=()
while IFS= read -r asset_name; do
    [ -n "$asset_name" ] || continue
    safe_name "$asset_name" || { echo "Unsafe bundle asset name: $asset_name" >&2; exit 1; }
    ASSETS+=("$asset_name")
done < <(tail -n +3 "$BUNDLE_DATA")
[ "${#ASSETS[@]}" -gt 0 ] || { echo "Release index contains no bundle assets." >&2; exit 1; }

for asset_name in "${ASSETS[@]}"; do
    download_asset "$asset_name"
    checksum_matches=$(awk -v name="$asset_name" '$2 == name {print $1}' "$TMP_DIR/SHA256SUMS")
    [ "$(printf '%s\n' "$checksum_matches" | awk 'NF {count++} END {print count+0}')" -eq 1 ] || {
        echo "SHA256SUMS does not uniquely identify $asset_name" >&2
        exit 1
    }
    case "$checksum_matches" in
        ""|*[!0-9a-fA-F]*) echo "SHA256SUMS has an invalid digest for $asset_name" >&2; exit 1 ;;
    esac
    [ "${#checksum_matches}" -eq 64 ] || {
        echo "SHA256SUMS has an invalid digest for $asset_name" >&2
        exit 1
    }
    actual_hash=$(hash_file "$TMP_DIR/$asset_name")
    [ "$(printf '%s' "$actual_hash" | tr 'A-F' 'a-f')" = "$(printf '%s' "$checksum_matches" | tr 'A-F' 'a-f')" ] || {
        echo "Checksum verification failed for $asset_name" >&2
        exit 1
    }
done

ARCHIVE_FILE="$TMP_DIR/$BUNDLE_NAME"
if [ "${#ASSETS[@]}" -eq 1 ] && [ "${ASSETS[0]}" = "$BUNDLE_NAME" ]; then
    :
else
    : > "$ARCHIVE_FILE"
    for asset_name in "${ASSETS[@]}"; do
        cat "$TMP_DIR/$asset_name" >> "$ARCHIVE_FILE"
    done
fi
ACTUAL_BUNDLE_HASH=$(hash_file "$ARCHIVE_FILE")
[ "$(printf '%s' "$ACTUAL_BUNDLE_HASH" | tr 'A-F' 'a-f')" = "$(printf '%s' "$EXPECTED_BUNDLE_HASH" | tr 'A-F' 'a-f')" ] || {
    echo "Assembled bundle checksum verification failed." >&2
    exit 1
}

echo "Verified $BUNDLE_NAME for $FLAVOR."
UNPACKED="$TMP_DIR/unpacked"
mkdir -p "$UNPACKED"
tar -xzf "$ARCHIVE_FILE" -C "$UNPACKED"

if [ "$OS" = "Darwin" ]; then
    SOURCE_APP="$UNPACKED/LocalSR.app"
    [ -x "$SOURCE_APP/Contents/MacOS/LocalSR" ] || {
        echo "Verified archive does not contain LocalSR.app." >&2
        exit 1
    }
    TARGET_PARENT="/Applications"
    if [ ! -w "$TARGET_PARENT" ]; then
        TARGET_PARENT="$HOME/Applications"
        mkdir -p "$TARGET_PARENT"
    fi
    TARGET_APP="$TARGET_PARENT/LocalSR.app"
    NEW_APP="$TARGET_PARENT/.LocalSR.app.new.$$"
    BACKUP_APP="$TARGET_PARENT/.LocalSR.app.previous.$$"
    if command -v ditto >/dev/null 2>&1; then
        ditto "$SOURCE_APP" "$NEW_APP"
    else
        cp -R "$SOURCE_APP" "$NEW_APP"
    fi
    if [ -e "$TARGET_APP" ]; then
        mv "$TARGET_APP" "$BACKUP_APP"
        ROLLBACK_TARGET="$TARGET_APP"
        ROLLBACK_BACKUP="$BACKUP_APP"
    fi
    mv "$NEW_APP" "$TARGET_APP"
    if [ -n "$ROLLBACK_BACKUP" ]; then
        rm -rf "$ROLLBACK_BACKUP"
    fi
    ROLLBACK_TARGET=""
    ROLLBACK_BACKUP=""
    echo "Installed LocalSR to $TARGET_APP"
else
    SOURCE_EXE="$UNPACKED/LocalSR/LocalSR"
    [ -x "$SOURCE_EXE" ] || { echo "Verified archive does not contain executable LocalSR." >&2; exit 1; }
    SOURCE_ROOT=$(dirname "$SOURCE_EXE")
    TARGET_PARENT=$(dirname "$INSTALL_DIR_LINUX")
    mkdir -p "$TARGET_PARENT" "$BIN_DIR_LINUX" "$DESKTOP_DIR_LINUX"
    NEW_DIR="$TARGET_PARENT/.localsr.new.$$"
    BACKUP_DIR="$TARGET_PARENT/.localsr.previous.$$"
    cp -R "$SOURCE_ROOT" "$NEW_DIR"
    if [ -e "$INSTALL_DIR_LINUX" ]; then
        mv "$INSTALL_DIR_LINUX" "$BACKUP_DIR"
        ROLLBACK_TARGET="$INSTALL_DIR_LINUX"
        ROLLBACK_BACKUP="$BACKUP_DIR"
    fi
    mv "$NEW_DIR" "$INSTALL_DIR_LINUX"
    if [ -n "$ROLLBACK_BACKUP" ]; then
        rm -rf "$ROLLBACK_BACKUP"
    fi
    ROLLBACK_TARGET=""
    ROLLBACK_BACKUP=""
    ln -sfn "$INSTALL_DIR_LINUX/LocalSR" "$BIN_DIR_LINUX/localsr"
    cat > "$DESKTOP_DIR_LINUX/localsr.desktop" <<EOF
[Desktop Entry]
Name=LocalSR
Comment=Private local image and video upscaling
Exec=$INSTALL_DIR_LINUX/LocalSR %F
Terminal=false
Type=Application
Categories=Graphics;Photography;Utility;
StartupNotify=true
EOF
    chmod 0755 "$DESKTOP_DIR_LINUX/localsr.desktop"
    echo "Installed LocalSR to $INSTALL_DIR_LINUX"
    echo "Command: $BIN_DIR_LINUX/localsr"
fi

echo "Installation complete. Models remain on-demand downloads."
