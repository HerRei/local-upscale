# LocalSR Store submission materials

Prepared 13 September 2026. The retained **1.0.7.0 CPU/DirectML MSIX** is an
earlier accepted baseline. [Updated listing/reviewer drafts](../build/beta-review/rollout-20260913/store-final-draft/)
await the next package; no new build or Store submission is authorized.
The app version **0.0.13-beta.1 remains provisional**. No Store submission or
website publication has occurred. The actual copyable files and four installed
screenshots are in `build/beta-review/store/`; the package and hashes are in
`build/beta-review/windows/`.

The listing explicitly distinguishes the shipped CPU/DirectML engine from
SeedVR2's CUDA/ROCm/Metal editions. All agreed backends remain in the project;
the Store package is not advertised as containing all of them.

## Rollout and capture refresh

The approved order is website/GitHub beta first, then Store submission. The four
existing captures are retained evidence from 1.0.7.0; recapture affected views,
especially benchmark v2.1 and final model-policy wording, from the next installed
MSIX. No screenshot of an unbuilt candidate has been created. WACK and final
listing/runtime claims must refer to the same package uploaded for certification.

## Copyable listing

Short description:

> Enlarge images, reduce noise and enhance videos with AI on your computer. Compare results, save recipes and process a queue without uploading media for enhancement.

Description:

LocalSR enlarges images, reduces noise and enhances video with AI models running on your computer.

Add your media, choose a compatible model and compare the original with its enhanced result. Save recipes for repeat work. Process individual files or a queue, with grouped results for imported folders. Watch actual model tiles and processing stages, with estimates for the current job, next job and whole queue once enough work has been measured.

Processing stays local. LocalSR has no account requirement, advertising or automatic media uploads. Internet access is needed for model downloads and Store updates. After compatible models are installed, enhancement can run offline.

This public beta is intended for capable computers and patient testers. AI processing is not real-time. Video can take hours or days, depending on duration, model and output dimensions. Try a short clip first. A GPU's memory capacity alone does not guarantee that a job will fit.

This Microsoft Store package includes CPU processing and DirectML for compatible graphics adapters. Installed acceptance covered Windows 11, an Intel Core i7-8550U, 16 GB system RAM and Intel UHD 620 integrated graphics. Performance varies; on this test PC the CPU benchmark was faster than the iGPU benchmark. Other adapters are not claimed as physically tested.

Image processing and SDR video are the core beta features. Compatible HAT models also offer experimental 10-bit HLG/PQ preservation. They were trained on SDR; HDR image quality is unverified. In-app live previews use an SDR display conversion.

SeedVR2 3B is retained in the wider LocalSR project for compatible CUDA, ROCm and Metal editions. It cannot run on this CPU/DirectML Store package. HDR preservation is disabled for SeedVR2.

AI enhancement can alter faces, text and fine detail. Inspect outputs and retain originals. The denoiser can reject unstable results; two of eleven challenging scanned documents remained unsupported in testing. SeedVR2 can produce tile seams in memory-saving configurations on supported editions.

Models retain their own licenses. Some checkpoints require user import or additional license acknowledgements. The application's license does not grant rights to every model or to your input media.

## Actual screenshots

1. `01-image-comparison.png`: actual installed HAT-S Earth image comparison.
2. `02-video-comparison.png`: completed video comparison in the installed app.
3. `03-live-video-tiles.png`: real HAT-S video tiles and timing while processing.
4. `04-separate-cpu-gpu-scores.png`: separate measured CPU and UHD 620 results.

All are unedited 1725×1030 PNG captures from installed Windows 1.0.7.0.
Captions, image hashes and NASA provenance are in `store/screenshots.json` and
`store/samples/source-permission.json`. These are public demonstration fixtures,
not the user's private scan acceptance material. Microsoft requires desktop
screenshots of at least 1366×768; four are recommended.
[Microsoft screenshot requirements](https://learn.microsoft.com/en-us/windows/apps/publish/publish-your-app/msix/screenshots-and-images).

## Reviewer and submission

The exact reviewer procedure is `store/reviewer-instructions.txt`. It includes
no-account startup, stock-model download, images, folder queue, video switching,
real tiles/ETA, cancellation/recovery, separate benchmarks and preservation.
It discloses the retained WACK warning and independent native DPI evidence.

The reserved identity is `HerRei.LocalSR`; Store ID `9NTG848ZQTCQ`.
The upload candidate SHA-256 is
`bf4f748d95cfea976e05ca394ee13e53b3c3cd2dfecc675ab6cb6b3525f7bf9c`.
This is a review candidate, not a claim of certification readiness.

Privacy/support pages are prepared at `/localsr/privacy/` and
`/localsr/support/` in the isolated website clone. They must be published,
anonymously accessible and approved before their URLs go into Partner Center.
Contact is **hermes.reisner@gmail.com**. Personal publisher **Hermes Reisner**,
branding **HerRei** and the beta's hobby purpose were confirmed on 13 September
2026. Public tracker activation, any remaining account-holder/trader declarations,
final dependency/source-license delivery, remaining native
acceptance, upload and certification remain on the beta checklist.
