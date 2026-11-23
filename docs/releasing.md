# Releasing

Current release: **v0.5.2-beta** for macOS, Windows and Linux. Release notes live in
[`releases/`](releases/), one file per tag; that file is the GitHub release
body.

## Versions

One version string appears in `pyproject.toml`, `src/localsr/__init__.py`,
`desktop/package.json` and `package-lock.json`, `desktop/src-tauri/Cargo.toml`
and `Cargo.lock`, `desktop/src-tauri/tauri.conf.json`, `uv.lock`, the changelog
heading, the README, this page and `docs/releases/v<version>.md`.
`python scripts/check_release_version.py --tag v<version>` fails when any of them
disagree. For beta versions it also validates `ci/public-beta-release.json` and
the readiness register; `python scripts/check_beta_readiness.py
--require-beta-ready` reports the gates that are still open. Tags with a hyphen
are published as prereleases.

## Release train

Every 14 days `release-train.yml` opens a `Version X` PR made by
`scripts/bump_version.py` ([dependency-updates.md](dependency-updates.md) has the
rules). `bump_version.py X.Y.Z-beta` (or `--next`) does the same by hand. After the
packages are live, `bump_version.py --mark-published` clears the candidate markers.
Before publishing, use `python scripts/bump_version.py --published-notes` to prepare
the release body without the candidate notice.

Before building a candidate on the Mac:

1. Check out the merged commit exactly (`git switch --detach <sha>`); the Windows and
   Linux workflows get a tag or branch at that same commit (`--ref`). Check the built
   source commit and version before attaching anything.
2. Bring the release venv up to the merged dependencies:
   `uv pip install --python .venv/bin/python -e '.[video,face,package,macos-release]'
   --upgrade-package <each runtime package merged since the last release>`. Never
   upgrade `av` or `opencv*` this way: they are the licensing-clean builds from
   `packaging/ffmpeg/install_media_runtime.py`. When their pinned versions change,
   rerun that script. Either way `verify_codec_allowlist.py` must pass.
3. `release_macos_beta.sh` writes `mac-environment.txt` (the venv's `uv pip freeze`)
   next to the build. Diff it against the previous release's copy and put the diff
   in the release PR.

## macOS beta

`scripts/release_macos_beta.sh` builds the app from a committed tree (the
packaging step also compiles `localsr-media` with `swiftc` into the engine
directory), signs it with the Developer ID identity, notarizes and staples the app and the DMG,
packages the update archive and the engine payload, assembles the corresponding
source, and writes `beta.json`, `release.json` and `SHA256SUMS` under
`build/release-<version>/`. It writes the update archive without AppleDouble
entries: the updater strips the bundle name from every path, so a hidden
`._LocalSR.app` entry becomes an empty path and the install fails.

The disk image opens as one Finder window with LocalSR on the left, the
Applications shortcut on the right and an arrow between them.
`scripts/make_macos_dmg.py` builds it with dmgbuild (the `macos-release` extra:
`uv pip install --python .venv/bin/python 'dmgbuild>=1.6.7,<2'`); the layout
constants live there, and `packaging/macos/installer/build_background.py`
renders the 1x and 2x backgrounds from them. Re-render and commit the
backgrounds after changing the layout.

The script needs the media runtime's corresponding source in this checkout —
`build/lgpl-media/{dist,opencv}/corresponding-source` and the tarballs in
`build/extra-sources/` — and refuses to start without them, because they are
only used by the last step. A fresh worktree gets them by building the runtime
(`packaging/ffmpeg/install_media_runtime.py`) or by copying `build/lgpl-media`
and `build/extra-sources` from a worktree that already has them.

`LOCALSR_FEED_NOTES` sets the text the update dialog shows for this release;
without it the entry points at the release-notes page.

Never edit the script while it runs: bash reads it incrementally, so a changed
file resumes at the wrong offset.

After the build:

Public installers, source bundles and updater files use
`https://github.com/HerRei/local-upscale/releases/download/v<version>/`.

1. Create the **draft** GitHub release, then attach the DMG, update archive and
   its signature, engine payload and parts, source bundle, `.sha256` files and
   `SHA256SUMS`. Add the Windows and Linux entries and attach their packages and
   signatures ([Windows and Linux updates](#windows-and-linux-updates)).
2. Download the draft release's files and verify them with
   `shasum -a 256 -c SHA256SUMS`. Verify the full prepared release and signatures
   as described below before publishing.
3. Publish the release. Install the previous version on each platform and confirm
   that the update downloads, installs and relaunches on the new version.
4. Copy `build/release-<version>/beta.json` to the website's `updates/beta.json`,
   update `release.json` and run `localsr/tools/sync_release.py` and
   `validate_site.py`, then publish. GitHub Pages caches the feed for ten minutes.

The release requires a Developer ID signing identity and notarization credentials.
The updater signing key is managed by `scripts/beta_update_signing.py`; see
[Updates](updates.md).

### Windows and Linux updates

Windows installers and Linux AppImages carry their update identity and update themselves
([Updates](updates.md)); its feed entry is written and signed on the Mac, after the
macOS build, from the workflow runs at the same commit:

1. Download both runs into one folder: `gh run download <run> -D
   build/release-<version>/platforms` for the Windows and the Linux run. Every
   artifact lands in its target's folder (`windows-x86_64-cpu`, …). With the Linux
   CUDA and ROCm AppImages and their engine packages this is roughly 45 GB; signing
   signing needs room for one more copy of the largest package.
2. `python scripts/beta_update_signing.py platforms build/release-<version>/platforms
   --feed build/release-<version>/beta.json --commit <sha>` checks every package
   against its checksum file, its installed smoke report (version and update
   identity) and its engine package, and signs nothing unless all of them pass. It
   then signs private copies of exactly those files with the Keychain key, adds the
   entries with their signed release statements next to the macOS entry, and prints
   the `.sig` files it wrote. The copies need room for the largest package once more.
3. Nothing is signed for the .deb: apt installs its updates. It reads the signed
   `linux-x86_64-cpu-native` entry to report a new version and the `native` engine
   entries to download GPU engines, so the feed has no entry for it.
4. `python scripts/verify_prepared_release.py --feed
   build/release-<version>/beta.json --commit <sha> --files build/release-<version>
   --files build/release-<version>/platforms` must pass: one entry per release
   target except the .deb, with its backend, kind, protocol, engine id and engine package, and every
   statement and signature verified against the public key over the local files.
5. Attach the packages, their parts and engine packages from the same runs
   (`release_tag`, or `attach-release-assets.yml`), and upload the printed `.sig`
   files from the Mac to the release. The signatures cover the exact files of those
   runs; a rerun produces different files and needs new signatures.
6. Install the previous version on a Windows machine and from an AppImage on Linux
   and confirm that the update downloads, installs and relaunches, before the feed
   goes live.

## Windows and Linux packages

`windows-installers.yml` and `linux-appimages.yml` build the packages on
GitHub-hosted runners. Both start only by hand, at the release tag or commit
(`--ref`). Given `release_tag`, they attach the verified files to that existing
release; otherwise the files stay run artifacts for 14 days, and
`attach-release-assets.yml` can copy one reviewed artifact to a release later.
Each build gets its update identity from `scripts/release_targets.py --update-env`
and writes its schema 2 engine package (`<package>.engine-payload.json` with
`version`, `os`, `arch`, `protocol_version` and `engine_id`, plus its
`.engine.tar.gz.part-*` files) next to the package, and the update form of that
manifest (`<package>.engine-update.json`, see [Older releases](#older-releases));
the updater signatures are made on the Mac
([Windows and Linux updates](#windows-and-linux-updates)).

The CPU Linux job also builds `LocalSR-v<version>-Linux-CPU-amd64.deb` (package
`localsr`) from the same worker, with Tauri's deb bundler and the `deb` identity
(`release_targets.py --update-env linux-x86_64-cpu-deb`: the CPU engine id, kind
`deb`). The extracted package gets the AppImage's codec, NVIDIA, glibc 2.35 and
source bundle checks (`LocalSR-v<version>-Linux-CPU-deb-source-and-notices.tar.gz`),
and `verify_deb_dependencies.py` checks that every system library it links comes
with its Depends: Tauri adds WebKitGTK 4.1 and GTK 3, `tauri.conf.json` adds
`libc6 (>= 2.35)` and the OpenCL loader that the AppImage bundles. The smoke test
installs it with `sudo apt-get install`, starts the installed host headless, removes
it with `sudo apt-get remove` and fails if a file it installed is left. The .deb and
its checksum are attached with the AppImages; it is never split, it has no updater
file or engine package, and the feed has no entry for it.

### Targets

| Platform | Engines | Runtime |
| --- | --- | --- |
| macOS, Apple Silicon | MPS | Torch 2.13, built and signed on a Mac with `release_macos_beta.sh` |
| Windows 10/11, x86-64 | CPU, CUDA, DirectML | Torch 2.13; CUDA ships as an installer plus verified external engine payloads; DirectML through ONNX Runtime |
| Linux, x86-64 | CPU, CUDA, AMD ROCm | separate Torch 2.13 distributions; the CPU edition also as a .deb for Ubuntu 22.04+ and Debian 12+ |

Intel XPU is listed under `withheld_targets`; see
[media formats and licensing](licensing-media.md).

`ci/tauri-release-artifacts.json`, written by `scripts/release_targets.py
--write-manifests`, must match the registry exactly. `requirements/locks/` holds
the reviewed Windows and Linux dependency locks with hashes; refresh them with
`scripts/lock_backend_requirements.py` and uv 0.12.8. Builders install offline
from verified wheelhouses prepared by `backend_wheelhouse.py`.

Windows CUDA's installer embeds its engine manifest; the Rust host verifies the
adjacent 1,900 MiB payload parts before extracting them into a staging directory
and promoting them atomically. AppImages larger than GitHub's 2 GB file limit
are published as 1,900 MiB parts with the checksum of the whole file.

### Gates

Every package is installed and its bundled host started in headless smoke mode,
which must complete the worker handshake with the expected backend
(`smoke_tauri_installer.py`). `verify_tauri_release_package.py` checks the file
format and architecture, `verify_codec_allowlist.py` the media runtime against
`packaging/ffmpeg/codec-policy.json`, `verify_nvidia_redistributables.py` the
NVIDIA files, and `pip check` the build environment. Every AppImage must also
need at most glibc 2.35 (`verify_appimage_libc.py`) and show its window without
network within 30 seconds (`verify_appimage_window.py`), as AppImageHub tests
it. Each package gets its corresponding source bundle.

The installed host's smoke report must also show the edition's update identity
(`smoke_tauri_installer.py --update-target`), so a package built without its updater
key cannot reach a release.

Older releases must be able to update to this one, with their own code
(`upgrade_check.py`, see [Older releases](#older-releases)): in the Windows CUDA
and Linux CPU jobs and in `release_macos_beta.sh`.

`scripts/check_desktop_architecture.py` fails when either workflow loses one of
these gates, its update identity or its engine package, starts on anything but a
manual run, or lets a job other than the attach job write to releases, and when
`release_macos_beta.sh` loses its signing, notarization or smoke tests.

### Older releases

Every installed release updates with the code it shipped, not with this release's.
On 2026-11-23 every 0.5.0 install refused the update to 0.5.2: the feed named a
schema 2 engine manifest, which the updaters of 0.4.0 and 0.5.0 reject, and no test
had run an older updater ([post-mortem](postmortems/2026-11-23-updates-from-0.5.0.md)).
So:

- The feed names `<package>.engine-update.json`: the engine manifest in the form every
  updater since 0.4.0 reads (exactly `schema_version` 1, `format`, `backend`,
  `file_count`, `unpacked_bytes` and `parts`). It is derived from the schema 2 manifest
  and signed with it; the version, platform and engine identity it leaves out are bound
  by the signed release statement. `verify_prepared_release.py` refuses a feed whose
  manifest an older updater would refuse. Never add a field to that form, to the
  release statement or to anything else an older updater parses strictly.
- `scripts/upgrade_check.py` checks out each release in `OLDER_RELEASES`, builds its
  update code with `scripts/upgrade_check/old_release_probe.rs` injected and runs it on
  this release's engine package, signed with a disposable key: the contract check, the
  release statement and the engine installation. This release's host must then record
  that engine and start it. Run it locally with `--as-in-0.5.2` to see it fail the way
  0.5.2 did.
- After publishing, add the new tag to `OLDER_RELEASES`. If a release changes the update
  functions the probe calls, give that release its own probe rather than dropping it.
- A change to the feed, the release statement, the engine manifest or the engine folder
  layout is reviewed for what every installed release will do with it.

### Signing

macOS: `release_macos_beta.sh` signs with the Developer ID identity, notarizes,
staples and checks the app and the disk image with Gatekeeper, and signs the
update archive with the updater key. The Windows and Linux updater files are signed
with the same key on the Mac (`beta_update_signing.py platforms`). No workflow uses
signing secrets.

Windows: the beta's direct installers are not Authenticode-signed until a
certificate is bought; the Microsoft Store package is signed by the Store.

### Failure recovery

- Transient failure, same commit: re-run the failed jobs.
- Fix requires a new commit: build every platform again from that commit.
- Never present binaries from an older commit as products of a newer one.

### Runners

Every workflow runs on GitHub-hosted runners; see
[ADR 0008](adr/0008-hosted-ci.md). The self-hosted release workflow, the runner
maintenance workflows and their provisioning were removed in 0.3.1.
