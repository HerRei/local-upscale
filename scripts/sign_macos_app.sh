#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "usage: sign_macos_app.sh /path/to/LocalSR.app /path/to/signing-report.json" >&2
  exit 2
fi

APP_PATH="$1"
REPORT_PATH="$2"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENTITLEMENTS="$SCRIPT_DIR/../packaging/macos/entitlements.plist"

test -d "$APP_PATH"
mkdir -p "$(dirname "$REPORT_PATH")"

required=(
  MACOS_CERTIFICATE_P12_BASE64
  MACOS_CERTIFICATE_PASSWORD
  MACOS_SIGNING_IDENTITY
  MACOS_NOTARY_APPLE_ID
  MACOS_NOTARY_PASSWORD
  MACOS_TEAM_ID
)
present=0
for name in "${required[@]}"; do
  if [ -n "${!name:-}" ]; then
    present=$((present + 1))
  fi
done

if [ "$present" -eq 0 ]; then
  codesign --verify --deep --strict "$APP_PATH"
  python3 - "$REPORT_PATH" <<'PY'
import json
import sys
from pathlib import Path

Path(sys.argv[1]).write_text(
    json.dumps(
        {
            "status": "ad-hoc",
            "developer_id": False,
            "notarized": False,
            "gatekeeper_accepted": False,
            "reason": "Production signing credentials were not configured for this alpha build.",
        },
        indent=2,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)
PY
  exit 0
fi

if [ "$present" -ne "${#required[@]}" ]; then
  echo "macOS signing configuration is incomplete; provide all signing and notarization values" >&2
  exit 1
fi

SIGNING_TEMP="$(mktemp -d)"
KEYCHAIN_PATH="$SIGNING_TEMP/localsr-signing.keychain-db"
KEYCHAIN_PASSWORD="$(uuidgen)$(uuidgen)"
cleanup() {
  security delete-keychain "$KEYCHAIN_PATH" >/dev/null 2>&1 || true
  rm -rf "$SIGNING_TEMP"
}
trap cleanup EXIT

python3 - "$SIGNING_TEMP/certificate.p12" <<'PY'
import base64
import os
import sys
from pathlib import Path

Path(sys.argv[1]).write_bytes(base64.b64decode(os.environ["MACOS_CERTIFICATE_P12_BASE64"]))
PY
security create-keychain -p "$KEYCHAIN_PASSWORD" "$KEYCHAIN_PATH"
security set-keychain-settings -lut 21600 "$KEYCHAIN_PATH"
security unlock-keychain -p "$KEYCHAIN_PASSWORD" "$KEYCHAIN_PATH"
security import "$SIGNING_TEMP/certificate.p12" \
  -k "$KEYCHAIN_PATH" \
  -P "$MACOS_CERTIFICATE_PASSWORD" \
  -T /usr/bin/codesign
security set-key-partition-list \
  -S apple-tool:,apple: \
  -s \
  -k "$KEYCHAIN_PASSWORD" \
  "$KEYCHAIN_PATH"

codesign --force --deep --strict \
  --options runtime \
  --timestamp \
  --entitlements "$ENTITLEMENTS" \
  --keychain "$KEYCHAIN_PATH" \
  --sign "$MACOS_SIGNING_IDENTITY" \
  "$APP_PATH"
codesign --verify --deep --strict --verbose=2 "$APP_PATH"

ditto -c -k --keepParent "$APP_PATH" "$SIGNING_TEMP/LocalSR-notarization.zip"
xcrun notarytool submit "$SIGNING_TEMP/LocalSR-notarization.zip" \
  --apple-id "$MACOS_NOTARY_APPLE_ID" \
  --password "$MACOS_NOTARY_PASSWORD" \
  --team-id "$MACOS_TEAM_ID" \
  --wait
xcrun stapler staple "$APP_PATH"
xcrun stapler validate "$APP_PATH"
spctl --assess --type execute --verbose=2 "$APP_PATH"

python3 - "$REPORT_PATH" "$MACOS_TEAM_ID" "$MACOS_SIGNING_IDENTITY" <<'PY'
import json
import sys
from pathlib import Path

Path(sys.argv[1]).write_text(
    json.dumps(
        {
            "status": "developer-id-notarized",
            "developer_id": True,
            "notarized": True,
            "gatekeeper_accepted": True,
            "team_id": sys.argv[2],
            "identity": sys.argv[3],
        },
        indent=2,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)
PY
