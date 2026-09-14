# LocalSR public beta review

The [13 September independent handoff](beta-handoff-2026-09-13.md) is the current
review hub. **No final package is ready for publication.** All beta builds are
paused until the user explicitly confirms `.12` concluded and authorizes them.
No polling, timer, alternative build host, public server or publication is active.

The new Windows ONNX source worker has corrected separate CPU/iGPU benchmarks,
real image/video/live-progress tests, cancellation and memory-failure recovery.
NAFNet SIDD's GPU numerical discrepancy still needs a user decision; no temporary
CPU restriction has been applied. Current runtime evidence is separate from the
previously installed 1.0.7.0 MSIX and its successful upgrades/recovery.

Retained earlier artifacts remain valuable evidence: notarized/stapled macOS
ARM64 app/DMG, Store MSIX installation/upgrades, four signed Linux AppImages,
CPU/ROCm/MPS worker checks, bounded long-video/4K/resource tests and signed-update
download/tamper checks. They predate current source/dependency changes and require
rebuilding plus affected installed acceptance. Windows WACK remains WARNING;
CUDA/XPU hardware remains Labs without physical-device acceptance claims.

The updated website, GitHub release draft, Store listing/reviewer instructions,
actual older screenshots, dedicated-directory hosting configuration and
checksummed artifact inventory are local review material. The rollout is
**website/GitHub first, Microsoft Store afterwards**, following final package
review. Contact: **hermes.reisner@gmail.com**. Eight backend targets and all
agreed Labs features remain in scope; unresolved face checkpoints remain imports.

Use the [handoff](beta-handoff-2026-09-13.md) for evidence links, remaining build
checks, decisions, personal tasks and the exact next sequence. The
[checklist](beta-release-checklist.md) and [readiness register](../ci/public-beta-readiness.json)
remain explicitly not ready. Earlier details are preserved in the
[installed acceptance record](beta-acceptance-2026-09-12.md).
