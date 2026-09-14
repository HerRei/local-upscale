# LocalSR public beta checklist

Updated 13 September 2026. Candidate **0.0.13-beta.1 is provisional**, awaiting
user confirmation. This checklist follows the current isolated beta work and
[recorded acceptance](beta-acceptance-2026-09-12.md), not the old alpha gate count.
The separate machine-readable records are [the beta plan](../ci/public-beta-release.json)
and [readiness register](../ci/public-beta-readiness.json).

**Build pause:** no beta packages on any host until the user explicitly says
“.12 has concluded; you may build the beta.” Do not poll, wait on a timer or
schedule a build. The existing Mac mini Windows VM is selected for later isolated
builds; native Intel remains acceptance-only. Website/GitHub publication precedes
Store submission, following final package review.

The active `.12` checkout, jobs and runners remain untouched. This Mac's GUI is
not used for testing. Native Windows and DDP GUI acceptance use isolated work.
No public release, Store submission, repository visibility change or message
to a model publisher has been sent. Private scan fixtures are excluded from
release screenshots and review downloads.

## Legacy-video addition — 13 September

- [x] Extend file/folder imports and associations for common legacy containers.
- [x] Add AAC conversion, tagged deinterlacing and pixel aspect normalization.
- [x] Add cancellable playback conversion, request ownership and bounded storage.
- [x] Run source codec/timing, switching and recovery checks; retain
  [evidence and scope](beta-legacy-video-2026-09-13.md).
- [ ] After build authorization, verify the new installed packages' AVI/WMV/MPEG
  import/export, audio, playback/seek, result switching, cancellation and restart.
  Previous installed packages do not contain this addition. Include OGV coverage.

## Decisions with the user

- [x] Audience: friends and voluntary Reddit testers; limited maintenance time
  during university, with the full agreed scope retained.
- [x] Retain all eight backend targets. Main test focus: macOS MPS, Linux ROCm,
  Windows CPU/Intel iGPU; other hardware paths are Labs with honest coverage.
- [x] Image processing and SDR video are core. HDR preservation, SeedVR2 3B
  FP16/FP8, de-flicker and video-face processing keep individual Labs limits.
- [x] Contact: **hermes.reisner@gmail.com**. Public GitHub Issues for bug reports.
- [x] Personal publisher **Hermes Reisner**, project branding **HerRei**, for a
  hobby beta outside business activity; confirmed 13 September 2026. Any required
  account-holder/trader declarations still need their own assessment.
- [ ] Confirm provisional version **0.0.13-beta.1** and Store package numbering.
- [x] Direct Windows installers remain unsigned for this beta, as requested;
  no paid code-signing purchase. Retain updater signatures and disclose Windows
  publisher warnings. The separate Store MSIX remains Store-signed.
- [ ] Build and accept direct CPU/DirectML/CUDA installers after the explicit
  go-ahead; DirectML must be available at website/GitHub launch, before Store.
- [x] Approve and implement the corrected RealPLKSR download policy: the author's
  explicit CC BY 4.0 declarations, intended application downloads and checkpoint
  provenance support verified downloads with accurate attribution. User approval
  and targeted source tests recorded on 13 September. Face-fork imports remain
  separate; final native package rebuilding/acceptance is still required.
- [x] Approve public release source and required exact dependency sources/build
  instructions, retaining the application's MIT notice. Approved 13 September,
  conditional on source/README cleanup and respecting upstream terms.
- [x] Clean and check the source/READMEs, preserve upstream notices and publish
  the separately authorized GitHub profile README. [Review and tests](source-release-review.md).
- [ ] Complete corresponding-source delivery and verify the actual bundled
  components' compatibility. Source publication alone does not settle this.
- [x] Approve a Windows ML/ONNX Runtime prototype to replace the old torch-directml
  dependency while retaining Intel acceleration (B20, 13 September).
- [x] Export all twelve catalog models and run native CPU/Intel GPU probes.
  The later native matrix includes five patterns/three sizes for HAT-S, SPAN
  and SIDD, plus 45/45 probes for the other nine models. SPAN instability is
  detected on both paths. Three SIDD photo comparisons remain outside tolerance.
- [x] Integrate and test the replacement source worker: separate CPU/iGPU v2.1
  benchmarks, larger tiles/ETA, short video/audio, cancellation and real allocation
  pressure/recovery pass. [Runtime review](windows-inference-runtime-review.md).
- [x] Complete the bounded native Intel five-minute export: 3,600/3,600 frames
  fully decoded, increasing timestamps, matching audio and stable memory.
  This uses 160 × 90 input, not a full-length 4K workload. Custom Safetensors,
  rejection and same-worker recovery also pass on Windows CPU/iGPU and Mac CPU/MPS.
- [x] Choose further bounded NAFNet SIDD investigation before a restriction.
  The user deferred this investigation to a later point; it is not scheduled.
- [ ] Complete that investigation and settle the final SIDD GPU treatment.
  No temporary CPU restriction or acceptance of the discrepancy for release was
  approved. New installed acceptance requires the deferred package.
- [x] Retire Slint separately; retain Qt/CLI/engine and verify the Tauri launcher
  and migrated tests. Approved under B22; 595 Python, 110 frontend and 67 Rust
  checks pass. [Cleanup and migration record](slint-retirement.md).
- [x] Choose the Mac mini's existing Windows x64 VM for separate beta builds
  after explicit authorization. No availability polling or timed restart. Its
  jobs, checkouts, caches and runners remain protected.
- [x] Prepare the dedicated release-directory/Caddy/service draft locally; test
  large ranges, resumption, concurrency, cache behavior and process restart.
- [x] Keep the Mac mini as the preferred release host; managed hosting is a
  contingency, not a required purchase.
- [ ] Choose the public download hostname and confirm hosting reachability; test public HTTPS/WAN,
  reboot and uptime only after explicit deployment approval. [Hosting plan](beta-hosting-plan.md).
- [ ] Arrange independent protected backup of the production updater key.
- [ ] Confirm support-message retention and maintenance wording; recommend no
  promised update cadence and one public feedback channel.
- [ ] Approve actual artifacts/content before public downloads, tracker activation,
  website deployment or Store certification submission.

Details and prior approvals: [decision log](beta-release-decisions.md).

## Reported bugs and targeted acceptance

The [bug-by-bug status](beta-bug-status.md) distinguishes verified interface
fixes from remaining restoration-quality limits and final-package checks.

- [x] Correct tile outlines for denoise, NomosWebPhoto and HAT, including partial
  edges, switching to an unseen running image and multi-stage jobs.
- [x] Correct benchmark rendering squares and keep separate CPU/iGPU scores.
  Native Windows CPU and UHD 620 runs completed; both survived a Windows Update reboot.
- [x] Keep the completed-image comparison responsive during another queued job;
  trusted-mouse slider movement passed on installed Windows.
- [x] Video A completed → start B → return to A → play both sides passed on
  native Windows and DDP. Actual player ownership and synchronization were checked.
- [x] Folder jobs export to `LocalSR Results`; native Windows folder selection
  and grouped exports passed. DDP's configured-folder path was checked.
- [x] Show current-job, next-job and whole-queue ETA, with unmeasured work identified.
- [x] Lock incompatible controls during processing and cancellation.
- [x] Forced stalled-worker cancellation/restart followed by another successful
  job passed on DDP and native Windows.
- [x] Reproduce scan corruption across CPU/ROCm; apply bounded context retries
  and instability guards before saving. Nine of eleven denoise inputs succeed;
  two are rejected clearly with no output. Full-quality claims remain limited.
- [x] Signed native macOS worker passed six real MPS cases: corrupt-file rejection,
  recovery, transparent image/unicode paths, video/audio/tiles/ETA, cancellation
  cleanup and a subsequent successful video. The signed host used its bundled worker.
- [x] Final CPU/ROCm frozen workers passed six image/video/recovery cases each.
  The exact ROCm AppImage passed completed-video switching while a second job ran.
- [ ] Finish DDP native folder-dialog observation and the remaining native
  installed upgrade checks; reconcile binaries with the final reviewed source.

Earlier automated checks: 619 Python tests passed / 3 skipped on the Mac
headlessly; 581 / 41 on Windows; 105 frontend and 65 Rust tests passed. These
counts precede the separate beta metadata and final packaging changes. They
are not substitutes for installed-package acceptance.

## Windows package and Store

- [x] Reserved identity recorded: `HerRei.LocalSR`, Store ID `9NTG848ZQTCQ`.
- [x] User's Partner Center screenshot shows pricing, properties and age ratings
  complete; Store package/listing/submission remain drafts.
- [x] Native CPU/DirectML MSIX built with the bundled worker, integrity-checked
  models and Store-managed application updates. No restoration weights bundled.
- [x] Fresh installation and 1.0.0.0 → 1 → 2 → 3 → 4 → 5 → 6 → 7 upgrades preserve
  settings, recipes, model hashes and logical queue contents; backups retained.
- [x] Native Windows 11 / Intel UHD 620 driver 24.20.100.6286 passed real HAT-S,
  SPAN, short video, cancellation/recovery, comparisons and both benchmarks.
- [x] Full WACK executed on 1.0.5.0. Overall **WARNING**: required DPI warning
  and optional blocked-executable findings; other reported tests passed.
- [x] Add per-monitor DPI declarations and use native ShellExecute for core Open actions.
- [x] Build/install 1.0.6.0; settings, recipes, model hashes, queue and recovery backup verified.
- [x] Repeat full WACK on 1.0.6.0; report retained. It still reports **WARNING**
  for host DPI processing and optional blocked-executable findings.
- [x] Build/install 1.0.7.0 with a validated embedded assembly identity and DPI manifest;
  settings, recipes, model hashes, logical queue and recovery backup survived.
- [x] Repeat full WACK on 1.0.7.0 and retain native DPI evidence. Overall **WARNING**:
  the kit's DPI inspection reports COM E_FAIL, while the actual window is
  PerMonitorV2 at 120 DPI. Optional blocked-executable findings remain.
- [ ] Complete final installed Open/Reveal and remaining GUI acceptance.
- [x] Uninstall/reinstall 1.0.7.0 and verify explicit backup restoration of the
  entire profile. Windows deleted the profile during uninstall: this is not
  automatic retention. Accepted upgrade evidence and the recovery backup remain.
- [ ] Finish runtime/dependency policy and rebuild acceptance after those changes.
- [x] Capture four actual 1.0.7.0 Windows screenshots: image comparison, video
  comparison, real HAT-S video tiles/ETA, and separate saved CPU/iGPU scores.
  Public NASA imagery replaces private acceptance scans; captions/listing need final review.
- [x] Prepare copyable English listing, reviewer instructions, captions, sample
  media, privacy/support pages and exact package hashes in `build/beta-review/`.
- [ ] Confirm publisher/content choices and deploy/test the final public URLs.
- [ ] User review → upload MSIX → certification → Store publication. Microsoft's
  signing applies after certification; the temporary acceptance certificate is not public trust.

[Store details](microsoft-store.md) · [Listing/reviewer materials](beta-store-submission.md).

## macOS and Linux packages

- [x] Existing Apple Developer ID identity and matching private key verified in
  Keychain; team `Z2TU844D84`. Notarization profile authenticated without key export.
- [x] Maintained native Apple Silicon worker built with Torch 2.13; macOS 14+ floor.
- [x] Sign all 317 native bundle files and the outer app inside out with hardened
  runtime/timestamps; strict verification and real MPS acceptance passed.
- [x] Apple accepted both app and DMG notarization submissions. Both are stapled;
  strict signature and Gatekeeper checks pass. The mounted DMG's app starts its
  bundled worker successfully. This Mac's GUI was not used.
- [ ] Verify native macOS upgrade/data retention; document any recovery limit.
- [x] Build CPU AppImage on Ubuntu 24.04; bundled host/worker and CPU runtime smoke passed.
- [x] Diagnose DDP's packaged EGL failure: conflicting bundled Wayland libraries.
  The corrected CPU AppImage launches and plays both video-comparison streams.
  A compiled packaging-wrapper regression test verifies the library exclusions.
- [x] Verify the source-preview → beta AppImage profile backup and preservation
  of settings, recipes, twelve model hashes and logical media/queue rows.
- [x] Build all four retained Linux AppImages on the Ubuntu 24.04 baseline,
  with the multimedia framework needed for playback. All four final archives
  exclude the conflicting Wayland libraries and pass native ELF/host inspection.
- [x] Final CPU/ROCm AppImages pass host startup, real inference, cancellation
  cleanup and subsequent recovery. CPU/ROCm native video comparisons play;
  the final ROCm package preserves the selected completed result during another job.
- [x] CUDA/XPU archives pass bundled-host handshake, clean shutdown and all-ELF
  architecture inspection. Physical CUDA/XPU inference remains explicitly untested.
- [ ] Finish DDP native folder selection and signed native AppImage upgrade testing.
- [ ] Complete Windows CPU/DirectML/CUDA direct-edition package preparation without
  repurposing the native Intel acceptance PC as a build runner.

## Video, resource limits and dependencies

- [x] Full five-minute SPAN ROCm export: 480×854 → 960×1708, 9,000 frames,
  approximately 100 minutes. Full frame/audio decode and increasing timestamps passed.
- [x] Native DDP and Windows result-switching sequence passed during a second job.
- [x] Known SeedVR2 OOM and NAFNet context-memory limits produce bounded errors;
  do not advertise 16 GB as a guarantee for any resolution or duration.
- [x] Final ROCm worker exports six 4K frames to 8K with full frame/audio decode,
  increasing timestamps, tiles/ETA and bounded memory observations. A forced
  per-process file-size failure leaves no output; the same worker recovers.
  This is not a complete four-minute 4K export.
- [x] Inspect actual dependency advisories, FFmpeg configurations and bundled terms.
  Record the DirectML Torch 2.4.1 and GTK3 risks without claiming blanket safety.
- [ ] Resolve distribution policy; finish worker rebuilds and corresponding-source
  delivery. Routine setuptools/wheel updates are prepared in the beta source.
- [x] Preserve supported model/backend restrictions and explicit SDR/HDR output labels.
- [x] Keep restoration quality, broad HDR-model validation and untested GPU hardware
  as specific Labs limitations, without weakening core package/data requirements.

## Updates, distribution content and cleanup

- [x] Production updater key stored in Keychain; public key retained in the repo.
  Correct signatures pass the runtime-equivalent verifier; altered content fails.
- [x] Earlier disposable-key Linux update/restart/rollback preserves user data.
- [x] Production-sign and verify four AppImages and the stapled macOS app archive.
  The application's downloader accepts the actual signed CPU candidate and rejects
  truncation and substituted bytes even with a recomputed checksum.
- [x] Assemble backend/channel contracts and exact checksums. Draft feed URLs are
  deliberately non-deployable until hosting and publication are approved.
- [ ] Exercise native production-key install/restart/recovery on the final binary
  set. Existing disposable-key rollback and low-disk tests do not establish this.
- [x] Keep application updates in the Store edition managed by Microsoft Store.
- [x] Prepare privacy/support/download pages and public issue-tracker contents,
  including the confirmed contact, capable-hardware warning and slow-video examples.
- [x] Assemble release notes, checksums, download/update manifests, installation
  instructions, actual Store screenshots and acceptance evidence for user review.
- [x] Remove owned disposable build/test artifacts and temporary tasks/tunnels;
  retain reviewed packages, evidence, working acceptance installs and user originals.
  Corrected AppImages remain; obsolete failing variants, temporary extractions,
  the one-off reinstall task and test-only processes were removed.
- [ ] After user approval, publish and test public access; never publish private scans,
  unresolved checkpoints, credentials or false hardware/certification claims.


## Current independent handoff

The [13 September handoff](beta-handoff-2026-09-13.md) is the current review hub.
It separates source-worker tests, previous installed-package acceptance and gates
that cannot be completed until the next build. Historical checked items above do
not certify rebuilt packages. Download inventories contain only actual artifact
hashes/sizes; final links remain disabled. Store screenshots remain labelled
1.0.7.0 and must be refreshed for the changed benchmark/runtime.

Current checks: 603 Python passes / 3 skips / 0 failures, 110 frontend passes,
zero Svelte errors/warnings, frontend build and Ruff pass. Exact reports and
owned-artifact cleanup are linked from the handoff; no paused package build or
publication was started.
