**LocalSR beta decisions**

Started 12 September 2026. Companion to the [beta checklist](beta-release-checklist.md). Recommendations are not approvals. Update this file as the user answers; do not infer acceptance from elapsed time.

**Confirmed constraints**

| Constraint | Source/status |
| --- | --- |
| Preserve the active `.12` release, its checkouts, jobs and runners | Explicit user instruction; confirmed |
| Keep current work local; no publication or remote changes | Explicit user instruction; confirmed |
| Leave the current Mac GUI alone; use isolated environments for GUI acceptance | Explicit user instruction; confirmed |
| Preserve media, recipes, settings and downloaded models; clean disposable test artifacts | Explicit user instruction; confirmed |
| Decide beta scope and signing/account choices with the user | Current request; confirmed |
| Finish other prerequisites before the Mac candidate build/signing/notarization tests | User instruction on 12 September 2026; candidate work remains on the to-do list |
| Limited maintenance capacity during university for roughly the next six months | Explicit user statement on 12 September 2026; account for this in scope and release expectations |

**Decisions and pending choices**

| ID | Decision | Recommendation/context | Status |
| --- | --- | --- | --- |
| B1 | Beta audience | Friends and voluntary testers reached through Reddit; prepare access for that audience while retaining signing and acceptance requirements | Confirmed by user; no publication or Reddit post authorized |
| B2 | Individual or registered-company publisher | Use the actual legal identity/entity; this controls signing eligibility | Asked; awaiting answer |
| B3 | Publisher country and existing developer/signing accounts | Store identity supplied; Apple Developer ID Application certificate verified for team `Z2TU844D84`, subject country `CH`, expiring 13 September 2031; notarization authentication verified using the local Keychain profile | Store identity and local Apple signing/notarization setup verified; publisher account type remains pending |
| B4 | Budget and build capacity | Assess the free Store MSIX route before buying a Windows certificate; confirm available isolated runners and plan for limited maintenance during university | Capacity constraint confirmed; budget and implementation choices pending |
| B5 | Included platforms and GPUs | Start with testable Apple-Silicon/Linux paths; advertise each Windows/other GPU package only after its actual installer passes | Proposed; not approved |
| B6 | Core versus Labs features | Image processing/SDR video core; HDR preservation and SeedVR2 3B remain optional Labs. Retain FP16 and FP8 variants where the shipped backend supports them. De-flicker/video faces keep their existing Labs status | Core plus experimental HDR/SeedVR2 and 3B inclusion confirmed by user; exact package/backends still require acceptance |
| B7 | Public model catalog and defaults | Review face-fork/training rights and RealPLKSR ambiguity; preserve local research models during that decision. SeedVR2 3B retention and its clearer descriptions are covered by B6/B16 | Remaining checkpoint/default choices pending; no removal or change to checkpoint permissions has been approved |
| B8 | Download destination, source visibility and feedback | Public GitHub Issues for bug reports; `hermes.reisner@gmail.com` for contact/private requests. Prepare a separate feedback repository locally, keeping source visibility unchanged | Contact and tracker type confirmed by user. Public activation, access testing and download destination remain pending |
| B9 | Production update feed, key custody and channel policy | Direct editions: separate Stable/Beta feeds, protected key and matching engine packages. If selected, Store MSIX edition: Store-managed application updates with tested data preservation | Pending; depends on B11 |
| B10 | Beta version, release date and expansion to public access | Select after account readiness, scope and an initial signed candidate test cycle | Pending |
| B11 | Windows Store MSIX, direct installer, or both | User created an MSIX Store draft and supplied its reserved identity; prepare the Store package locally. Direct installer distribution and engine delivery strategy remain open | Local Store preparation in progress; no submission/publication approved |
| B12 | Maintenance and feedback expectations | Recommend a small, well-tested beta, one feedback channel, clear known issues and no promised update cadence during the next six months | Proposed; user confirmed limited time, not this exact operating plan |
| B13 | Deferred Apple build task | Developer ID certificate/private-key setup, real timestamped native signing and notarization authentication passed. Keep the native candidate build, signing, notarization and installed/update tests on the to-do list while other prerequisites are completed first | Order confirmed by user; candidate work remains pending |
| B14 | Microsoft Store remaining work | Track engine selection, Store integration, native MSIX build, installed acceptance, public beta content and listing/upload/certification in order | Added to the beta to-do list at the user's request; no upload or publication authorized |
| B15 | Preparation before candidate builds | English Store text, reviewer procedure, four-shot Windows capture plan, privacy/support text and tracker files are prepared locally, including the confirmed contact. Finish publisher/scope decisions, then prepare the actual destinations and packages | Contact choice incorporated; final content and publication remain pending |
| B16 | Hardware and processing-time expectations | State that demanding video needs powerful compatible hardware and substantial memory, can take hours/days or longer, and should first be tried on a short clip. FP8 download size and a nominal 16 GB are not workload-fit guarantees | Explicit user request; included in local app descriptions, Store/support/tracker text and website preview notes |

**Current evidence**

Local preview evidence is recorded at [local-update-acceptance.json](local-update-acceptance.json). Source repository `HerRei/local-upscale` was confirmed private, with issues enabled, via the GitHub API on 12 September 2026. This does not give general testers access. The strict beta readiness check still reports ten unresolved blocking requirements; no gate has been relaxed or marked passed by this planning work.

The audience question is answered: friends and Reddit volunteers. The legal publisher account type remains unanswered. Public access is a planning requirement for this audience, not authorization to publish or contact testers.

The user created a Developer ID Application certificate and authorized local installation after downloading it. The certificate matches the user's CSR and private key; its Apple G2 chain is trusted without a custom trust override. Team ID `Z2TU844D84` and certificate subject country `CH` are taken from the issued certificate. A disposable ARM64 executable passed signing with hardened runtime and an Apple secure timestamp, signature verification and execution. The private key was not exported, temporary artifacts were removed and `.12` was preserved. [Public verification record](apple-signing-acceptance-2026-09.json).

The user completed the notarization helper. An authenticated `notarytool history` request using `LocalSR-Z2TU844D84-notary` succeeded on 12 September 2026; verification did not export or log the stored credentials. The completed Desktop helper was removed after checking its contents. Local Apple account setup is complete. A complete signed/notarized LocalSR candidate and installed Gatekeeper/update acceptance remain open. This setup does not configure production release jobs or authorize publication. [Next build steps](apple-signing.md).

The latest Partner Center screenshot marks pricing/availability, properties and age ratings complete. Packages are incomplete, Store listings are not started and submission options are recommended; the product remains a draft. The local MSIX layout helper, identity and artwork have 18 packaging tests, but no finished Windows MSIX has been built or certified. The six remaining Store stages are now explicit tasks in the [beta checklist](beta-release-checklist.md).

Following the user's request to do other prerequisites first, local [listing and
certification notes](beta-store-submission.md), [privacy/support text](beta-privacy-and-support.md)
and a [feedback form](../packaging/beta-feedback/README.md) are prepared. The privacy
draft records local queue thumbnails/backups, external model/update requests and
user-initiated diagnostic sharing. The user confirmed `hermes.reisner@gmail.com`
as contact and public GitHub Issues for bugs. Publisher details, private-message
retention and final installed-package behavior remain to confirm. No public page, Store
entry, active issue template, checkpoint download or release gate was changed.

The user then confirmed the core/Labs split, requested clear capable-hardware and
long-processing-time warnings, and asked to include the 3B model. Both SeedVR2
3B FP16 and FP8 were already present in the catalog. Their descriptions now make
the hardware, duration and SDR-only output explicit; checkpoint IDs, files,
hashes, requirements and model behavior are unchanged. Website preview wording
and the prepared Store/support/tracker text carry the same warning locally.

The Store package identity is now recorded in [microsoft-store.md](microsoft-store.md).
The display name `HerRei` does not establish whether the account is Individual or
Company. The user requested conservative provisional hardware fields; suggested
targets are 16 GB RAM minimum, 32 GB recommended, CPU processing available, and
16 GB recommended GPU memory. For demanding SeedVR2 video, 64 GB RAM / 24 GB VRAM
were suggested. These are planning targets, not measured compatibility guarantees.

Microsoft's current documentation confirms free Store registration for [individuals](https://learn.microsoft.com/en-us/windows/apps/publish/whats-new-individual-developer) and [companies](https://blogs.windows.com/windowsdeveloper/2026/05/07/publish-to-microsoft-store-as-a-company-now-with-free-registration-and-faster-onboarding/). Store **MSIX** receives free Microsoft signing after certification; a Store **EXE/MSI** listing still requires the publisher to sign its installer. [Signing requirements](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/code-signing-options). The checklist's earlier assumption that Windows necessarily needs a paid signing provider has been corrected. LocalSR Store packaging and certification are not yet verified.
