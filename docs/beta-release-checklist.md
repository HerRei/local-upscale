**LocalSR beta release checklist and walkthrough**

Updated 12 September 2026 from the local preview, the current release configuration, the [application verification record](local-update-acceptance.json), the [Apple signing check](apple-signing-acceptance-2026-09.json) and the user's account/submission updates. This is the working checklist for our decisions together. Approved choices and pending questions belong in the [decision log](beta-release-decisions.md).

The existing `.12` release, its runners and the running Mac application stay untouched. This work is local. The user created the Apple signing key/certificate and authorized its local installation; no private key was exported, account purchase made, repository visibility changed or release published by the agent.

**Where we are**

- [x] Last recorded application regression suite: 568 Python, 83 frontend and 58 Rust tests passed; 3 Python tests skipped.
- [x] CPU/ROCm worker inference, cancellation and subsequent processing checked on DDP.
- [x] Linux portable signed update, restart and failed-startup rollback tested with a disposable key; recipes, settings, queue data and models survived.
- [x] Both RealPLKSR downloads passed the two-acknowledgement UI flow, checksum verification and AMD inference. This establishes functionality; their license ambiguity remains unresolved.
- [x] Test keys, profiles, services and build caches cleaned up; current DDP preview left open.
- [x] Store identity, package artwork and local MSIX layout helper prepared; 18 packaging tests passed. A complete Windows MSIX has not yet been built or tested.
- [x] Partner Center pricing/availability, properties and age ratings marked complete in the user's screenshot. Packages remain incomplete, Store listings are not started and the submission remains a draft.
- [x] Apple Developer account reported ready; the downloaded Developer ID Application certificate is installed, matches the CSR/private key and is trusted with Apple's G2 intermediate. A disposable native executable passed signing with an Apple timestamp, hardened runtime, signature verification and execution. [Evidence](apple-signing-acceptance-2026-09.json).
- [x] Apple notarization authentication verified using the saved `LocalSR-Z2TU844D84-notary` login Keychain profile. An authenticated request to Apple's notarization history succeeded; the completed setup helper was removed. No application has been submitted by this setup check.
- [x] Prepared local [Store listing text, reviewer notes and screenshot plan](beta-store-submission.md), a [privacy/support draft](beta-privacy-and-support.md), and a [public feedback form](../packaging/beta-feedback/README.md). These drafts do not establish published pages, captured Windows screenshots or candidate acceptance.
- [x] User confirmed public GitHub Issues for beta bug reports and `hermes.reisner@gmail.com` for contact/private requests. These details are now in the local Store/privacy/support drafts and prepared tracker configuration. Public tracker activation remains pending.
- [x] User confirmed image processing/SDR video as core, with HDR preservation and SeedVR2 3B as optional Labs. Existing FP16/FP8 variants remain included where compatible. Explicit powerful-hardware, long-processing-time and memory-limit wording is included in local app descriptions and beta materials.
- [ ] Public beta acceptance completed. The existing readiness register still has **10 unresolved blocking requirements**, plus two optional Labs items. Its strict check currently exits with code 1. Production signing, actual installer upgrades and long-video acceptance are still open.

The register predates the updater work and the Store route. Its count does not include every newly documented item below. When preparing the separate beta configuration, add the production updater and HDR acceptance requirements and record Store certification/signing as the Windows MSIX trust path. The existing Windows PFX requirement concerns direct installers. Apple account readiness does not establish a signed or notarized build. No existing gate is marked passed by this checklist update.

**Current step: finish preparation before candidate builds**

The user asked to keep the Mac candidate build, signing, notarization and installed/update tests on the to-do list and complete other prerequisites first. Those tasks remain unchecked in sections 4–7. The verified identity and Keychain profile are ready for that later stage. [Apple setup evidence](apple-signing.md).

Work through the following preparation first:

1. Confirm advertised platforms, Windows engine delivery and the remaining checkpoint offering. The core/Labs split and SeedVR2 3B inclusion are now confirmed; each advertised package/backend still needs installed acceptance.
2. Complete publisher details in the privacy/support draft and agree the support expectations and private-message retention during university. The contact is confirmed as `hermes.reisner@gmail.com`.
3. Prepare the selected public GitHub issue tracker using the [local repository files](../packaging/beta-feedback/README.md), then publish and test access when remote work is authorized. Contact and tracker type do not need reconfirmation.
4. Review the prepared Store text and reviewer procedure against that scope. Capture the planned screenshots from the exact Windows candidate later.
5. Prepare the website/privacy/support destinations and production update configuration locally; verify public access after publication is authorized.

There is no additional Apple certificate or notarization password to obtain at this stage.

**The decisions, in the order we will make them**

| Step | Decision | Choice or starting recommendation |
| --- | --- | --- |
| 1 | Beta audience | **Confirmed:** friends and voluntary testers reached through Reddit. Prepare public access for those testers; no announcement or publication is authorized yet. |
| 2 | Publisher and account setup | Store identity supplied; Apple signing identity and notarization authentication verified for team `Z2TU844D84`, certificate subject country `CH`. Publisher account type remains to record. Candidate build/signing acceptance is scheduled after the other preparation. |
| 3 | Windows distribution | The user created a Store MSIX draft and supplied its identity. Local package preparation is implemented; the native Windows package and installed acceptance remain to do. Keeping a direct EXE download is a separate choice. |
| 4 | Which operating systems and GPUs ship in beta 1? | Prepare Apple Silicon and the Linux CPU/AMD paths first; include Windows CPU and other GPU packages only when their exact installers pass on matching hardware. Defer unverified targets explicitly. |
| 5 | What counts as supported versus experimental? | **Confirmed:** image processing and SDR video as core; HDR preservation and SeedVR2 3B FP16/FP8 as optional Labs where compatible. De-flicker and video-face processing retain existing Labs status. State the need for powerful hardware and potentially very long processing times clearly. |
| 6 | Which model checkpoints can be offered publicly? | Prefer documented checkpoint rights for the main catalog. Review your face forks and the two ambiguous RealPLKSR checkpoints together before deciding their beta availability. |
| 7 | Downloads, source visibility and feedback? | **Confirmed:** public GitHub Issues for bugs; `hermes.reisner@gmail.com` for contact/private requests. A separate feedback repository is prepared locally. Final download destination and public access testing remain pending. |
| 8 | Budget, build capacity and release timing? | **Confirmed constraint:** limited maintenance time during university for roughly the next six months. Recommend a small, well-tested beta without a promised update schedule; confirm costs and isolated build capacity before setting a date. |

Only the entries explicitly marked confirmed record user decisions. The remaining entries are proposals. No models have been removed and no platform has been dropped by this checklist.

**1. Confirm audience, publisher and accounts — you decide; I guide**

- [x] Record friends and Reddit volunteers as the intended beta audience.
- [x] Record the user's Apple Developer account as ready and verify the downloaded signing certificate against the local CSR/private key.
- [ ] Confirm publisher type in the decision log.
- [x] Record Apple Team ID `Z2TU844D84` and certificate subject country `CH` from the issued certificate. The publisher account type is still a separate pending decision.
- [ ] Agree a signing/build budget before purchasing anything.
- [x] Choose the tester contact/feedback route: public GitHub Issues for bugs and `hermes.reisner@gmail.com` for contact/private requests.
- [ ] Publish the prepared tracker when remote work is authorized, then verify public reading while signed out and issue creation as an ordinary tester. Do not use the private source repository's issue URL as the public support destination.
- [ ] Agree maintenance expectations: recommend one feedback channel, clear known issues and no promised release cadence during university. Decide how to pause downloads or notify testers if a serious issue is found while maintenance capacity is limited.

The Developer ID Application certificate and notarization authentication are verified locally. The complete application still needs signing, notarization and installed acceptance. [Apple setup and verification record](apple-signing.md).

For Windows, **Microsoft Store registration is free through the new onboarding flow**, for both [individuals](https://learn.microsoft.com/en-us/windows/apps/publish/whats-new-individual-developer) and [companies](https://blogs.windows.com/windowsdeveloper/2026/05/07/publish-to-microsoft-store-as-a-company-now-with-free-registration-and-faster-onboarding/). Use [the Store developer entry point](https://storedeveloper.microsoft.com/) for that flow. Identity/account verification still applies.

**An MSIX package distributed through the Store receives free Microsoft signing after certification.** It does not require purchasing a signing certificate. A Store listing for an MSI/EXE installer does require the publisher's own trusted Authenticode signature. LocalSR currently builds a Windows EXE installer; Store MSIX compatibility has not been established. [Microsoft signing options](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/code-signing-options).

If we retain direct installer downloads, choose their signing provider separately. Microsoft's Artifact Signing Public Trust service currently supports Swiss **organizations**, but only US/Canadian **individuals**. That restriction applies to this direct-signing service, not to the free Store MSIX signing route. [Artifact Signing requirements](https://learn.microsoft.com/en-us/azure/artifact-signing/quickstart).

You complete identity verification, account agreements and purchases directly with the provider. Passwords, private signing keys and identity documents stay out of chat and the repository.

**2. Agree the beta scope — we decide together**

- [ ] Name the exact OS versions, architectures and GPU backends we will advertise.
- [ ] Assign one real test machine/tester for every advertised GPU package. A CPU-only VM does not establish GPU support.
- [ ] Decide whether Intel Mac, DirectML and Intel XPU wait for a later beta. Their current release/runtime questions need resolution or explicit deferral.
- [x] Record the confirmed core/Labs split and optional SeedVR2 3B FP16/FP8 inclusion, with clear hardware and processing-time warnings.
- [ ] Finalize tested minimum requirements for the advertised packages. SeedVR2's weight size or a 16 GB baseline is not a guarantee that a particular resolution fits.
- [ ] Give each retained Labs feature specific limitations and acceptance criteria. A broken core video path remains blocking even if SeedVR2 is experimental.

The current target registry contains eight Tauri targets; changing beta scope requires corresponding changes to the future beta target registry, artifact manifest, readiness register, release notes and website. We make those changes after the scope decision, separately from `.12`.

**3. Resolve model distribution — we decide; I prepare the evidence**

- [ ] Review the exact checkpoint and training-data evidence for both HAT face forks, including what redistribution and use are permitted.
- [ ] Clarify the RealPLKSR publisher's `CC-BY-0.4` string, or agree a beta catalog/default that does not rely on an unresolved permission claim.
- [ ] Record which models are stock defaults, optional downloads, user imports or deferred from the beta offering.
- [ ] Align model cards, download acknowledgements, attribution and commercial-use labels with that decision.
- [ ] Ensure installers do not redistribute checkpoint files without the required rights.

The existing [model-license evidence](model-licenses.md) is the starting point. A download checkbox does not settle missing rights. We will preserve the local research setup while deciding what is appropriate to offer to beta users. Any publisher contact is a separate action for you to authorize; none has been sent.

**4. Sign the application — you provide account access; I implement and verify**

- [x] **Mac account:** user reports the Apple Developer account is ready.
- [x] **Mac certificate:** installed the user-created Developer ID Application certificate and verified the matching private key, trust chain and real native signing with an Apple secure timestamp. Team ID `Z2TU844D84`; certificate expires 13 September 2031. The private key remains in this Mac's login Keychain. [Verification record](apple-signing.md).
- [x] **Mac notarization access:** verified an authenticated request to Apple with the `LocalSR-Z2TU844D84-notary` profile saved in this Mac's login Keychain. No credential was exported or logged by verification. Local renewal instructions are in the [Apple setup guide](apple-signing.md); production runner configuration and key recovery remain separate tasks.
- [ ] **Mac candidate signing — deferred until the other prerequisites are ready:** prepare a maintained native Apple-Silicon build in isolated directories, sign the app and embedded worker/native libraries with the required runtime options and entitlements, and retain the signature verification report. This depends on the build work in section 6.
- [ ] **Mac notarization and distribution:** submit the signed candidate to Apple's notarization service, inspect the result, attach the notarization ticket and validate it for the app and final DMG. Verify Gatekeeper acceptance and real inference from that downloaded candidate on a separate Mac, followed by the update/data tests in sections 5 and 7. [Apple notarization workflow](https://developer.apple.com/documentation/security/customizing-the-notarization-workflow).
- [ ] **Windows distribution:** decide Store MSIX, direct installer, or both before buying a certificate.
- [x] **Store identity:** reserved LocalSR product supplied by the user and recorded in the [local MSIX preparation guide](microsoft-store.md). The layout tool and Store artwork are prepared locally; Windows packaging and installed acceptance remain open.
- [ ] **Windows Store:** complete the ordered Store checklist below, including installation of the Microsoft-signed package after certification. Local layout preparation does not establish installed compatibility or certification.
- [ ] **Windows direct installer, if retained:** choose a provider that supports your identity/country and unattended signing, adapt the pipeline to its key-storage method, then sign and timestamp the app and installer and verify Authenticode trust. This also applies to submitting an EXE/MSI installer to the Store.
- [ ] **Linux:** create the selected distributable package, retain its checksums and provide signed updater artifacts. Test the package on the advertised distributions.
- [ ] Document certificate/key renewal and a recovery contact. Store secrets in the chosen protected signing environment.

The current Windows workflow builds an EXE and expects PFX input. A future Store edition needs a separate MSIX packaging and acceptance path; a retained direct installer must follow its selected provider's supported signing method. Record the chosen trust path and its actual acceptance evidence in the future beta readiness register. The current trust requirement remains unresolved. The current Mac workflow also requires a native ARM64 runner but is still routed to the old X64 label; both routing and actual build capacity need correction in the future beta workflow.

**Microsoft Store: current preview to publication, in order**

- [ ] **1. Choose Windows engines.** Keep CPU processing available and decide which GPU backends the first Store candidate includes. Test each advertised backend on matching hardware. One x64 Store identity does not choose between separate CPU/CUDA/DirectML packages by GPU vendor; settle engine delivery before creating alternative packages.
- [ ] **2. Finish Store integration.** Make update controls use Store-managed updates and prevent a cached direct-distribution engine override from replacing the packaged engine. Verify writable data paths, WebView2 availability and preservation of existing preferences, recipes, queue data and downloaded models.
- [ ] **3. Build the native MSIX.** Use the reserved identity, a documented Store package version and the complete frozen engine/runtime dependencies. Build in an isolated Windows environment, run MakeAppx schema validation and the Windows App Certification Kit, and retain package hashes and reports. The [Store preparation guide](microsoft-store.md) has the commands; its 18 layout tests do not replace these checks.
- [ ] **4. Test the installed candidate.** Complete section 7 on the exact MSIX: fresh install, downloads/imports, image enhancement, representative long MOV export, media switching/playback, aligned render tiles, ETA, separate CPU/GPU benchmarks, cancellation/restart and resource-pressure recovery. Test an upgrade between two package versions and data preservation; check reinstall/uninstall separately. The last recorded Windows rerun still needs completion on the current candidate.
- [ ] **5. Finalize the public beta offering.** Complete the checkpoint decisions in section 3, state the supported/Labs limits, and provide working privacy/support information and a feedback route accessible to friends and Reddit testers. The [privacy/support text](beta-privacy-and-support.md) includes the confirmed Gmail contact and GitHub Issues route; publisher details and public hosting remain open.
- [ ] **6. Complete listing, upload and certification.** Review the prepared [English listing and reviewer notes](beta-store-submission.md) against the installed candidate, then capture its four planned Windows screenshots (1920 × 1080 PNG recommended; one is the minimum). Upload the tested MSIX. Review the release timing/visibility before submitting; after certification, release according to the approved settings and verify installation and updates through the Store. Microsoft provides the Store MSIX signature; a paid Windows certificate is not required for this route.

The user asked to add this work to the beta to-do list on 12 September 2026. This records pending work, not authorization to upload or publish. Package and listing requirements are documented by [Microsoft](https://learn.microsoft.com/en-us/windows/apps/publish/publish-your-app/msix/create-app-submission); see also [MSIX signing](https://learn.microsoft.com/en-us/windows/apps/publish/publish-your-app/msix/app-package-requirements) and [screenshot specifications](https://learn.microsoft.com/en-us/windows/apps/publish/publish-your-app/msix/screenshots-and-images).

**5. Enable production updates — I prepare; we verify together**

- [ ] For a selected Store MSIX edition, use Store-managed application updates and adapt the update control accordingly. Verify the engine/backend packaging and data preservation through a real Store package upgrade.
- [ ] For direct-distribution editions, choose the final updater feed/download URLs and Stable/Beta policy.
- [ ] Generate a production updater key for those direct editions in the chosen secure environment and arrange its backup; embed only the public key in their initial beta.
- [ ] Include the correct backend and engine identity in every build and generate the artifacts/manifests required by its distribution path.
- [ ] Test actual upgrades from an older installed candidate on macOS, Windows and the selected Linux package format. Repeat split-engine and changed-engine cases, not only an unchanged engine.
- [ ] Verify interrupted downloads, invalid signatures, low disk space, busy/cancelling queues, unreadable settings and failed-startup recovery.
- [ ] Check that recipes, preferences, queue history and downloaded models survive; confirm uninstall behavior separately.

For direct-distribution editions, the updater's signature is separate from Apple's or Windows' application signing. Tauri requires signatures for updates. Production feeds are currently disabled; their first beta must include the correct public key from its initial installation. [Tauri updater documentation](https://v2.tauri.app/plugin/updater/). Build and manifest details are in [local-updates.md](local-updates.md). A Store MSIX edition would instead use Microsoft's hosting and automatic application updates; its packaging and upgrade behavior still require implementation and testing. [Microsoft Store MSIX benefits](https://blogs.windows.com/windowsdeveloper/2026/05/07/publish-to-microsoft-store-as-a-company-now-with-free-registration-and-faster-onboarding/).

**6. Prepare a separate beta build — I handle implementation**

- [ ] Choose the beta version after checking the release history; keep `.12` and its testing-only exception separate.
- [ ] Synchronize Python/npm/Cargo/Tauri versions, package names, manifests and release notes on a separate beta branch.
- [ ] Use a maintained native Apple-Silicon worker/runtime and resolve the selected backend dependencies.
- [ ] Arrange isolated build capacity. Recheck the account spending/artifact-storage issue recorded during the last Windows rerun before relying on hosted Windows jobs. Do not repurpose an active `.12` runner or VM.
- [ ] Recheck dependency advisories and available scanning/protection. The readiness register records an unresolved Linux GTK/glib issue and runner trust concerns; retain this requirement until supported fixes and evidence, or an explicitly reviewed scope change, resolve it.
- [ ] Build one immutable release candidate and retain package hashes, signing reports, worker versions and test results.

**7. Test the exact candidate — I run tests; you/testers assess real use**

- [ ] Fresh install and first launch on every advertised OS/backend, using the downloadable package without a development environment.
- [ ] Download/import a model, change settings, save a recipe, restart and verify persistence.
- [ ] Test photos, faces, anime/text, transparency, DNG and a large image; verify valid exports and unchanged sources.
- [ ] Complete a representative long MOV export. Fully decode it and inspect orientation, audio, timing, motion and visual integrity. Earlier short-clip results do not establish a full four-minute 4K export.
- [ ] Repeat video A completed → start B → return to A; verify playback, correct frame ownership, aligned tile display and useful ETA.
- [ ] Cancel during verification/loading/inference/encoding, then process another job. Test memory pressure, insufficient disk space and worker recovery.
- [ ] Run separate CPU/GPU benchmarks and verify the correct device labels and independently retained scores.
- [ ] If HDR preservation is included, test HLG and PQ precision/metadata plus actual highlights and colours on an HDR-capable display. Valid 10-bit output alone does not establish HAT model quality on HDR. Confirm SeedVR2 cannot select preservation.
- [ ] Inspect SeedVR2 multi-window continuity and tile seams before making a quality claim; retain explicit Labs limitations if unresolved.
- [ ] Repeat update/reinstall/uninstall and record results for each shipped artifact.

Use [platform acceptance evidence](platform-acceptance-2026-09.md) as prior evidence, then update the [candidate acceptance template](acceptance-record.example.json) for the actual beta version and scope. The previous Windows fixes still need final platform verification; they are not certified by the local Linux updater test.

**8. Release to the agreed audience — final decision together**

- [ ] Update the website with the actual beta version, tested requirements, download sizes, install/update instructions, model limitations and feedback contact.
- [ ] Explain local processing and what model downloads, update checks or optional diagnostic reports contact online. Do not describe all application network activity as zero.
- [ ] Finalize the prepared privacy/support text, verify the final candidate's network behavior, and add working privacy/support links to the app, Store and website. Include locally retained thumbnails, queue history and backups; confirm the Store edition's actual data removal behavior.
- [ ] Test every distribution link with the intended access level and verify checksums/signatures after downloading.
- [ ] Close each applicable readiness requirement with evidence; add the updater/HDR requirements for the agreed beta scope. The strict readiness check must pass for the beta candidate.
- [ ] Review the concrete installers, release notes, known issues and rollback plan together, then publish only to the audience you selected.
- [ ] Collect the first test cycle's feedback, fix blocking failures and decide when to widen access.

```sh
python3 scripts/check_beta_readiness.py --require-beta-ready
```

Today that command correctly fails because beta requirements are unresolved. This checklist does not alter that register or declare the preview beta-ready.

**Our next conversation step**

Contact, bug-report routing, core/Labs scope and SeedVR2 3B inclusion are confirmed.
Next finish platform/engine, remaining checkpoint and publisher details using the
prepared drafts. The user explicitly deferred the Mac candidate build, signing,
notarization and installed/update acceptance until after this preparation. The
audience remains friends and Reddit volunteers, with limited maintenance capacity
during university.
