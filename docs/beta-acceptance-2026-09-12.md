# Local beta candidate acceptance — 12 September 2026

Status: **in progress, not approved for publication**. This record continues the
earlier beta evidence. Work is isolated in `LocalSR-windows-beta`, branch
`codex/windows-store-candidate`; the active `.12` checkout, jobs and runners are
untouched. The Mac GUI is not used. Native Windows and DDP are the authorized
acceptance devices. No Store submission, public release or checkpoint upload
has been made.

## Evidence preserved

- Native Windows: Acer Swift SF514-52T, Windows 11 Home 26200 x64,
  i7-8550U, 15.89 GiB RAM, UHD 620 driver 24.20.100.6286 (2018-08-15).
- Frozen CPU/DirectML workers: six real acceptance cases per device, covering
  corrupt-video recovery, odd-sized RGBA/Unicode image export, trimmed video
  with audio/timestamps, live tiles/ETA, cancellation cleanup and a subsequent
  successful job.
- Real HAT-S DirectML: 32×48 → 128×192, finite output; CPU comparison mean
  absolute error 3.599e-7, maximum 4.05e-6. Actual device `privateuseone:0`;
  `aten::roll` used a reported CPU fallback. Packaged image and three-frame
  video also completed with tile/frame events.
- MSIX: fresh 1.0.0.0 installation, 1.0.0.0 → 1.0.1.0 and
  1.0.1.0 → 1.0.2.0 → 1.0.3.0 → 1.0.4.0 → 1.0.5.0 upgrades. Settings, the saved recipe, downloaded SPAN hash
  and logical queue/media data survived. Version backups contained settings,
  queue database and the previous data-version marker. The installed host
  launched successfully after upgrades.
- These packages use the confirmed Store identity `HerRei.LocalSR`. Local
  acceptance signing uses a temporary test certificate, not Microsoft Store
  signing. The unsigned `store-upload.msix` is retained separately. No
  restoration checkpoints are bundled; the two SeedVR2 runtime conditioning
  embeddings are individually allowlisted and hash pinned.
- Installed GUI completed a 129×97 RGBA image at 4× and a 24-frame 96×64 MOV
  at 4×. On 1.0.3.0 both video elements reached readyState 4, playback advanced
  with a measured 22 ms difference, and switching to an image and back loaded
  both videos without media errors. Longer exports and the final package
  still need acceptance.

Operator evidence is retained under the isolated machine's
`C:\LocalSR-Beta-20260912\reports`: `msix-install-1.json`,
`msix-upgrade.json`, `msix-upgrade-final.json`, installed-host reports and
before/after profile snapshots. Keep these historical results when creating
the next candidate; do not overwrite them with later runs.

## Regressions found and changes under acceptance

1. A full UHD 620 benchmark finished its large scene but emitted JSON
   `Infinity` for variability when only one measured repetition fit the time
   budget. The native host rejected its terminal event and remained busy.
   Unmeasured variability now serializes as `null`, with an explicit UI label;
   protocol serialization rejects non-finite numbers. Separate CPU/GPU scores
   are retained. Both installed device runs now complete successfully; see below.
2. The forced-cancellation watchdog previously covered video only. It now
   covers images and benchmarks, stops only the owned worker after the grace
   period, cleans owned temporary output, and restarts the worker. Encoding
   checks cancellation before publishing its atomic output. Existing outputs
   are preserved. Installed forced-cancellation and subsequent CPU restoration
   passed; exact timings are recorded below.
3. Opening an already-running image on a narrow edge tile could establish an
   incorrect checker grid. The grid now uses the actual worker tile size and
   full output geometry. Fast model outlines are not throttled with JPEG
   encoding; late JPEGs cannot rewind the active outline. Stage identifiers
   reject denoise frames arriving during a subsequent upscale stage.
4. Completed-image comparison is owned by the selected media/output, separate
   from the running job. Revisiting a completed image while another runs asks
   the worker for that output's bounded preview. Stale responses are rejected.
   Installed 1.0.3.0 testing found a second blocking cause: the worker's main
   message loop was occupied by inference, so preview requests waited until
   the current job finished. A bounded preview service now receives requests
   from the reader thread and decodes without touching the inference device.
   It retains at most one pending selected preview and one automatic preview.
   DDP loaded a selected completed result in 0.593 seconds while the same CPU
   inference continued. Installed Windows 1.0.4.0 loaded it in 0.283 seconds
   while HAT DirectML continued; trusted mouse input moved the slider to 82%
   with the correct clipping and media ownership. The failed 1.0.3.0 slider
   test is retained as the reproduced regression.
5. Add Folder now groups exports inside that folder's `LocalSR Results`
   directory. Filename collision handling remains in place.
6. The benchmark preview had the same delayed-JPEG outline problem. A failing
   regression test reproduced an outline stuck on the prior tile during decode;
   another reproduced a missing grid when opening on a narrow edge. Position
   updates now run independently of JPEG painting, and the regular grid uses
   the reported source tile size × the benchmark model's fixed 4× scale. Both
   new tests pass. DDP's rebuilt GUI completed a real CPU benchmark: 3,440
   outline samples across 106 positions, no missing outlines or coordinate
   mismatches. Its 562.36-second run overlapped the long ROCm video test, so
   the score is a contended measurement, not an isolated performance baseline.
   It returned to ready and correctly labelled unmeasured consistency.
   Windows package 1.0.5.0 completed both separate runs: CPU took 428.85 seconds
   with 2,841 outline samples; Intel UHD 620 DirectML took 782.17 seconds with
   5,457 samples. Each covered 106 positions with no missing outlines or
   coordinate mismatches. The independently retained scores were 0.74 and
   0.33 output megapixels/second. Both remained present after a Windows Update
   restart. Consistency is explicitly unmeasured, so these are functional
   benchmark checks, not stable cross-machine performance baselines.
7. Queue timing shows current, next and whole-queue estimates. It uses saved
   job configurations, observed timing and workload dimensions. Unknown
   configurations are explicitly unmeasured. All pending jobs remain visible,
   including queues larger than the historical 200-job query limit.

Mac headless full Python run after these changes: **619 passed, 3 skipped**.
The first two attempts exposed a Qt object-lifetime crash in older tests;
registering their windows with `qtbot` fixed teardown without skipping tests.
Frontend: **105 passed**, Svelte zero errors/warnings, production build passed.
Native Rust: **65 passed** with the Store feature. These counts include the
periodic-pattern guard and asynchronous result-preview changes. Real model and
installed package validation remain separate requirements.

The latest completed native Windows Python run was **581 passed, 41 skipped**
in 209.51 seconds, including the final guard and asynchronous preview changes.
Skipped hardware/FFmpeg fixtures are not evidence of passing those paths.

## Real batch and tile checks — 13 September

- DDP HAT: a 1982×1361 source produced 7928×5444 output. First selection of the
  running second image happened on an edge tile at x=7168, width=760, while
  the regular output grid remained 1024 pixels. All 1,526 sampled outlines
  agreed with the worker coordinates; 19 distinct positions were observed.
- Native Windows HAT: 382×257 → 1528×1028, first selection at the right edge
  x=1024, width=504, regular output tile 512. There were no mismatches in
  3,501 outline samples over eight distinct positions.
- DDP NAFNet: first selection at x=1792, width=190, regular grid 256;
  155 samples over 88 positions had no mismatches. A separate three-image
  NAFNet → NomosWeb 2× queue completed both stages, with no mismatches in
  368 sampled outlines. These scans are private acceptance material.
- The native Windows Add Folder dialog selected the isolated fixture folder,
  enabled batch mode, and saved four completed HAT outputs inside its
  `LocalSR Results` subdirectory. Current, next-item and whole-queue estimates
  appeared on both DDP and Windows; unmeasured work is labelled accordingly.
- DDP ordinary image cancellation returned to ready in 0.509 seconds. A
  separately suspended owned worker was stopped by the watchdog and the app
  returned to ready in 10.572 seconds, then completed another NomosWeb job.
  The installed Windows 1.0.4.0 worker was separately suspended through its
  exact owned process handle; Cancel returned to ready in 13.908 seconds.
  A subsequent full Page 01 CPU NAFNet → NomosWeb job completed, establishing
  recovery inference after the worker restart.
- Installed UHD 620 NAFNet Large processing of the full Page 01 scan reached
  the bounded context retry, then failed with an explicit insufficient-memory
  explanation and no exported output. This model/workload is not counted as a
  successful iGPU case. Windows CPU completed both stages in 1,469.23 seconds
  and exported a valid 3964×2722 PNG. All 15,000 sampled outlines agreed with
  the worker coordinates across 74 observed positions and both stages. The
  sample cap covers only part of this long run; completion is recorded by its
  separate terminal event. Other validation and compilation overlapped this
  run, so its elapsed time is not an isolated performance benchmark.

Raw coordinate samples, UI screenshots and machine reports are retained in
the ignored `build/document-artifacts` and `build/windows-candidate`
directories. Sampling establishes the observed positions, not every possible
model or display geometry. The Mac GUI remains unused; its UI checks are
headless.

## Scanned-photo investigation

The user's private DDP scans are used only in disposable local acceptance;
they must never become Store screenshots, public fixtures or model assets.
The original files and previous results are preserved.

The installed NAFNet SIDD width64 checkpoint matched its catalog hash
`cd685efaae01f7c4e9951f2deab05780079c8eb1e49ed664b72f6db04dabb445`.
Raw network values on a failing edge crop reached approximately −30 to +36
before Spandrel clipped them into coloured blocks. CPU reproduced the AMD
output nearly exactly. Increasing halo or lowering precision was not a fix.
Larger aligned input context recovered several failing regions with the same
checkpoint. The NomosWeb upscale can amplify an already damaged denoise input.

The candidate detects unstable NAFNet output before range clipping and retries
with bounded larger context. If recovery fails or exceeds memory, the job fails
with a clear explanation and does not publish an output. Range checks alone
missed a smaller colour pattern during visual inspection; the guard now also
checks large signal and chroma changes and newly introduced small periodic
patterns relative to the source. The periodic check removed a visible colour
grid in Page 10 that the earlier checks missed. CPU reruns of Pages 01, 09 and
10 completed with the final guard. A complete final-guard denoise run again
completed nine of eleven scans and safely rejected Pages 02 and 03 without
publishing output. The preceding full CPU two-step run also completed nine
scans. Final-guard MPS two-step runs of Pages 01 and 10 completed with real
tile/ETA events and outputs of 3964×2722 and 4860×3316 respectively. Real
Windows CPU two-step validation completed. Its Page 01 output was visually
inspected and closely matched the MPS output (mean absolute difference 0.00098
on the 0–255 scale; maximum 5). The final MPS Page 10 output was also inspected
against its source; the central dotted light trail is present in the original.
Eight of the nine successful final CPU denoise outputs were pixel-identical to
the preceding run, allowing their existing NomosWeb outputs to be retained.
Page 10's changed denoise output received a fresh CPU NomosWeb run, producing
a 4860×3316 PNG in 253.56 seconds.
Do not describe all scanned-photo quality as fixed based only on finite tensors
or the absence of extremely changed pixels. This is a model-domain limitation
as well as an application error-handling issue; no replacement model has been
silently selected.

## Video and resource acceptance — 13 September

The DDP source-worker run processed the complete five-minute portrait SDR clip
at 480×854 and 30 fps through SPAN on ROCm at requested 2× scale. It exported
a 960×1708 H.264 MP4 in 5,965.86 seconds (about 99.5 minutes). Full sequential
decoding recovered all **9,000 frames** with strictly increasing timestamps,
ending at 299.9667 seconds. The AAC audio duration was 299.968 seconds. Peak
worker RSS was 2,067,816,448 bytes; the lowest observed available system memory
was 13,446,115,328 bytes. Real preview and positive ETA events continued during
processing. Sampled exported frames were visually inspected. This verifies a
complete long export at that workload; it is not evidence of a full four-minute
4K export or of every backend's resource limits.

DDP also completed the exact sequence video A finished → start B → return to A
using two procedural two-second SDR MOVs. Both original/result players loaded,
retained A's URLs while B remained active, and advanced with 0.395 ms observed
time difference after returning. B subsequently completed. Both MP4 outputs
fully decoded to 24 frames at 384×256 with increasing timestamps and readable
AAC audio. The first observer's 300-second timeout is retained: B was still
processing and later completed after 472.16 seconds, rather than hanging.
This DDP GUI uses the isolated native host with a source worker; final Linux
package acceptance is still a separate requirement.

## Final Windows package checks — 13 September

Data-preserving upgrades continued through 1.0.6.0 and **1.0.7.0**. Settings,
recipes, installed model hashes and logical queue/media contents survived;
versioned recovery backups are retained. These upgrade snapshots precede the
later, intentional removal of private fixture rows from the isolated Windows
interface for public screenshot capture. Original fixture files and historical
evidence were not removed.

Package 1.0.7.0 has an embedded assembly identity and per-monitor DPI manifest
that passes the Windows SDK manifest validator. A native check of the live
window reports **PerMonitorV2 at 120 DPI**. Full WACK kit 10.0.26100.7705 still
returns overall **WARNING**: its required DPI inspection fails internally with
COM E_FAIL, and the optional blocked-executable test reports Python/Torch native
process APIs and strings. Other reported tests pass. The WACK XML, native DPI
result and prior unsuccessful checks remain retained. This is not an all-pass
WACK or Microsoft certification claim.

The earlier installed video-switch outputs each fully decode to 24 frames at
384×256 with increasing timestamps, ending at 1.916667 seconds, and AAC audio
of 2.005333 seconds. On 1.0.7.0, a public NASA Earth-photo fixture completed
HAT-S CPU 4× export, 384×384 → 1536×1536, in 138.891 seconds. An eight-frame
160×160 H.264 MOV made from the same credited image completed twice at 4×,
in 314.263 and 344.397 seconds. Actual screenshots record the completed image
comparison, video comparison, live edge tile/ETA and independently retained
CPU/UHD 620 benchmark results. Timings are observed acceptance workloads,
not controlled performance claims. No private scans appear in these assets.

## Linux AppImage compatibility and profile checks — 13 September

The Ubuntu 24.04 CPU package passed the bundled host/worker smoke test but
failed native WebKit startup on DDP with `EGL_BAD_PARAMETER`. An isolated
extraction reproduced the correction by removing the four bundled Wayland
client/cursor/EGL/server libraries, allowing the host Mesa stack to use its
matching versions. The packaging wrapper now excludes those libraries;
the compiled wrapper regression verifies the exclusion arguments and tool
restoration. All 15 build-script tests pass.

The repacked **493,681,144-byte CPU AppImage**, SHA-256
`a8f51ac51366fcef9a02e435f81f12e5166855babd85271453b1e3ad95fc6efa`,
launches on DDP and loads both original and enhanced video elements without
media errors. Playback advanced to 0.519939 and 0.458424 seconds in the sampled
observation. The beta startup preserved settings/recipes, twelve installed
model hashes and logical media/job rows, with a versioned backup before
changing the data-version marker from 0.0.12-alpha to 0.0.13-beta.1. This uses
the isolated acceptance profile; DDP's original app/profile stays unchanged.

Final native folder-picker acceptance is incomplete. The simulated X11 input
also failed to generate observed key events in a plain system GTK probe, so
that attempt does not establish a LocalSR-specific dialog regression. The
probe closed after its bounded timeout. Final package inference, cancellation,
resource-pressure and remaining native-dialog checks are still required.

## Final candidate package and recovery checks — 13 September

All four Linux review AppImages have been copied locally, hash-compared, signed
with the production updater key and verified. Their full native-library inventories
identify x86_64 host code separately from AMD device code. No foreign host architecture
was found. Each actual AppImage completed its bundled-host/worker protocol handshake
and clean shutdown. These checks do not establish physical NVIDIA or XPU inference.

The four conflicting Wayland libraries are absent from every corrected archive.
A further packaging reproduction established that the GStreamer deployment plugin
can reintroduce an excluded library. The compiled wrapper now deploys plugins,
removes only the excluded host-library paths, then packages without rerunning those
plugins. Both the compiled fake-plugin regression and an actual rootless Ubuntu
24.04 linuxdeploy/GStreamer build pass; the original tool is restored. The first
actual-plugin probe omitted the existing private-library linker setup and failed
on libtorch resolution; the corrected harness uses the real build preparation.

The exact final CPU and ROCm frozen workers each passed six cases: corrupt MOV
rejection and recovery, odd transparent image with Unicode paths, trimmed video
with audio/live tiles/ETA, cancellation cleanup and a subsequent successful video.
The final ROCm AppImage, SHA-256
`ea5172912dbface3f7698bbb4601b74b696bc4a02c04835e308b7bc37ce694da`,
then passed the native GUI sequence A completed → start B → return to A. Both
players retained A's media URLs, decoded with no media error and advanced to
0.778134 / 0.778150 seconds while B remained active. B subsequently completed.
This is final-AppImage evidence, separately retained from the earlier source-worker
sequence. DDP's original user app/profile was not replaced.

**Bounded 4K and resource pressure.** The final ROCm worker exported six procedural
3840×2160 SDR frames to 7680×4320 using SPAN 2× in 65.339 seconds. All six frames
fully decode with increasing timestamps, valid AAC audio and nonblack/varied pixels;
1,623 ETA events and real tile previews were observed. Peak worker RSS was
4,248,752,128 bytes; observed system memory available stayed above 20,416,163,840
bytes. A per-process 65,536-byte file-size limit then forced an encoder failure.
No completed or partial output remained. Restoring that limit allowed the same
worker to complete another image. This bounded test does not simulate a host-wide
OOM or fill the machine's disk, and is not a complete four-minute 4K export.
The earlier complete five-minute / 9,000-frame export remains valid separate evidence.

**macOS trust and mounted package.** The ARM64 app and DMG are both Developer ID
signed, notarized and stapled. Apple submissions
`04821633-8a94-4fbc-add5-4d6b6944b1f2` (app) and
`0139f0ff-5005-4081-b93a-0e84e14ae186` (DMG) are Accepted. Signature, staple and
Gatekeeper checks pass. The read-only mounted DMG contains the correct Applications
link and its app starts the bundled worker successfully. DMG SHA-256:
`c24f964a722ba5a35d7045bacf45881e28aea2d50eb94ca904418a01ab8f628a`.
The image was unmounted after verification. This Mac's GUI was not used.

**Windows uninstall and recovery.** The accepted 1.0.7.0 package was closed cleanly
while idle, and its whole isolated profile was backed up and verified. Uninstall
removed that profile, including settings. The same signed test MSIX was reinstalled;
explicit restoration from the verified backup preserved settings, recipes, model
hashes and logical queues. Its installed host/worker handshake passed, and the
reopened GUI showed the expected public fixtures and recipe. This is explicit
backup recovery, not automatic retention through Store uninstall. Historical
installation/upgrade evidence and the recovery backup are retained. WACK WARNING
and external Open/Reveal observation behind the locked desktop remain unresolved.

**Production updater.** The application's downloader accepted the actual signed
493,681,144-byte CPU AppImage. Truncated responses never became installable and
left no partial/final file. Substituted bytes failed Minisign verification even
with a recomputed SHA-256. The targeted production-key test passed; it contains
only the public key and an externally supplied candidate path. This does not
establish native installer restart/rollback. Earlier disposable-key portable
update/rollback evidence is preserved without relabelling it.

**Public review materials.** Four actual installed Windows screenshots use the
credited NASA Earth image, not private scans. Copyable English listing, features,
reviewer instructions and safe sample media are prepared. The isolated website
clone has beta, support and privacy pages plus a preview-page link. Six desktop/mobile
headless checks pass with HTTP 200, no missing images, no horizontal overflow and
no JavaScript errors. Tracker templates, release notes, checksums and download/update
manifests are assembled locally. No public page, tracker, release or Store submission
has been activated. Contact: **hermes.reisner@gmail.com**.

## Remaining release requirements

- External Windows Open/Reveal observation on the unlocked desktop; disposition
  of the retained WACK warnings during Store acceptance. Do not claim certification.
- DDP native folder-dialog observation. Input injection also failed in the plain
  GTK probe, so this remains an observation gap rather than a proven app failure.
- Native production-key install/restart/recovery, including installed macOS data
  preservation on a separate authorized Mac. Keep this Mac's GUI untouched.
- Separate Windows CPU/CUDA direct packages and trust/delivery on isolated build
  infrastructure; the native Intel PC remains acceptance-only.
- Carry the approved model-download policy into final packages; finish the
  codec/source-distribution policy and older DirectML runtime risk treatment.
  No unresolved restoration weights are bundled.
- Final immutable rebuild with updated dependency notices and routine packaging-tool
  pins, affected acceptance, fresh signatures/notarization and exact source provenance.
- Public hosting, protected independent updater-key backup, publisher/version/support
  confirmation and final content/publication review. Draft feed URLs are intentionally
  unusable until those choices are settled.

The [review summary](beta-review-summary.md) separates these decisions, user-only
account/access actions, and the remaining implementation work that stays with us.
All eight backend targets and the agreed Labs features remain in scope.

## 13 September — approved RealPLKSR license/download policy

The user approved retaining NomosWebPhoto and HFA2k publisher downloads after
reviewing explicit CC BY 4.0 declarations, intended application downloads and
exact checkpoint provenance. The isolated beta source now shows the author,
license and source links, retains those notices after installation and provides
an ordinary verified download with progress/cancellation. The two HAT face
checkpoints remain verified manual imports. No checkpoint is bundled or replaced.

Targeted verification: **54 frontend tests, 18 catalog tests and three Rust
download-policy tests passed**. Svelte checking found zero errors/warnings;
the production frontend build, generated catalog and separate beta metadata
checks passed. The legacy Qt estimator module was not run; its updated catalog
license-set assertion passed by direct import without opening a GUI.

Both exact publisher files were freshly downloaded into a temporary model
directory, passed size/SHA-256 verification and real ModelAdapter loading on
Torch 2.13 CPU, then produced finite 32×32 → 128×128 inference output. Temporary
weights were removed; the installed models directory was not touched. This is
bounded loading/inference verification, not broad quality or new installed-GUI
acceptance. Evidence: `build/beta-review/research/model-license-20260913/`.

README/model notices, beta checklist/decision/readiness records, draft beta
webpage, release notes and reviewer version notes now reflect the approved
policy. Retained MSIX/AppImage/DMG and review archives predate these source
changes and remain immutable historical evidence pending the final rebuild.
No public destination, active `.12` job/checkout/runner or this Mac's GUI changed.
