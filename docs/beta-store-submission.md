**LocalSR Store listing and certification preparation**

Prepared locally on 12 September 2026. These are drafts for the future beta,
based on the current desktop source. The Store draft has not been changed.
Confirm the advertised features against the installed Windows candidate before
using this text. Package construction, screenshots, upload and certification
remain on the [beta checklist](beta-release-checklist.md).

Local checks passed: the short description is 175 characters, the description
1,947 characters, all nine features and four planned captions fit their field
limits, and local documentation links resolve. These checks do not validate the
future package or replace review of the captured screenshots.

**Listing fields — English (United States)**

Select the reserved product name **LocalSR**. The following plain-text blocks
can be copied into the corresponding Partner Center fields.

Short description:

```text
Give photos and videos more detail with AI models running on your own computer. Compare results, save recipes and process a queue without uploading your media for enhancement.
```

Description:

```text
LocalSR is a desktop app for enlarging images, reducing noise and enhancing video with AI models that run on your computer.

Choose your media, select a model and inspect the result before saving a separate output. Reuse your settings with recipes, process a queue and compare the original with the enhanced result.

Processing stays local. LocalSR has no account requirement, advertising or automatic upload of your media. An internet connection is needed to download models and receive software updates. Once the required model files are installed, enhancement can run offline.

This beta is intended for people who want to try LocalSR on capable hardware and report problems. Demanding video work, especially high-resolution output and SeedVR2, needs a powerful compatible GPU and substantial memory. Processing is not real-time: a short video can take hours, and long or high-resolution jobs can take days or longer. CPU processing can be much slower. Speed and memory use depend on the model, hardware and input and output dimensions. Test a short clip at a modest output size first.

Image processing and SDR video are the core beta features. Optional SeedVR2 3B video restoration is included as Labs, with FP16 and FP8 variants where supported by the installed backend. FP8 has a smaller download and lower weight storage, but working memory can still be large. Even 16 GB of GPU memory is not a guarantee that a particular video will fit. SeedVR2 exports SDR and cannot preserve HDR.

HDR preservation is also experimental and limited to compatible models and input formats. SDR-trained model quality on HDR remains unverified. AI enhancement can introduce artifacts or change fine details; inspect the result and keep your originals.

Models have their own licenses and download requirements. Some models require a user-supplied checkpoint. The application's license does not grant rights to every model or to media you choose to process.
```

Product features — enter each line as a separate feature:

```text
Image upscaling and noise reduction with selectable local AI models
Video enhancement with explicit SDR output and model compatibility information
Original and enhanced comparison views
Live processing stages, model output previews and measured time estimates
Reusable recipes and queued processing
Separate CPU and GPU benchmark results where the installed backend is available
Local diagnostics that you choose whether to share
Model integrity verification and visible license information
Optional SeedVR2 3B FP16 and FP8 video restoration with explicit Labs limitations
```

Leave **What's new in this version** empty for the first Store submission. Put
website, privacy and support links in their dedicated fields. Microsoft's limits
are 10,000 characters for the description, 1,000 for the short description
(under 270 recommended), and 20 features of at most 200 characters each.
[Microsoft listing fields](https://learn.microsoft.com/en-us/windows/apps/publish/publish-your-app/msix/add-and-edit-store-listing-info).

**Support and privacy fields**

| Field | Preparation |
| --- | --- |
| Website | Existing LocalSR homepage: `https://herrei.github.io/localsr/`; recheck its release links before submission. |
| Support contact info | `hermes.reisner@gmail.com` — confirmed by the user. Use GitHub Issues for bug reports; add its public URL to the support page once the tracker is published. |
| Privacy policy | Use the [privacy draft](beta-privacy-and-support.md) after confirming publisher details and hosting it at an accessible URL. No new privacy page is live yet. |
| Additional system requirements | Finalize from the exact Windows engine and installed acceptance. The earlier 16 GB minimum / 32 GB recommended RAM values are provisional planning choices. |

**Confirmed beta scope and hardware message**

The user selected image processing/SDR video as the core and HDR preservation/
SeedVR2 as optional Labs. The hardware and duration warning above belongs in the
Store description and website, and the individual SeedVR2 descriptions also show
it in the application. This scope decision does not complete installed acceptance.

Both 3B variants already exist in the local catalog; no new checkpoint is being
introduced by this listing update. Their complete catalog downloads, including
the VAE, are:

| Optional Labs model | Catalog ID | Download | Backend described by the current catalog |
| --- | --- | --- | --- |
| SeedVR2 3B FP16 | `seedvr2_3b` | About 7.28 GB | NVIDIA CUDA, AMD ROCm, Apple Metal |
| SeedVR2 3B FP8 | `seedvr2_3b_fp8` | About 3.89 GB | NVIDIA CUDA, AMD ROCm |

These decimal GB values are file sizes, not RAM/VRAM requirements. Only advertise
backends that pass acceptance in the shipped package. FP8 reduces weight storage;
clip buffers, activations, output resolution and offloading still determine
working memory. See the [catalog](../src/localsr/core/model_catalog.py) and
[recorded short-clip results](video-support.md). CPU processing remains part of
the core path; it is not a practical-performance promise for SeedVR2.

LocalSR accesses user-selected photos and videos, which can contain personal
information even when processing stays on-device. The proposed answer to the
privacy access question is **Yes**, with a privacy policy. Microsoft asks about
access as well as collection and transmission. [Microsoft privacy and support
fields](https://learn.microsoft.com/en-us/windows/apps/publish/publish-your-app/msix/support-info).

**Four Windows screenshots to capture later**

Use the actual installed Windows beta and media whose use in the public listing
is permitted. Capture the application window with readable controls and no
personal desktop or unrelated applications. Keep the original captures and
record the build, model and source permission with them.

| File to capture | Show | Caption |
| --- | --- | --- |
| `01-image-comparison.png` | A completed photo result with the original/enhanced comparison and actual selected model. | Compare the original with the enhanced result. |
| `02-video-processing.png` | A permitted SDR clip during real processing, with actual model output and ETA once measured. | Follow video enhancement with real processing progress. |
| `03-recipes-and-queue.png` | A saved recipe and a small queue using the candidate's real controls. | Reuse settings and process a queue of media. |
| `04-performance.png` | A completed benchmark on the hardware actually available to the Windows candidate. | Check local performance for the selected processing device. |

Target **1920 × 1080 PNG**, at most 50 MB each. The desktop minimum is
1366 × 768; one screenshot is required and four are recommended. Captions must
fit within 200 characters. Do not fabricate output, progress or GPU scores, and
do not use the Mac screenshots as Windows captures. Optional Store artwork can
wait; the package icons are already prepared. [Microsoft screenshot
specifications](https://learn.microsoft.com/en-us/windows/apps/publish/publish-your-app/msix/screenshots-and-images).

**Notes for certification — draft instructions**

Complete the candidate-specific facts below before pasting the reviewer notes:

- App version, four-part Store package version and package SHA-256.
- Included engine/backend, tested Windows version and WebView2 setup behavior.
- A small distributable sample image and SDR clip with documented permission.
- Exact download size and name of the model used in the reviewer procedure.
- Installed Store update and data-preservation results.

```text
LocalSR is a desktop image and video enhancement application. It processes user-selected media locally and launches a bundled inference worker as the signed-in user. The runFullTrust capability is needed for this desktop host and its local worker. The application does not require a LocalSR account or a subscription.

An internet connection is needed for the initial model download. Model files are not bundled with the application. The model's license information is shown in the application, and the downloaded file is checked against the catalog's expected size and checksum before use.

Suggested review procedure:
1. Launch LocalSR and add the supplied sample image.
2. Select stock HAT-S x4 and use the application's download control if the model is not installed. Review the displayed license information.
3. Choose CPU processing and an output directory that is writable by the current user. Start enhancement and verify the resulting image.
4. Save a recipe, close the application, reopen it and verify that the recipe and downloaded model remain available.
5. Add the supplied short SDR clip, select frame-by-frame processing and stock HAT-S, and export an MP4 result.
6. Start another job, cancel it, wait for cancellation to finish, and confirm that another job can start.

SeedVR2 3B FP16/FP8 and HDR preservation are optional experimental features with model-specific limitations. They are not required for the basic image/SDR-video procedure. SeedVR2 outputs SDR, needs capable hardware and may take hours or days on demanding video. Use a short sample and modest output resolution for functional review. Do not select an unsupported GPU backend; only the backends documented for this exact package are included.
```

These instructions assume a CPU-capable candidate with stock HAT-S and SDR video.
The engine/scope decision and the installed procedure must pass before the notes
can represent the submitted package. They do not claim an MSIX or Store update
has already been tested. See the [package guide](microsoft-store.md).
