#!/usr/bin/env bash
# Build, sign, notarize and package a LocalSR macOS arm64 (MPS) beta, including the
# separate signed engine payload the in-app updater needs when the engine identity
# changes, and a ready update-feed entry. Usage: scripts/release_macos_beta.sh
# The version comes from pyproject.toml; the tree must be committed. LOCALSR_FEED_NOTES
# sets the text the update dialog shows for this entry.
set -euo pipefail
cd "$(dirname "$0")/.."
export DEVELOPER_DIR=/Library/Developer/CommandLineTools
export PATH="$HOME/.cargo/bin:$(brew --prefix rustup)/bin:$PWD/.venv/bin:$PATH"
VERSION=$(python3 -c 'import tomllib;print(tomllib.load(open("pyproject.toml","rb"))["project"]["version"])')
SHORT_VERSION=${VERSION%%-*}
IDENTITY="Developer ID Application: Hermes Reisner (Z2TU844D84)"
PROFILE=LocalSR-Z2TU844D84-notary
OUT="$PWD/build/release-$VERSION"
mkdir -p "$OUT"
APP_NAME="LocalSR.app"
HOST_BASE="https://macmini-ci.tail34a4e0.ts.net/releases/v$VERSION"
step() { printf '\n==> %s (%s)\n' "$1" "$(date +%H:%M:%S)"; }

test -z "$(git status --porcelain -- src desktop packaging scripts pyproject.toml)" || { echo "uncommitted source changes"; exit 1; }
# The source bundle is the last step but its inputs are built much earlier, so check
# them now rather than after half an hour of building, signing and notarizing.
for required in build/lgpl-media/dist/corresponding-source build/lgpl-media/opencv/corresponding-source \
  build/extra-sources/rawpy-0.27.0.tar.gz build/extra-sources/gcc-11.3.0-2.tar.gz; do
  test -e "$required" || {
    echo "missing corresponding-source input: $required"
    echo "Build the media runtime here (packaging/ffmpeg/install_media_runtime.py) or copy"
    echo "build/lgpl-media and build/extra-sources from a worktree that has them."
    exit 1
  }
done
COMMIT=$(git rev-parse HEAD)
export LOCALSR_UPDATE_PUBLIC_KEY="$(tr -d '\n' < packaging/updates/production.pub)"
export LOCALSR_UPDATE_BACKEND=mps
export LOCALSR_UPDATE_KIND=native
export LOCALSR_ENGINE_ID="macos-arm64-mps-${COMMIT:0:12}"
echo "commit $COMMIT engine $LOCALSR_ENGINE_ID"

step "verify licensing-clean media runtime"
python scripts/verify_codec_allowlist.py --python-env "$(command -v python)" > "$OUT/runtime-policy.json"

step "build worker and app"
python scripts/build_tauri_preview.py --skip-checks --bundles app > "$OUT/build.log" 2>&1
APP="$OUT/$APP_NAME"
rm -rf "$APP" && ditto "desktop/src-tauri/target/release/bundle/macos/$APP_NAME" "$APP"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $SHORT_VERSION" -c "Set :CFBundleVersion $SHORT_VERSION" "$APP/Contents/Info.plist"
python scripts/verify_codec_allowlist.py --tree "$APP" > "$OUT/app-policy.json"

step "Developer ID signing"
python scripts/sign_macos_keychain.py "$APP" --identity "$IDENTITY" --report "$OUT/signing-report.json"
python packaging/smoke_worker.py "$APP/Contents/Resources/engine/localsr-worker"
"$APP/Contents/MacOS/localsr-next" --headless-smoke-test > "$OUT/host-smoke.json"

notarize() {
  local file="$1" report="$2"
  xcrun notarytool submit "$file" --keychain-profile "$PROFILE" --wait --output-format json > "$report"
  python3 -c "import json,sys; r=json.load(open(sys.argv[1])); print(r.get('status'), r.get('id')); sys.exit(0 if r.get('status')=='Accepted' else 1)" "$report"
}

step "notarize app"
rm -f "$OUT/notarize-app.zip" && ditto -c -k --keepParent "$APP" "$OUT/notarize-app.zip"
notarize "$OUT/notarize-app.zip" "$OUT/notary-app.json"
xcrun stapler staple "$APP" && xcrun stapler validate "$APP"
spctl --assess --type execute --verbose=2 "$APP"

step "disk image"
DMG="$OUT/LocalSR-v$VERSION-macOS-arm64.dmg"
STAGE="$OUT/dmg-stage"
rm -rf "$STAGE" "$DMG" && mkdir -p "$STAGE"
ditto "$APP" "$STAGE/$APP_NAME" && ln -s /Applications "$STAGE/Applications"
hdiutil create -volname "LocalSR $VERSION" -srcfolder "$STAGE" -ov -format UDZO "$DMG" > /dev/null
codesign --force --sign "$IDENTITY" --timestamp "$DMG"
notarize "$DMG" "$OUT/notary-dmg.json"
xcrun stapler staple "$DMG" && xcrun stapler validate "$DMG"
spctl --assess --type open --context context:primary-signature --verbose=2 "$DMG"
rm -rf "$STAGE" "$OUT/notarize-app.zip"

step "signed update archive"
UPDATE="$OUT/LocalSR-v$VERSION-macOS-arm64.app.tar.gz"
rm -f "$UPDATE" "$UPDATE.sig"
# macOS tar stores extended attributes as hidden AppleDouble "._" entries by default.
# The updater strips the bundle name from every entry, so the top-level "._<app>" entry
# became an empty path and unpacking failed (0.1.0/0.1.1). Write plain entries only.
COPYFILE_DISABLE=1 tar --no-mac-metadata --no-xattrs --no-acls --no-fflags -C "$OUT" -czf "$UPDATE" "$APP_NAME"
python3 - "$UPDATE" "$APP_NAME" <<'PY'
import sys, tarfile
archive, app = sys.argv[1:]
with tarfile.open(archive) as t:
    names = t.getnames()
if names[0] != app or not all(n == app or n.startswith(app + "/") for n in names):
    raise SystemExit("update archive must contain only the app bundle at its top level")
if any(n.startswith("._") or "/._" in n for n in names):
    raise SystemExit("update archive contains AppleDouble metadata entries")
print(f"update archive: {len(names)} entries, no metadata entries")
PY
python scripts/beta_update_signing.py sign "$UPDATE"

step "signed engine payload"
ENGINE_OUT="$OUT/engine"
rm -rf "$ENGINE_OUT" && mkdir -p "$ENGINE_OUT"
ENGINE_PREFIX="LocalSR-v$VERSION-macOS-arm64.engine.tar.gz"
ENGINE_MANIFEST="LocalSR-v$VERSION-macOS-arm64.engine-payload.json"
python - "$APP/Contents/Resources/engine" "$ENGINE_OUT" "$ENGINE_PREFIX" "$ENGINE_MANIFEST" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, "scripts")
from prepare_engine_payload import prepare
engine, out, prefix, manifest_name = sys.argv[1:]
written = prepare(Path(engine), Path(out), prefix, backend="MPS", worker="localsr-worker")
written.rename(Path(out) / manifest_name)
print("engine payload written")
PY
for file in "$ENGINE_OUT/$ENGINE_MANIFEST" "$ENGINE_OUT"/*.part-*; do
  python scripts/beta_update_signing.py sign "$file"
done

step "corresponding source and notices"
rm -rf "$OUT/source"
python scripts/build_source_bundle.py --inventory "$APP" --output "$OUT/source" --commit "$COMMIT" \
  --media-source build/lgpl-media/dist/corresponding-source \
  --opencv-source build/lgpl-media/opencv/corresponding-source \
  --extra-source libraw=build/extra-sources/rawpy-0.27.0.tar.gz \
  --extra-source gcc-runtime=build/extra-sources/gcc-11.3.0-2.tar.gz > "$OUT/source-bundle.json"
tar -C "$OUT" -czf "$OUT/LocalSR-v$VERSION-source-and-notices.tar.gz" source

step "checksums and feed entry"
(cd "$OUT" && shasum -a 256 "LocalSR-v$VERSION-macOS-arm64.dmg" "LocalSR-v$VERSION-macOS-arm64.app.tar.gz" "LocalSR-v$VERSION-source-and-notices.tar.gz" > SHA256SUMS)
(cd "$ENGINE_OUT" && shasum -a 256 "$ENGINE_MANIFEST" *.part-* >> "$OUT/SHA256SUMS")
python3 - "$OUT" "$VERSION" "$COMMIT" "$LOCALSR_ENGINE_ID" "$HOST_BASE" "$ENGINE_MANIFEST" <<'PY'
import datetime, hashlib, json, os, sys, tarfile
out, version, commit, engine_id, host, manifest_name = sys.argv[1:]

def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def download_file(path, name):
    return {
        "name": name,
        "url": f"{host}/{name}",
        "size": os.path.getsize(path),
        "sha256": sha256(path),
        "signature": open(path + ".sig").read().strip(),
    }

update = f"{out}/LocalSR-v{version}-macOS-arm64.app.tar.gz"
with tarfile.open(update) as archive:
    app_unpacked = sum(member.size for member in archive.getmembers())
manifest_path = f"{out}/engine/{manifest_name}"
manifest = json.load(open(manifest_path))
parts = [download_file(f"{out}/engine/{part['filename']}", part["filename"]) for part in manifest["parts"]]
contract = {
    "channel": "beta",
    "backend": "mps",
    "kind": "native",
    "protocol": 1,
    "engine_id": engine_id,
    "size": os.path.getsize(update),
    "unpacked_size": app_unpacked + manifest["unpacked_bytes"],
    "sha256": sha256(update),
    "engine": {
        "id": engine_id,
        "manifest": download_file(manifest_path, manifest_name),
        "parts": parts,
    },
}
feed = {
    "version": version,
    "pub_date": datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    "notes": os.environ.get("LOCALSR_FEED_NOTES")
    or f"LocalSR {version}. What changed: https://herrei.github.io/localsr/release-notes/",
    "platforms": {
        "darwin-aarch64-mps-native": {
            "url": f"{host}/LocalSR-v{version}-macOS-arm64.app.tar.gz",
            "signature": open(update + ".sig").read().strip(),
            "localsr": contract,
        }
    },
}
json.dump(feed, open(f"{out}/beta.json", "w"), indent=2)
open(f"{out}/beta.json", "a").write("\n")
json.dump({"version": version, "commit": commit, "engine_id": engine_id,
           "dmg_size": os.path.getsize(f"{out}/LocalSR-v{version}-macOS-arm64.dmg"),
           "update_size": os.path.getsize(update), "update_unpacked_size": app_unpacked,
           "engine_parts": [p["name"] for p in parts], "engine_unpacked_size": manifest["unpacked_bytes"]},
          open(f"{out}/release.json", "w"), indent=2)
print("feed entry written")
PY
step "done"
cat "$OUT/SHA256SUMS"
