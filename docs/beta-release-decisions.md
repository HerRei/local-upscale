**LocalSR beta decisions**

Started 12 September 2026; updated 13 September 2026. Companion to the [beta checklist](beta-release-checklist.md). Recommendations are not approvals. Update this file as the user answers; do not infer acceptance from elapsed time.

**Confirmed constraints**

| Constraint | Source/status |
| --- | --- |
| Preserve the active `.12` release, its checkouts, jobs and runners | Explicit user instruction; confirmed |
| Keep release preparation local until final review | Explicit user instruction; confirmed. Public-source preparation is approved under B19. The user separately authorized updating the public GitHub profile README and native Windows acceptance access. |
| Leave the current Mac GUI alone; use isolated environments for GUI acceptance | Explicit user instruction; confirmed |
| Preserve media, recipes, settings and downloaded models; clean disposable test artifacts | Explicit user instruction; confirmed |
| Decide beta scope and signing/account choices with the user | Current request; confirmed |
| Finish other prerequisites before the Mac candidate build/signing/notarization tests | Order preserved; the requested bug work and native Windows acceptance preceded the signed Mac candidate. Notarization and mounted-package verification passed; native installed upgrades remain |
| Limited maintenance capacity during university for roughly the next six months | Explicit user statement on 12 September 2026; account for this in scope and release expectations |

**Decisions and pending choices**

| ID | Decision | Recommendation/context | Status |
| --- | --- | --- | --- |
| B1 | Beta audience | Friends and voluntary testers reached through Reddit; prepare access for that audience while retaining signing and acceptance requirements | Confirmed by user; no publication or Reddit post authorized |
| B2 | Publisher identity and purpose | Publish personally as **Hermes Reisner**, with **HerRei** as project branding; this beta is a hobby project outside business activity | Confirmed by the user's “yes” on 13 September 2026 after reviewing Microsoft/Apple account guidance. This does not submit account declarations or establish trader status. |
| B3 | Publisher country and existing developer/signing accounts | Store identity supplied; Apple Developer ID Application certificate verified for team `Z2TU844D84`, subject country `CH`, expiring 13 September 2031; notarization authentication verified using the local Keychain profile | Store identity and local Apple signing/notarization setup verified; personal publisher choice confirmed under B2. Any outstanding account-holder/trader declarations remain separate. |
| B4 | Budget and build capacity | Assess the free Store MSIX route before buying a Windows certificate; confirm available isolated runners and plan for limited maintenance during university | Capacity constraint confirmed; budget and implementation choices pending |
| B5 | Included platforms and GPUs | Retain all eight existing Tauri targets. Main testing focus: macOS MPS, Linux ROCm and Windows CPU/Intel iGPU; other hardware paths remain available as Labs. Use recorded workload/device evidence for tested claims | Confirmed by user; [coverage matrix](beta-platform-matrix.md) records prior checks and gaps. Native UHD 620 DirectML inference, installed GUI and separate CPU/iGPU benchmarks are now recorded |
| B6 | Core versus Labs features | Image processing/SDR video core; HDR preservation and SeedVR2 3B remain optional Labs. Retain FP16 and FP8 variants where the shipped backend supports them. De-flicker/video faces keep their existing Labs status | Core plus experimental HDR/SeedVR2 and 3B inclusion confirmed by user; exact package/backends still require acceptance |
| B7 | Public model catalog and defaults | Retain NomosWebPhoto/HFA2k with verified in-app downloads, accurate CC BY 4.0 attribution and source/license links. Author declarations, intended application downloads and exact checkpoint provenance support this use. Face forks retain their separate import policy. [Evidence](beta-model-license-choices.md). | Confirmed by the user on 13 September 2026, conditional on respecting the author's license and intent. Implemented and tested in isolated beta source; final native packages must be rebuilt. SeedVR2 scope is covered by B6/B16. |
| B8 | Download destination, source visibility and feedback | Public GitHub Issues for bug reports; `hermes.reisner@gmail.com` for contact/private requests. Prepare tracker files locally; source publication follows B19 and final release review | Contact and tracker type confirmed by user. Tracker activation, access testing and download destination remain pending. The separate public profile README update is authorized |
| B9 | Production update feed, key custody and channel policy | Direct editions: separate Stable/Beta feeds, protected key and matching engine packages. If selected, Store MSIX edition: Store-managed application updates with tested data preservation | Production key retained in Keychain; signature/tamper checks passed. Production signatures and actual candidate download/tamper checks pass; native installation/recovery, hosting/feed deployment and independent key backup remain pending |
| B10 | Beta version, release date and expansion to public access | Provisional application version `0.0.13-beta.1`, Store package `1.0.7.0`, Apple build `13.1`; no release date set | Asked; version remains provisional, publication unapproved |
| B11 | Windows Store MSIX, direct installer, or both | User created an MSIX Store draft and supplied its reserved identity; prepare the Store package locally. Direct installer distribution and engine delivery strategy remain open | CPU/DirectML Store package built and upgraded successfully through 1.0.7.0. Other direct-edition packages/trust remain open; no submission/publication approved |
| B12 | Maintenance and feedback expectations | Retain the agreed feature/backend scope with clear Labs limitations. Recommend one feedback channel, known issues and no promised update cadence during the next six months | Full scope and Labs approach confirmed; support cadence still proposed |
| B13 | Deferred Apple build task | Developer ID certificate/private-key setup, real timestamped native signing and notarization authentication passed. Keep the native candidate build, signing, notarization and installed/update tests on the to-do list while other prerequisites are completed first | Order preserved; maintained ARM64 candidate signed, bundled-host and six MPS cases passed, app/DMG notarized, stapled and Gatekeeper accepted; native installed upgrades remain |
| B14 | Microsoft Store remaining work | Track delivery of the retained Windows engines, Store integration, native MSIX build, installed acceptance, public beta content and listing/upload/certification in order | Added to the beta to-do list at the user's request; no upload or publication authorized |
| B15 | Preparation before candidate builds | English Store text, reviewer procedure, four-shot Windows capture plan, privacy/support text and tracker files are prepared locally, including the confirmed contact. Feature/backend scope is recorded; finish publisher and delivery details, then prepare the actual destinations and packages | Contact and scope choices incorporated; final content and publication remain pending |
| B16 | Hardware and processing-time expectations | State that demanding video needs powerful compatible hardware and substantial memory, can take hours/days or longer, and should first be tried on a short clip. FP8 download size and a nominal 16 GB are not workload-fit guarantees | Explicit user request; included in local app descriptions, Store/support/tracker text and website preview notes |
| B17 | Intel integrated graphics | User explicitly requested local implementation/verification before the next beta decision. Retain Windows DirectML and compatible Linux XPU paths; distinguish runtime support from actual device test evidence | Local selection, naming and discovery-error fixes passed regression checks; [Intel GPU notes](intel-gpu-support.md). Real UHD 620 inference and installed acceptance recorded; other Intel adapters remain untested |
| B18 | Native Windows Intel test device | User authorized SSH, Tailscale and full GUI access for native Intel/iGPU and Store/MSIX testing; the device must not become a build runner | SSH, file transfer and desktop input verified over LAN and Tailscale; secure-desktop UAC handling verified over LAN. Automatic sleep disabled as requested. Native CPU/iGPU inference, GUI, cancellation/recovery, data-preserving MSIX upgrades and separate benchmarks are recorded. Final WACK is WARNING; uninstall/reinstall backup restoration passed; external Open/Reveal observation remains. The device is not a general build runner. [Setup and hardware evidence](windows-intel-test-host.md) |

**Additional concrete choices**

Publisher basis checked on 13 September 2026: Microsoft's
[account guidance](https://learn.microsoft.com/en-us/windows/apps/publish/partner-center/open-a-developer-account)
places hobby/personal distribution outside business activity in the Individual
category; business/professional distribution requires Company even if free.
Apple's [enrollment guidance](https://developer.apple.com/help/account/membership/program-enrollment/)
requires the individual's legal name. Any applicable trader declaration needs its
own factual assessment; individual enrollment and a free price do not settle it.
The user approved the personal hobby-project identity, not publication or a
change to model licenses, support commitments or the provisional release version.

| ID | Decision | Recommendation/context | Status |
| --- | --- | --- | --- |
| B19 | Multimedia and corresponding source | Make LocalSR's release source, build instructions and required exact dependency sources available with the beta, retaining the application's MIT notice. Audit the actual GPL/LGPL/native/GPU combinations and rebuild components as required; publishing source alone does not establish compliance. Preserve the agreed features. [Evidence](beta-dependency-review.md). | Approved 13 September 2026, conditional on thoroughly cleaning source and READMEs and respecting upstream terms. Public-source preparation is authorized; repository visibility and final release publication await package review. The user also authorized adding LocalSR to their public GitHub profile README |
| B20 | Windows Intel iGPU runtime | Prototype Windows ML/ONNX Runtime to retain Intel acceleration without the old torch-directml/Torch 2.4.1 dependency. Adopt only after model equivalence, custom-model behavior, native acceptance and dependency/privacy review. [Proposal and results](windows-inference-runtime-review.md). | Approved 13 September 2026. Replacement integrated in isolated beta source. Corrected separate CPU/iGPU benchmarks and worker image/video/tiles/cancellation/pressure checks pass. SIDD photo comparisons still exceed tolerance; no CPU restriction is approved. Accepted 1.0.7.0 MSIX remains the earlier baseline; no replacement package is built |
| B21 | Large native downloads and Windows build capacity | ROCm 6.24 GB / CUDA 5.09 GB AppImages need suitable immutable HTTPS hosting or a completed split-engine update design. Use the Mac mini's existing Windows x64 VM for separate beta builds when it is free; native Intel stays acceptance-only. | User selected the existing VM, then explicitly paused ALL beta package builds until their .12-complete/build go-ahead. No polling, timers, scheduled builds or alternative host. Dedicated release-directory hosting configuration and local tests are prepared; public HTTPS/domain/WAN/availability and cost choice remain unapproved |
| B22 | Retire Slint | Remove its screens, Python bridge, dependency, installer and build checks. Launch Tauri from the Python entry point; retain the engine, CLI and optional Qt Widgets client. Migrate useful tests. | Approved and source cleanup verified 13 September 2026: 595 Python, 110 frontend and 67 Rust tests pass; wheel/sdist contain no Slint. `.12`, accepted packages and this Mac’s GUI remain untouched. New installed packages still require acceptance. [Cleanup record](slint-retirement.md) |

**Current evidence**

The [acceptance record](beta-acceptance-2026-09-12.md), [review summary](beta-review-summary.md)
and `build/beta-review/README.md` identify actual packages and remaining work.
The separate [readiness register](../ci/public-beta-readiness.json) remains **not ready**.

Windows has a real CPU/DirectML MSIX 1.0.7.0. Upgrades preserve settings, recipes,
model hashes and logical queues. Explicit uninstall/reinstall deletes the profile,
but restoration from the verified backup preserves it. WACK remains WARNING despite
native PerMonitorV2 evidence. Store certification and external Open/Reveal observation
are not complete; the acceptance certificate is temporary test trust only.

macOS ARM64 beta app and DMG are Developer ID signed, notarized, stapled and
Gatekeeper accepted. The mounted DMG starts its bundled worker; six MPS cases pass.
This Mac's GUI and Keychain private material remain untouched by GUI/export tests.
Native installed upgrade/recovery remains open.

All four Linux AppImages have exact hashes, production update signatures, bundled
host handshakes and all-ELF architecture checks. CPU/ROCm workers pass six real cases;
final ROCm native video switching and bounded 4K/file-limit recovery pass. CUDA/XPU
physical-device inference is untested. The native folder-dialog observation and
signed native upgrade checks remain open.

The application's downloader accepts the actual production-signed CPU candidate
and rejects truncation and substituted content. This does not establish complete
native update/restart/rollback. Draft feed URLs are deliberately unusable until
hosting, final artifacts and public deployment are agreed. The key remains in
Keychain and needs an independent protected backup.

The NomosWebPhoto/HFA2k policy is approved under B7 and implemented locally.
The public-source approach is approved under B19; exact dependency sources and
compatibility of the bundled components still require work. No restoration checkpoints are bundled.
Author credit, source/license links and verified downloads replace the old
non-commercial-only acknowledgement for those two models. Face imports retain
their existing restrictions. Routine packaging-tool updates are also prepared
in source, but retained binary candidates predate these changes and expanded
notices. Final rebuilds and affected tests remain our work once the other
distribution choices are settled.

Actual Store screenshots/listing/reviewer samples, website pages, tracker files,
release notes and draft download/update manifests are assembled for local review.
No LocalSR release, source repository, website deployment or Store submission has
been published. No model publisher has been contacted. The separately authorized
GitHub profile README introduces LocalSR as a beta in preparation. The contact
remains **hermes.reisner@gmail.com**.


## Latest rollout instructions and unanswered choices

Website/GitHub beta first, Microsoft Store afterwards. Current work ends after
independent preparation. The required next signal is the user's explicit
“.12 has concluded; you may build the beta.” No package build, publication,
public server or host/network/service configuration change is authorized now.

| ID | Decision | Recommendation | Status |
| --- | --- | --- | --- |
| B23 | NAFNet SIDD CPU/GPU discrepancy | Investigate first: locate the meaningful divergence, inspect representative output and measure CPU/GPU performance in an initial bounded 2–4-hour investigation; do not promise a fix within that window | User agreed to further investigation, then explicitly deferred it to a later point. Not started or scheduled. No CPU restriction, fallback or release waiver approved. Final runtime treatment remains open. |
| B24 | Direct Windows download trust | Follow the user-selected unsigned EXE/NSIS route for this beta, with clear publisher-warning wording. Keep hashes and production updater signatures; these do not provide Windows publisher trust | User chose unsigned direct Windows installers for this beta, with no paid signing service. Keep updater signatures/checksums and clear publisher-warning wording. Store signing remains separate; publication is still unapproved. |
| B25 | Public source/feedback destination | Prefer the approved public LocalSR source repository's Issues after source review, so code and feedback share one destination | Public Issues/contact are agreed; exact repository URL/activation remains unconfirmed. |
| B26 | Download operations and support | Keep the Mac mini as preferred host; verify public HTTPS, upload capacity and availability after deployment approval. Managed hosting is a contingency. Support cadence and retention remain separate proposals | User reaffirmed the Mac mini hosting preference. Managed hosting is only a contingency; no hosting purchase or deployment is approved. Public hostname/reachability, uptime responsibility and support retention remain open. |

Public trust guidance: [Tauri Windows signing](https://v2.tauri.app/distribute/sign/windows/).
A newly signed installer can still receive a SmartScreen warning. The temporary
native acceptance certificate must not be distributed as public trust.

## Windows vendor and delivery clarification

AMD, Intel and NVIDIA remain in the Windows scope. DirectML supports compatible
adapters from all three vendors; NVIDIA also has the retained CUDA edition.
Only Intel UHD 620 has native Windows acceptance evidence. The direct DirectML
NSIS installer must be part of the website/GitHub beta, alongside direct CPU/CUDA;
the separately compiled Store MSIX follows later. These are distribution variants
of the existing backend targets, not three newly validated vendor-specific runtimes.
SeedVR2 remains unavailable through DirectML.

The user chose no paid direct Windows signing for now. This does not disable
production updater signatures, change Apple signing or remove Microsoft Store
signing. The explicit build/deployment/publication pause remains in force.

[DirectML hardware requirements](https://onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider.html) ·
[Microsoft Store MSIX signing](https://learn.microsoft.com/en-us/windows/msix/package/sign-msix-package-guide#production-microsoft-store-distribution).

## Next public-project decision

Read-only GitHub checks found `HerRei/local-upscale` private, with Issues enabled.
`gh repo view HerRei/LocalSR` did not resolve an accessible repository; availability
must be rechecked before any later creation. Recommendation: a separate public
`HerRei/LocalSR` repository for the reviewed source snapshot, release records and
GitHub Issues. Keep the existing private repository and active `.12` unchanged.
This name/destination is a proposal only; no repository was created or published.
