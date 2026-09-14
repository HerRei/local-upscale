# LocalSR beta — independent preparation handoff

13 September 2026. **Public release is not ready; beta package builds are paused.**
This is source, evidence and rollout preparation for review. No new native
package, public release, Store submission or release server was started. `.12`
checkouts/jobs/runners/caches and this Mac's GUI remain protected. The Mac mini
will not be polled or scheduled; the Intel Windows PC remains acceptance-only.

**Decisions updated after the archived handoff:** the Mac mini remains the
preferred host, and direct Windows installers will be unsigned for this beta
with no paid signing service. Keep signed direct updates and Store-managed MSIX
updates. DirectML needs a direct installer at website/GitHub launch for compatible
AMD/Intel/NVIDIA hardware, alongside CPU and NVIDIA CUDA editions. Only UHD 620
has native Windows GPU acceptance evidence. The archives below preserve the
earlier snapshot; these working documents and the decision register are current.
The user subsequently chose further bounded SIDD investigation, deferred until
later. No CPU restriction or release waiver is approved; the remaining decisions
can proceed while this investigation and all package builds remain paused.

## Latest source addition

[Legacy video](beta-legacy-video-2026-09-13.md): common older containers, AAC
conversion, tagged deinterlacing, display-aspect normalization and cancellable
playback copies. This postdates the archived review snapshot and must enter the
next authorized build and installed acceptance. No package-ready claim is made.

## Current work and its evidence

| Work | Result and scope |
| --- | --- |
| Windows replacement runtime | Implemented Torch 2.13 CPU / ONNX Runtime DirectML 1.24.4 path, verified DXGI selection and actual GPU node execution, bounded sessions, cancellation and allocation recovery. Source worker only; no new MSIX. |
| CPU/iGPU benchmark correction | v2.1 CPU **1.16 MP/s**, CV **1.82%**; UHD 620 **0.86 MP/s**, CV **3.97%**. Both pass the fixed consistency gate; large scene has three measured repetitions. Scores are separate and cannot be compared with old v2 scores. |
| Images/video/live progress | Larger 769 × 513 → 3076 × 2052 CPU/GPU image tests, odd alpha/Unicode paths, sixteen-frame MOV/audio export, real tile boxes/JPEGs/frame IDs and ETA pass. |
| Cancellation/pressure | Active cancellation after a tile completed in 0.125 s with cleanup and same-worker recovery. A real per-process memory limit forced allocation failure safely; removing it allowed recovery without putting the whole PC under pressure. |
| Model numerical checks | 45/45 small probes on nine models; HAT-S passes all fifteen larger-matrix cases. Three unstable SPAN checker cases rejected on both CPU/GPU. **Three NAFNet SIDD photo comparisons still fail tolerance.** No CPU restriction approved or implemented. |
| Custom checkpoints | Safetensors import, corrupt file/unverified pickle rejection and same-worker recovery pass on Mac CPU/MPS and native Windows CPU/DirectML source workers. |
| Long export | Native Intel SPAN export: five minutes, 160 × 90 → 640 × 360 at 12 fps; all 3,600 frames decode, timestamps increase and audio is 300.011 s. Processing took 30.84 minutes; peak worker-tree RSS 563.93 MiB, settled spread 10.19 MiB. Earlier Linux 9,000-frame and bounded six-frame 4K→8K evidence remains preserved. No full four-minute 4K claim. |
| Website/download code | Eight retained editions, honest pending downloads, accurate hardware/Labs notes and contact; desktop/mobile and broken/placeholder-inventory tests pass. No working download link is invented. |
| Hosting | Loopback Caddy passes a range beyond 2 GiB, 64 MiB interrupted/resumed transfer with matching hash, four concurrent downloads, cache/conditional requests and process restart. No public HTTPS, WAN-speed, host-reboot or uptime claim. |
| Dependencies/licensing | Hashed Windows lock, pip check and 56-distribution OSV review; ONNX/provider notices prepared. Exact codec build inputs plus verified FFmpeg/x265 archives and pinned x264 source tree retained. Full native redistribution/source closure remains a release gate. |

Detailed evidence:

- [Runtime findings and SIDD exception](windows-inference-runtime-review.md)
- [Native source-worker reports](../build/beta-review/windows-runtime-native/)
- [Separate benchmark measurements](../build/beta-review/windows-runtime-native/native-benchmarks-summary.json)
- [Five-minute export and memory observations](../build/beta-review/windows-runtime-native/native-long-video.json)
  and [native custom-model acceptance](../build/beta-review/windows-runtime-native/native-custom-models.json)
- [Earlier installed packages and upgrade evidence](beta-acceptance-2026-09-12.md)
- [Current source tests and evidence inventory](../build/beta-review/rollout-20260913/verification-summary.json)

## Review materials

- [Review bundle](../build/beta-review/rollout-20260913/LocalSR-beta-independent-review.zip),
  [source snapshot](../build/beta-review/rollout-20260913/LocalSR_0.0.13-beta.1_independent-source-review.tar.gz)
  and [their SHA-256 checksums](../build/beta-review/rollout-20260913/REVIEW-ARCHIVE-SHA256SUMS).
  These are documents/source, not installable packages.
- [Edited website source](../build/beta-review/website-source/localsr/beta/index.html),
  [desktop capture](../build/beta-review/rollout-20260913/beta-1440.png),
  [mobile capture](../build/beta-review/rollout-20260913/beta-390.png)
- [Local GitHub release draft](../build/beta-review/rollout-20260913/github-release-draft.json)
  and [release notes](releases/v0.0.13-beta.1.md)
- [Current download inventory](../build/beta-review/rollout-decisions-20260913/downloads.review.json)
  and [retained artifact checksums](../build/beta-review/rollout-20260913/REVIEW-SHA256SUMS)
- [Store listing/reviewer draft](../build/beta-review/rollout-20260913/store-final-draft/),
  [preserved actual 1.0.7.0 screenshots](../build/beta-review/store/screenshots.json),
  [public issue-tracker preparation](../build/beta-review/public-tracker/README.md)
- [Hosting configuration, costs and external test plan](beta-hosting-plan.md),
  [local hosting test report](../build/beta-review/rollout-20260913/hosting-test.json)
- [Dependency/license review](beta-dependency-review.md),
  [model policy evidence](beta-model-license-choices.md)
- [Windows build and installed acceptance procedure](windows-beta-build-handoff.md)
- [Separate beta checklist](beta-release-checklist.md) and
  [machine-readable readiness register](../ci/public-beta-readiness.json)

The eight reviewed files were rehashed from actual retained artifacts. They are
older binaries, explicitly marked for rebuilding. Separate Windows CPU/CUDA
packages have no invented hashes or sizes. Final download/update manifests remain
inactive. Existing production signatures/download-tamper evidence is retained;
the new source archive is not a signed installed application.

Current automated verification: **603 Python passes, three skips, no failures**;
**110 frontend passes**, Svelte reports no errors/warnings, frontend build and
Ruff pass. The inventory test also passes after the final metadata change.
The skips and warnings remain in the retained log. Existing Rust results predate
this phase; no native package compilation was started during the pause.

The Windows five-minute fixture is deliberately small enough for a bounded
native acceptance run. Every exported frame was decoded; three sampled previews
were inspected. Worker socket snapshots found no remote connections, but this
is not a packet capture or a privacy audit of an installed package. Local hosting
throughput is also not an internet download-speed estimate.

[Cleanup evidence](../build/beta-review/rollout-20260913/cleanup.json) records the
owned scratch files removed after preserving logs, samples and source provenance.
The successful MSIX 1.0.7.0 installation and earlier upgrade/recovery evidence
remain intact.

## What the deferred build specifically blocks

- Frozen ONNX/provider DLL/notice inspection and installed behavior of the new
  Windows worker; final direct CPU/CUDA packages and MSIX successor to 1.0.7.0.
- Installed GUI playback/seek and A → running B → A switching, sliders, unseen
  running queue items, denoise/upscale tiles, current/next/whole-queue ETA and
  external Open/Reveal on that package. Source events and old installed tests
  are useful evidence, not substitutes.
- Final macOS signing/notarization/stapling and native upgrade recovery; final
  Linux package/installed upgrade checks. The old accepted hashes stay intact.
- Actual production-signed native installation/restart/failed-startup recovery
  with the final artifacts, preserving settings, recipes, model hashes and the
  logical queue. Verified portable rollback does not certify every native installer.
- Final Windows WACK. The earlier result remains **WARNING**, including the
  documented DPI-inspection COM error; it is not Microsoft certification.
- Fresh Store screenshots, immutable final checksums/sizes and active feed entries.

These differ from decision/operational gates below; a new build alone does not
clear licensing, hosting, trust or publication approval.

## Decisions together, with recommendations

| Choice | Recommendation | Current state |
| --- | --- | --- |
| NAFNet SIDD on new Windows GPU path | Investigate the small numerical discrepancy and practical CPU/GPU speed before recommending a restriction. | Investigation selected but deferred by user; not started or scheduled. Final release treatment remains unresolved; no CPU restriction/fallback implemented. |
| Codec and GPU redistribution | Finish review of the actual combined components and corresponding sources before public binaries; rebuild incompatible codec arrangements while preserving functionality. Obtain focused licensing advice if the published terms do not clear the combination. | Public-source approach approved; blanket redistribution clearance is not established. No model or feature removed. |
| Large downloads | Keep the Mac mini as the preferred host; verify WAN/HTTPS/reboot behavior before publishing. Managed hosting is a contingency. | Preference reaffirmed. Public hostname, reachability, upload rate, storage/backup and availability remain to verify; deployment is not approved. |
| Direct Windows trust | Unsigned direct installers with clear download wording; retain checksums and signed updates. | User confirmed no paid Windows signing for this beta. Store signing remains separate; final publication still needs approval. |
| Version and public destinations | Confirm `0.0.13-beta.1`; use the approved public source repository's Issues to keep code and feedback together. | Version and exact public repository/URL still unconfirmed; contact is **hermes.reisner@gmail.com**. |
| Support/retention | No guaranteed update cadence; propose private support deletion 90 days after resolution except records that must be retained. | Proposal only; user agreement needed before policy publication. |

## Personal actions

1. Send the explicit build go-ahead when `.12` has concluded; elapsed time does
   not grant it. Make the choices above before final publication review.
2. Complete any account-holder/trader declarations and required authentication
   personally. Existing Apple Keychain signing/notarization credentials work;
   no new certificate is requested now.
3. Choose an independent protected backup location for the production updater
   key. No paid Windows signing identity is needed for the chosen unsigned route.
4. Keep the authorized native Windows desktop available/unlocked when installed
   GUI acceptance needs it. The Mac mini is for builds; Intel remains for tests.
5. Review the final packages/pages before approving website/GitHub publication
   and the later Store submission.

## Exact next sequence after the go-ahead

1. Inspect the selected VM once for current activity/capacity. Create isolated
   source, dependency caches and output directories; do not reuse the runner.
   Resolve any still-open source/runtime decisions required for the candidate.
2. Build from one recorded source snapshot: Windows in the Mac mini Windows VM,
   with the other retained platform packages built only in authorized isolated
   environments. Preserve all historical acceptance artifacts.
3. Inventory actual dependencies/notices; sign/notarize/staple final macOS
   artifacts and sign direct updates. Apply the agreed Windows trust route.
4. Transfer the Windows candidate to the native Intel PC; finish installed GUI,
   separate benchmarks, cancellation, upgrade/recovery and WACK. Complete the
   final Linux/macOS installed update checks on their isolated test machines.
5. Produce fresh Store captures, final hashes/download/update manifests and a
   concrete package review. Report any failed gate instead of labelling it ready.
6. After your package/publication approval, activate and externally verify the
   approved hosting, privacy/support/tracker pages and website/GitHub beta.
7. Submit the reviewed Microsoft Store package/listing afterwards. Store
   application/engine updates remain managed by Microsoft Store.

No build or publication is scheduled by this handoff.
