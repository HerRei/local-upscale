# Native releases

LocalSR's `Native installers` workflow builds on each target operating system because PyInstaller
does not cross-compile. Model checkpoints are not included in any package.

## Artifacts

| Runner | Artifact |
|---|---|
| macOS 14 Apple Silicon | `LocalSR-macOS-arm64.dmg` |
| Windows Server 2022 x86-64 | `LocalSR-Windows-x86_64-Setup.exe` |
| Ubuntu 22.04 x86-64 | `LocalSR-Linux-x86_64.AppImage` and portable `.tar.gz` |

Every job builds the PyInstaller directory, runs `LocalSR --smoke-test` to instantiate and cleanly
tear down the packaged QML/controller without entering an interactive event loop, and
independently asks the packaged worker for capabilities over JSONL before requesting clean shutdown.
Keeping these smoke tests separate avoids conflating slow first-time Torch startup with GUI startup.
The headless Windows runner uses Qt's offscreen software backend and permits extra time for Windows
to inspect the large first-run bundle; installed builds continue to use the native graphics backend.
Linux downloads
the official AppImage `appimagetool` asset and verifies its publisher-provided SHA-256 digest before
use. A `v*` tag publishes all successful artifacts as a GitHub Release; manual workflow runs retain
them as Actions artifacts without creating a release.

## Local build

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[package]"
python packaging/build_icons.py
pyinstaller --clean --noconfirm packaging/localsr.spec
```

On macOS the output is `dist/LocalSR.app`; Windows and Linux use `dist/LocalSR/`. The bundle contains
both `LocalSR` and `LocalSRWorker`. The custom QtQml hook intentionally includes only QtQml, QtQuick,
Layouts, Templates, Window, and the Basic Controls style.

## Optional signing and notarization

Unsigned artifacts are still produced when no credentials are configured. To sign releases, add
these GitHub Actions secrets:

### macOS

- `MACOS_CERTIFICATE`: Developer ID Application certificate exported as `.p12`, base64 encoded.
- `MACOS_CERTIFICATE_PASSWORD`: password used when exporting the certificate.
- `MACOS_KEYCHAIN_PASSWORD`: temporary CI keychain password.
- `MACOS_SIGNING_IDENTITY`: complete Developer ID Application identity.
- `APPLE_ID`, `APPLE_APP_PASSWORD`, `APPLE_TEAM_ID`: notarytool credentials.

The workflow signs the `.app` with hardened runtime and the entitlements in
`packaging/macos/entitlements.plist`, builds the DMG, submits it to Apple, and staples the result.

### Windows

- `WINDOWS_CERTIFICATE`: Authenticode `.pfx`, base64 encoded.
- `WINDOWS_CERTIFICATE_PASSWORD`: certificate password.

The workflow builds with Inno Setup and timestamps the installer using SHA-256. Hardware-backed or
cloud signing requires replacing this step with the certificate provider's supported action.

## Publishing

1. Ensure CI is green on `main`.
2. Update `pyproject.toml` and this document if artifact support changes.
3. Create and push an annotated version tag, for example `git tag -a v0.3.0 -m "LocalSR 0.3.0"`.
4. Watch all three native jobs. A release is created only after every platform succeeds.
5. Test installation on physical Windows, macOS, and Linux hardware before describing a build as
   stable. CI proves packaging and startup; it cannot prove each GPU driver/backend combination.
