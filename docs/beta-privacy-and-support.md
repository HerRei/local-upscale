**LocalSR privacy and support drafts**

Prepared locally on 12 September 2026 from the desktop implementation. This file
is preparation for the website and Store submission, not a published policy.
The user confirmed GitHub Issues for bug reports and
[hermes.reisner@gmail.com](mailto:hermes.reisner@gmail.com) for contact and private
privacy requests. Confirm the publisher's legal name/entity and the final package
behavior before publication. The contact was supplied explicitly by the user.

**Privacy text**

LocalSR processes the images and videos you select on your own computer. The
application does not upload your media for enhancement or use it to train models.
It does not require a LocalSR account and does not include advertising, app usage
analytics or automatic submission of diagnostic or benchmark reports.

To process media, LocalSR reads the selected files and their metadata, creates
previews and writes results to the output location you choose. The local queue
database stores filenames, paths, media properties, thumbnails, job settings,
status and output locations. Settings, recipes, benchmark results, downloaded
models, temporary processing files and recovery backups are also stored locally.
Closing the application does not erase this data.

Model downloads connect to the model host identified by the application's
catalog, including GitHub or Hugging Face and their download infrastructure.
Those services receive normal connection information such as your IP address,
the requested download and request headers. Download requests do not contain
your source image or video. Some features, such as a first use of face detection,
may need an additional model download.

For a direct-download edition with updates enabled, checking for updates contacts
the configured release service for compatible software. Downloading an update
contacts its file host. These services receive connection information and the
requested manifest or artifact. A Microsoft Store edition receives application
updates through Microsoft once that edition's Store integration is complete.
Selecting a website or model-license link opens an external site in your browser.
Those services apply their own privacy policies.

Copy diagnostics places a summary on your clipboard when you request it. The
current summary includes the app/worker versions, platform, hardware, memory
measurements, installed-model counts and queue status counts. It omits media
names, file paths and media content. Benchmark reports can be copied or saved to
a local JSON file. LocalSR does not send either report automatically. Review any
report, error text or screenshot before sharing it; manually added screenshots
and logs can contain personal information. Clipboard synchronization and operating
system backup or crash-reporting services follow your system settings.

If you choose to submit a bug report through GitHub Issues, GitHub receives the
information you send. Reports on the public issue tracker, including your
account name and attachments, are public. For contact, privacy requests or
confidential information, email
[hermes.reisner@gmail.com](mailto:hermes.reisner@gmail.com). Email messages are
handled by the email providers involved and received in the project's Gmail
inbox. They are not automatically posted to GitHub. The public tracker address
and support-record retention arrangements must be completed before this draft
is published.

The LocalSR website is hosted on GitHub Pages. GitHub records visitor IP addresses
for security. The current LocalSR website uses locally hosted fonts and assets
and has no added analytics script. GitHub's own service practices are described
in its [privacy statement](https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement).
See also [GitHub Pages visitor information](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages).

**Local data and removal instructions**

The current unpackaged desktop builds use these locations:

| Platform | LocalSR data folder |
| --- | --- |
| Windows | `%LOCALAPPDATA%\LocalSR` |
| macOS | `~/Library/Application Support/LocalSR` |
| Linux | `${XDG_DATA_HOME:-~/.local/share}/LocalSR` |

The `models` subfolder contains downloaded checkpoints and may retain partial
downloads so they can resume. The `next` subfolder contains settings/recipes,
the queue database, benchmarks, work files and update/recovery files. Exports are
stored in the output directory selected for each job. The actual MSIX storage
and uninstall behavior still need installed Windows verification before these
instructions are adapted for the Store edition.

Removing media from the queue removes its active queue entry and associated
jobs; it does not remove source files or finished exports. Local backups can
retain earlier queue information. For complete local removal, quit LocalSR,
back up anything you want to keep, and remove the appropriate LocalSR data and
output files. Removing the LocalSR data folder also removes recipes, settings
and downloaded models. Removing the application alone may leave those files.
Storage providers or operating system backups may retain their own copies.

**Support page text**

LocalSR beta feedback helps identify problems with different media, models and
hardware. Check the known limitations below, then report a reproducible problem
through GitHub Issues. For contact or private matters, email
[hermes.reisner@gmail.com](mailto:hermes.reisner@gmail.com). The tracker files are
prepared locally; add its working public URL when it is published.

Include the LocalSR version, your operating system, processing device, model,
input dimensions and the steps that caused the problem. Copy diagnostics can
supply hardware and worker details. For video, include its format, approximate
duration, HDR/SDR status and requested output size. A short sample that you are
allowed to share is useful but optional.

Before posting, remove personal paths, private images and unrelated information
from screenshots or error logs. Do not attach credentials, your settings file or
your full queue database. Ordinary bug reports do not require those files.

**Hardware and experimental video**

LocalSR is intended for capable computers. Demanding video processing, especially
SeedVR2 or large output dimensions, needs powerful compatible hardware and
substantial memory. Processing is not real-time: a short clip can take hours,
and long or high-resolution video can take days or longer. CPU processing can be
much slower. Try a short clip at a modest output resolution before starting a
long job; the ETA is measured from the actual work and can change.

The confirmed beta scope keeps image processing and SDR video as the core,
with HDR preservation and SeedVR2 3B as optional Labs. The existing FP16 variant
supports NVIDIA CUDA, AMD ROCm and Apple Metal; the FP8 variant is for CUDA/ROCm.
Only the backends verified in the eventual package will be advertised. A smaller
FP8 download does not guarantee that the working buffers fit in RAM/VRAM.

Known beta limits to confirm against the release candidate:

- A large model or high output resolution may exceed available memory. With
  SeedVR2, reduce the requested output size or use frame-by-frame HAT-S; memory
  saving trades speed and sometimes image quality for lower memory use.
- Both SeedVR2 3B variants export SDR and cannot preserve HDR. HAT HDR preservation is experimental, and its
  output quality has not been established by model training on HDR.
- A MOV extension alone does not determine compatibility. The codec, colour
  metadata, transform and audio tracks also matter. Unsupported media should
  report the cause instead of remaining indefinitely in a loading state.
- An ETA needs completed model work before it can be measured and can change
  with the scene, model and encoding workload.
- Experimental features and model licenses have individual limitations. The
  software license does not replace the model publisher's terms.

Proposed support expectations: a small personal beta project, one feedback queue,
and no guaranteed response time or regular release schedule during university.
This operating plan still needs the user's agreement.

**Publication details still to fill**

| Item | Status |
| --- | --- |
| Publisher legal name and individual/entity status | Awaiting confirmation; Store display name is HerRei. |
| Private privacy/contact route | Confirmed by user: `hermes.reisner@gmail.com`. No test email has been sent. |
| General support destination | Confirmed by user: public GitHub Issues. Tracker files are prepared locally; repository publication and access testing remain pending. |
| Support information retention | Confirm how long private support messages are kept and how deletion requests are handled. Public reports should remain useful without retaining unnecessary personal information. |
| Public page addresses | Proposed `/localsr/privacy/` and `/localsr/support/`; neither has been created or published by this work. |
| Store edition data/update behavior | Awaiting exact MSIX acceptance; do not reuse unpackaged assumptions as verification. |

**Implementation evidence**

| Statement | Source reviewed |
| --- | --- |
| Local paths and queue contents | [paths.rs](../desktop/src-tauri/src/paths.rs), [database.rs](../desktop/src-tauri/src/database.rs) |
| Profile backups remain local | [update_storage.rs](../desktop/src-tauri/src/update_storage.rs) |
| Download hosts, headers and partial downloads | [model_catalog.py](../src/localsr/core/model_catalog.py) |
| Update requests and explicit user controls | [updates.rs](../desktop/src-tauri/src/updates.rs), [UpdatePanel.svelte](../desktop/src/UpdatePanel.svelte) |
| Diagnostic contents and clipboard/export actions | [commands.rs](../desktop/src-tauri/src/commands.rs), [App.svelte](../desktop/src/App.svelte) |
| Local video playback and HDR/model limits | [Video support](video-support.md) |

This review establishes what the current code and documentation say. A network
observation of the final installed candidate, publisher details and support-record
retention are still needed before the final privacy text is approved and published.
