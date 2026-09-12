**LocalSR beta release checklist and walkthrough**

Updated 12 September 2026 from local preview commit `9dd57a1`, the current release configuration and the [latest verification record](local-update-acceptance.json). This is the working checklist for our decisions together. Approved choices and pending questions belong in the [decision log](beta-release-decisions.md).

The existing `.12` release, its runners and the current Mac GUI stay untouched. This planning work is local. No account purchase, repository visibility change, production key creation or publication has been performed.

**Where we are**

- [x] Local application changes and regression suite verified: 568 Python, 83 frontend and 58 Rust tests passed; 3 Python tests skipped.
- [x] CPU/ROCm worker inference, cancellation and subsequent processing checked on DDP.
- [x] Linux portable signed update, restart and failed-startup rollback tested with a disposable key; recipes, settings, queue data and models survived.
- [x] Both RealPLKSR downloads passed the two-acknowledgement UI flow, checksum verification and AMD inference. This establishes functionality; their license ambiguity remains unresolved.
- [x] Test keys, profiles, services and build caches cleaned up; current DDP preview left open.
- [ ] Public beta acceptance completed. The existing readiness register still has **10 unresolved blocking requirements**, plus two optional Labs items. Its strict check currently exits with code 1. Production signing, actual installer upgrades and long-video acceptance are still open.

The register predates the updater work. Its count does not include every newly documented item below. We must add the production updater and HDR acceptance requirements when preparing the separate beta configuration, without weakening or silently marking existing requirements passed.

**The decisions, in the order we will make them**

| Step | Decision | Starting recommendation — awaiting your choice |
| --- | --- | --- |
| 1 | Invited testers or immediate public beta? | Start with 5–10 invited testers, then expand after one complete test cycle. Signing and data-preservation checks still apply. |
| 2 | Individual or company publisher? | Use your actual legal identity/entity. Confirm the publisher's country before choosing a Windows signing service. |
| 3 | Which operating systems and GPUs ship in beta 1? | Prepare Apple Silicon and the Linux CPU/AMD paths first; include Windows CPU and other GPU packages only when their exact installers pass on matching hardware. Defer unverified targets explicitly. |
| 4 | What counts as supported versus experimental? | Make image upscaling and SDR video the core. Keep HDR preservation, SeedVR2, deflicker and video-face processing opt-in Labs until their quality evidence supports promotion. |
| 5 | Which model checkpoints can be offered publicly? | Prefer documented checkpoint rights for the main catalog. Review your face forks and the two ambiguous RealPLKSR checkpoints together before deciding their beta availability. |
| 6 | Downloads, source visibility and feedback? | For a later public beta, a separate public downloads/issues repository can preserve the current source repository's privacy. Confirm the destination and access requirements first. |
| 7 | Budget, build capacity and release timing? | Confirm signing costs and isolated build capacity before choosing a date. Set a date after the first complete signed candidate passes. |

These are proposals, not accepted changes. In particular, no models have been removed and no platform has been dropped by this checklist.

**1. Confirm audience, publisher and accounts — you decide; I guide**

- [ ] Record invited/public audience and publisher type in the decision log.
- [ ] Confirm the publisher country and whether you already have an Apple Developer membership or Windows signing account. We do not infer enrollment status from repository secrets.
- [ ] Agree a signing/build budget before purchasing anything.
- [ ] Choose a tester contact/feedback route. If distribution is invited-only, test that intended testers can access it; for public distribution, test signed out.

For Apple, individuals and eligible organizations can enroll. Individual enrollment uses a legal name and an Apple Account with two-factor authentication; organizations have additional verification requirements, normally including D-U-N-S. Membership is **USD 99 per year**, with regional pricing shown during enrollment. [Apple enrollment](https://developer.apple.com/programs/enroll/).

For Windows, publisher country and entity type affect provider eligibility. Microsoft currently lists Switzerland for **organizations**, while **individual developers must be in the US or Canada** for its Public Trust service. If you publish as a Swiss individual, we need a different eligible provider; a Swiss Azure region alone does not establish individual eligibility. We will compare suitable providers after confirming your identity type and budget. [Microsoft requirements](https://learn.microsoft.com/en-us/azure/artifact-signing/quickstart).

You complete identity verification, account agreements and purchases directly with the provider. Passwords, private signing keys and identity documents stay out of chat and the repository.

**2. Agree the beta scope — we decide together**

- [ ] Name the exact OS versions, architectures and GPU backends we will advertise.
- [ ] Assign one real test machine/tester for every advertised GPU package. A CPU-only VM does not establish GPU support.
- [ ] Decide whether Intel Mac, DirectML and Intel XPU wait for a later beta. Their current release/runtime questions need resolution or explicit deferral.
- [ ] Record the core/Labs feature split and minimum requirements. SeedVR2's weight size or a 16 GB baseline is not a guarantee that a particular resolution fits.
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

- [ ] **Mac:** obtain a Developer ID Application certificate, configure signing/notarization credentials securely, sign the app and bundled executables, notarize, staple and verify the downloaded distribution on a separate Mac. Developer ID Application signs the app; Developer ID Installer is for an Installer Package if we use one. [Apple certificate guide](https://developer.apple.com/help/account/certificates/create-developer-id-certificates/).
- [ ] **Windows:** choose a provider that supports your identity/country and unattended signing, adapt the pipeline to its key-storage method, then sign and timestamp the app and installer and verify Authenticode trust.
- [ ] **Linux:** create the selected distributable package, retain its checksums and provide signed updater artifacts. Test the package on the advertised distributions.
- [ ] Document certificate/key renewal and a recovery contact. Store secrets in the chosen protected signing environment.

The current Windows workflow expects PFX input. That is an existing implementation detail, not a reason to buy a certificate that does not fit your needs. The pipeline must follow the selected provider's supported signing method. The current Mac workflow also requires a native ARM64 runner but is still routed to the old X64 label; both routing and actual build capacity need correction in the future beta workflow.

**5. Enable production updates — I prepare; we verify together**

- [ ] Choose the final updater feed/download URLs and Stable/Beta policy.
- [ ] Generate a production updater key in the chosen secure environment and arrange its backup; embed only the public key in the initial beta.
- [ ] Include the correct backend and engine identity in every build and generate the matching signed artifacts/manifests.
- [ ] Test actual upgrades from an older installed candidate on macOS, Windows and the selected Linux package format. Repeat split-engine and changed-engine cases, not only an unchanged engine.
- [ ] Verify interrupted downloads, invalid signatures, low disk space, busy/cancelling queues, unreadable settings and failed-startup recovery.
- [ ] Check that recipes, preferences, queue history and downloaded models survive; confirm uninstall behavior separately.

The updater's signature is separate from Apple's or Windows' application signing. Tauri requires signatures for updates. Production feeds are currently disabled; the first beta must include the correct public key from its initial installation. [Tauri updater documentation](https://v2.tauri.app/plugin/updater/). Build and manifest details are in [local-updates.md](local-updates.md).

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
- [ ] Test every distribution link with the intended access level and verify checksums/signatures after downloading.
- [ ] Close each applicable readiness requirement with evidence; add the updater/HDR requirements for the agreed beta scope. The strict readiness check must pass for the beta candidate.
- [ ] Review the concrete installers, release notes, known issues and rollback plan together, then publish only to the audience you selected.
- [ ] Collect the first test cycle's feedback, fix blocking failures and decide when to widen access.

```sh
python3 scripts/check_beta_readiness.py --require-beta-ready
```

Today that command correctly fails because beta requirements are unresolved. This checklist does not alter that register or declare the preview beta-ready.

**Our next conversation step**

Start with audience and publisher identity. Once you answer those, I will record the choices and take you through the matching enrollment/signing path, including what you already have, what needs buying and what I can configure. We will make platform and model choices next, then turn the agreed scope into a build/test plan.
