**LocalSR Apple signing setup**

Updated locally on 13 September 2026. The user created a Developer ID Application
certificate and authorized installation after downloading it. Public verification
results are in [apple-signing-acceptance-2026-09.json](apple-signing-acceptance-2026-09.json).

| Item | Verified value/status |
| --- | --- |
| Signing identity | `Developer ID Application: Hermes Reisner (Z2TU844D84)` |
| Team ID | `Z2TU844D84` |
| Certificate subject country | `CH` |
| Certificate expiry | 13 September 2031, 14:09:37 UTC |
| Certificate/private key | Matching identity in this Mac's login Keychain |
| Certificate chain | Apple Developer ID Certification Authority G2; valid without a custom trust override |
| Native signing check | ARM64 executable signed with hardened runtime and an Apple secure timestamp; signature verification and execution passed |
| Notarization profile | `LocalSR-Z2TU844D84-notary` in this Mac's login Keychain; authenticated request to Apple passed |
| LocalSR beta candidate | ARM64 beta app and DMG signed, notarized, stapled and accepted by Gatekeeper; mounted bundled-worker check passed |

The downloaded certificate's public key matches the user's CSR, whose signature
also verified. Apple's G2 intermediate was initially absent from the login
Keychain; the official certificate was downloaded, verified against system trust
and installed. The first timestamp request failed with a service-availability
error; a bounded retry against Apple's timestamp endpoint passed. The successful
probe retained the secure timestamp requirement.

The private key was not exported. Temporary probes and the intermediate download
were removed. The user's original certificate/CSR, running app, `.12` checkout,
release configuration and runners were preserved.

**Notarization authentication verified**

The user completed the secure setup helper. An authenticated `notarytool history`
request using `LocalSR-Z2TU844D84-notary` succeeded on 12 September 2026. The
verification used the stored Keychain profile without exporting or logging its
credentials. No application was submitted to Apple during this check.

The completed Desktop helper was removed after checking that its contents still
matched the file created for this setup. The original certificate/CSR and the
Keychain identity/profile remain in place.

For future credential renewal, create an app-specific password labelled
`LocalSR notarization` through **Sign-In and Security → App-Specific Passwords**
in [Apple Account](https://account.apple.com/), following
[Apple's instructions](https://support.apple.com/en-us/102654). Then update the
same Keychain profile with this command, replacing the email placeholder:

```sh
xcrun notarytool store-credentials LocalSR-Z2TU844D84-notary \
  --apple-id YOUR_APPLE_ACCOUNT_EMAIL \
  --team-id Z2TU844D84 \
  --keychain "$HOME/Library/Keychains/login.keychain-db"
```

Omitting `--password` makes Apple's tool prompt securely. Keep validation enabled.

**Isolated candidate completed — 13 September 2026**

Developer ID signing covered 317 native files and the outer application with
hardened runtime and secure timestamps. The app notarization submission
`04821633-8a94-4fbc-add5-4d6b6944b1f2` and DMG submission
`0139f0ff-5005-4081-b93a-0e84e14ae186` are both **Accepted**. Their tickets were
stapled and validated. Strict codesign and Gatekeeper checks passed for the DMG
and its mounted app. The mounted host started the bundled worker and completed
its handshake. Six signed-worker MPS image/video/recovery cases also passed.

The retained review DMG is
`build/beta-review/macos/LocalSR_0.0.13-beta.1_macos-arm64-mps.dmg`, SHA-256
`c24f964a722ba5a35d7045bacf45881e28aea2d50eb94ca904418a01ab8f628a`.
Reports and Apple's submission logs are retained under `build/beta-review/`.
This Mac's GUI was not used. No credential or private key was exported or logged.

The current release workflow still expects its documented P12/notarization
secrets. This local Keychain profile does not configure those release jobs.
The isolated beta path now uses the local identity/profile. Native installed
upgrades and GUI acceptance on a separate authorized Mac remain open. The current
binary is a review candidate: final source/notices/dependency changes require a
new build and fresh signing/notarization before publication. These remain in the
[beta checklist](beta-release-checklist.md).

Sources: [Apple Developer ID certificates](https://developer.apple.com/help/account/certificates/create-developer-id-certificates/),
[Apple's official certificate authorities](https://www.apple.com/certificateauthority/),
and [Apple's notarization workflow](https://developer.apple.com/documentation/security/customizing-the-notarization-workflow).
