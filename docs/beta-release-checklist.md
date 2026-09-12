**LocalSR beta release checklist**

Checked on 2026-09-11 against app commit `1321045` on `codex/hdr-preservation`, the release files, and live GitHub repository metadata. This checklist is in the isolated integration checkout. Work on `fix/v0.0.12-release-pipeline` remains separate.

The current [readiness register](../ci/beta-readiness.json) has **10 unresolved blocking gates** and two optional Labs gates. Running `python3 scripts/check_beta_readiness.py --require-beta-ready` currently fails as expected. Repository signing secrets and a native ARM64 Mac runner were absent when checked; that does not establish whether you already own developer accounts or certificates.

**Your accounts and decisions — start here**

- [ ] **Choose the publisher identity:** your individual legal identity or a registered organization. Use the appropriate identity consistently when enrolling with signing providers.
- [ ] **Confirm or enroll in the Apple Developer Program.** Enable Apple Account two-factor authentication and complete identity verification. Membership is USD 99/year, with regional pricing. Organization enrollment also requires organization verification, normally including a D-U-N-S number. [Apple enrollment requirements](https://developer.apple.com/help/account/membership/program-enrollment).
- [ ] **Create a Developer ID Application certificate and notarization credentials.** This is the certificate for the directly distributed Mac app. Arrange secure access for the release workflow; store credentials in GitHub Secrets, never in repository files or chat. [Apple Developer ID certificates](https://developer.apple.com/help/account/certificates/create-developer-id-certificates).
- [ ] **Choose a Windows signing provider and complete identity verification.** Confirm support for your country, individual/company status, and unattended CI signing before purchasing. Microsoft Artifact Signing currently accepts Swiss organizations for Public Trust; individual developers must be in the US or Canada. A Swiss individual therefore needs another eligible provider. [Microsoft eligibility](https://learn.microsoft.com/en-us/azure/artifact-signing/quickstart).
- [ ] **Arrange native Apple-Silicon build capacity and testers.** A hosted ARM64 Mac runner or dedicated Apple-Silicon Mac can cover builds. Arrange separate machines for downloaded-installer acceptance on every advertised OS/backend.
- [ ] **Decide the beta platform scope.** Name the supported OS versions, architectures, GPU backends and memory requirements. Intel Mac and the legacy DirectML runtime remain unresolved. If a backend is deferred, update the target registry, release manifests and website together.
- [ ] **Resolve the model-license decisions.** Obtain documented terms for the exact HAT face checkpoints, including training-data and redistribution/commercial-use rights, and clarify the Best/anime checkpoints' `CC-BY-0.4` license string. Otherwise replace or remove the affected offering from the public beta and explicitly revise its gate. The preview’s two-step direct download for the two RealPLKSR checkpoints does not resolve their ambiguous license or close the beta blocker. Keep checkpoint files unpublished until the distribution terms are settled.
- [ ] **Choose public downloads and feedback.** The source repository is currently private. Choose a public release repository or other download host, plus a public issue tracker or support contact. Test access while signed out. Decide repository visibility/plan so the intended release protections and security monitoring can be enabled.

**Implementation work before making the candidate**

- [ ] **Finish Mac signing and notarization.** Sign the app and bundled executable/native components with the correct entitlements, hardened runtime and timestamps; notarize and staple the distribution. Verify Developer ID, Team ID, signatures, stapling and Gatekeeper acceptance on the downloaded DMG/app. Existing checks are in [verify_macos_tauri_signing.py](../scripts/verify_macos_tauri_signing.py). [Apple notarization requirements](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution).
- [ ] **Adapt Windows CI to the chosen provider.** The existing workflow imports an exported PFX. Modern publicly trusted code-signing keys generally use protected hardware/cloud storage; DigiCert's current keys cannot simply be exported as a PFX. Integrate the provider's supported signing method and update credential instructions and verification, including certificate rotation where applicable. Sign the app and installer and verify trusted Authenticode signatures plus timestamps. [DigiCert key/export restrictions](https://knowledge.digicert.com/general-information/export-a-code-signing-certificate-as-a-pfx-file).
- [ ] **Finish native builds and runtime support.** Change both Mac preflight routing and the Mac build job to a native ARM64 runner. The current signed workflow routes to an X64 cross-build runner. Use a maintained, reviewed inference runtime and resolve or defer the other unsupported runtime paths.
- [ ] **Close the security gate with evidence.** Review Python, npm and Cargo dependencies, resolve the recorded GTK/glib advisory through a supported fix/migration or an explicitly revised platform scope, configure available scanning/release protection, and isolate signing credentials from untrusted code on persistent runners. Recheck current findings; historical alert counts are not a fresh audit.
- [ ] **Configure and verify signed updates for the first beta.** The local implementation and build/feed contract are documented in [local-updates.md](local-updates.md). Create and protect a production updater key, embed its public key and backend identity, publish separate Stable/Beta manifests, and test actual old-to-new upgrades on every supported package type. Include split engines, cancellation, invalid signatures, low disk space, settings recovery and failed startup. Public updates remain disabled until this is done.
- [ ] **Prepare a separate beta version and release branch.** Synchronize Python, npm, Cargo and Tauri versions, artifact names, manifests and release notes. Remove hard-coded `v0.0.12-alpha` artifact paths from the future beta workflow. Build every candidate artifact from one immutable commit. Keep the `.12` testing-only exception and its release process separate.
- [ ] **Reconcile documentation with the beta's actual behavior.** Update the website, download instructions, OS/memory requirements, model cards, license notices, known limitations and privacy information. Several acceptance/limitation documents predate HDR preservation; the example acceptance record still references `0.0.11-alpha` and HDR rejection. Update them to the chosen beta scope. Preview features should be labeled separately from released downloads.

The current Mac workflow expects these **secret names**; values belong only in the configured secret store:

| Secret | Purpose |
| --- | --- |
| `MACOS_CERTIFICATE_P12_BASE64` | Exported Developer ID certificate and private key, encoded as base64 |
| `MACOS_CERTIFICATE_PASSWORD` | Password protecting that export |
| `MACOS_SIGNING_IDENTITY` | Exact Developer ID Application signing identity |
| `MACOS_NOTARY_APPLE_ID` | Apple Account used for notarization |
| `MACOS_NOTARY_PASSWORD` | App-specific password for notarization |
| `MACOS_TEAM_ID` | Apple Developer team identifier |

An alternative notarization authentication method requires a corresponding workflow change. Document Windows secret names after selecting and integrating the provider; the existing PFX instructions should not drive the purchase decision.

**Acceptance tests on the exact signed release candidate**

- [ ] **Fresh installation on every advertised platform/backend.** Download through the intended public link on a separate machine/user account without a development Python environment. Verify checksums, publisher trust, architecture, launch, model download/import, real inference and export. Exercise reinstall/update and uninstall. Test native GPU operation on the matching hardware; CPU fallback alone does not verify a GPU package. Include split installer/engine payloads and reassembly where used.
- [ ] **Representative images and resource pressure.** Test photos, faces, anime/text, transparency, DNG and large images. Exercise low memory, low disk space, cancellation, worker failure/recovery and a sustained run. Confirm valid outputs, cleanup of temporary files and unchanged originals.
- [ ] **Real video and playback.** Complete a representative long/high-resolution MOV run, then decode and inspect the whole output for timing, orientation, audio and visual integrity. Cover CFR/VFR, trims and supported containers. Specifically repeat completed video A → start B → return to A; verify playback, correct source-frame ownership, aligned tile animation and sensible ETA. Check CPU/GPU benchmark selection and separate scores.
- [ ] **Verify HDR claims and model restrictions.** Test HLG and PQ export precision, transfer/primaries/range metadata and visual highlight/colour retention on suitable displays. Keep the preview's SDR display conversion explicit. HAT models were trained on SDR, so valid 10-bit output alone is not proof of HDR model quality. Keep preservation experimental unless quality acceptance supports promotion. Confirm SeedVR2 cannot select HDR preservation and that custom-model options follow declared capabilities.
- [ ] **Keep Labs status honest.** SeedVR2 needs multi-chunk continuity, memory and cancellation acceptance before promotion. Deflicker and video face processing need moving detail, pans and scene cuts. Add an explicit HDR acceptance record/gate matching the chosen scope; optional status must not bypass a blocking standard-video issue.
- [ ] **Record results for the shipped binaries.** Use an updated [acceptance record](acceptance-record.example.json), recording commit, artifact hash, OS/GPU, settings, results and known limits. Prior source verification was 557 Python tests passed/2 skipped, 68 frontend tests passed and 48 Rust tests passed. That is useful evidence, but fresh signed-installer and full long-video acceptance remain outstanding.

**Release only after the evidence is complete**

- [ ] Update each blocking entry in [beta-readiness.json](../ci/beta-readiness.json) with its actual resolution and evidence. If scope changes, explicitly reconcile the gates, target registry and public claims.
- [ ] Require the readiness command below to exit successfully, alongside the existing automated integrity checks and signed package verification. Preserve checksums, signing reports and acceptance records.
- [ ] Publish a new immutable **prerelease** with the verified installers, all companion payloads, checksums, known limitations and an accessible feedback link. Confirm the website's version/download links while signed out. Keep the previous release available for rollback.

```sh
python3 scripts/check_beta_readiness.py --require-beta-ready
```

This checklist records work to complete; it does not change the readiness register or declare beta readiness.
