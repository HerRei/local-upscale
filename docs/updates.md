# Updates

## In the app

From 0.3.7, every direct download updates itself: the macOS app, the Windows CPU, DirectML and
CUDA installers, and the Linux CPU, CUDA and ROCm AppImages. The Microsoft Store
edition receives application and engine updates from the Store; its in-app updater
is disabled.

**Check for Updates…** sits in the LocalSR menu on macOS and in the Help menu on
Windows and Linux. It opens Software Update: choose the Stable or Beta channel,
check for a compatible release, read its notes and total download size, download
it, and install and restart. The beta also checks its feed once at launch and shows
a notice when a newer build exists. **Check for Updates at Launch**, next to
**Check for Updates…**, or the matching box in the update dialog, turns that launch
check off from the next launch (setting `check_updates_at_launch`); asking with
**Check for Updates…** still works. Both switches can change while jobs run, and the
menu shows them only in builds that check a signed feed (`checks_for_updates()`: a
direct build compiled with its update identity). The host saves them on their own
(`set_update_switch`); a full settings save never changes them, so a window that has
not caught up cannot turn an opt-out back on. Downloads can run while jobs are
processing; installation waits until the queue, any cancellation and any model
download have finished, and the native host enforces that as well as the interface.

How the verified package is installed depends on the edition:

- **macOS:** the Tauri updater replaces the app bundle from the signed
  `.app.tar.gz`, then LocalSR restarts.
- **Windows:** the signed NSIS installer is the update. LocalSR checks that the
  install folder's volume has room, records the pending installation
  (`update-pending.json`), and the Tauri updater starts the installer in passive mode
  with `/UPDATE` and `/LOCALSR_ENGINE_MANAGED=1`; LocalSR exits and the installer
  starts it again. The next start confirms the installation when it runs the new
  version: only then are the version recorded in the profile backup and the downloads
  removed. If the old version starts instead, the installer failed or was cancelled,
  and the verified downloads stay for another try. With the managed flag the CUDA
  installer's NSIS hook skips its own engine installation from adjacent parts, since
  the update has already prepared its versioned engine, or the engine identity is
  unchanged. The hook keeps any existing `engine` in the install folder: the flag
  cannot prove ownership of that directory at the installer's chosen destination.
  This may retain a stale engine and use extra disk space. NSIS cannot roll back:
  an installer that fails halfway leaves a
  partly updated install folder. Settings, the queue and models are kept, and running
  the full installer from the website repairs it.
- **Linux AppImage:** the signed AppImage is the update. LocalSR replaces the file
  named by `APPIMAGE` itself, streaming from disk (the Tauri updater would read the
  whole AppImage into memory). Under a lock on the AppImage's folder it copies the
  download into a new file next to the AppImage and verifies that copy, keeps a hard
  link to the working image as the backup (or a complete, permission-preserving copy
  on filesystems without hard links), renames the copy over the AppImage, runs
  the new image's `--headless-smoke-test`, and renames the backup back if that check
  fails. The working image never leaves its name, and the running image stays mounted
  until the restart. A journal (`swap-journal.json` in the profile) names the backup
  and the copy until the new image is accepted. The profile journal is locked before
  reading or writing; different app folders sharing that profile cannot overwrite it.
  It is written first, marked `preparing`, so a crash while staging or copying only
  removes the partial files at the next start; it is rewritten with an atomic rename
  and directory sync to a restoring record only after a complete, synchronized
  backup exists. An accepted update removes the backup before the journal, so a crash
  in between keeps the new image and leaves no stray file. Under the same lock, `--headless-update-version`
  reads the version at the destination before staging, so a surviving older process
  cannot overwrite a newer installation with an intermediate signed release.
  A failure after replacement restores the backup. Every removal in the application's
  folder (backup, staged file) is synced before the journal that names it is removed,
  and a start that finds the journal removes only `<journal>.new-<uuid>` leftovers of
  its own, under the profile lock. A backup that is no longer a plain file is never
  renamed over the application.
  Malformed journals are quarantined once. A start after a crash rolls the swap
  back before it opens anything and starts the restored image, using the same
  systemd user-service restart path as a normal update. A copy that does not
  run from its AppImage (no `APPIMAGE`, or the host outside `APPDIR`) refuses the
  update, and the AppImage's folder must be writable.
- **Portable Linux host** (`portable` kind): see [Feed format](#feed-format).

### Anonymous update-check count

After a successful check, direct builds send one request to
`https://macmini-ci.tail34a4e0.ts.net/ping/update-check?v=<version>&t=<platform key>&c=<channel>`,
for example `v=0.1.6-beta&t=darwin-aarch64-mps-native&c=beta` or
`t=windows-x86_64-cuda-native`. It carries no install or user ID and no cookies,
times out after five seconds and never affects the check. The usage counter counts active
installs per day without storing the IP address (see `packaging/hosting/stats/`).
Turn it off with **Send Anonymous Update Count** (LocalSR menu on macOS, Help menu
on Windows and Linux) or **Send an anonymous update-check count** in the update
dialog (setting `anonymous_update_count`). With the launch check off, the count is
sent only after a check you ask for. Store editions and loopback test feeds never
send it; `LOCALSR_USAGE_PING` points a build at another HTTPS endpoint.

## Build identity

Each build carries its update configuration at compile time; changing a value
means rebuilding the Rust host.

| Variable | Meaning |
| --- | --- |
| `LOCALSR_UPDATE_PUBLIC_KEY` | Updater public key in the format written by `tauri signer generate`; never the private key |
| `LOCALSR_UPDATE_FEED` | HTTPS directory containing `stable.json` and `beta.json` |
| `LOCALSR_UPDATE_BACKEND` | The package's engine: `cpu`, `cuda`, `rocm`, `directml`, `mps` |
| `LOCALSR_ENGINE_ID` | Immutable identity of the bundled engine build |
| `LOCALSR_UPDATE_KIND` | `native` for Tauri updater artifacts, `portable` for the unpacked Linux host |

Each release target in `ci/tauri-targets.json` names its update `backend` and
`kind`. The Windows and Linux workflows set the variables with
`scripts/release_targets.py --update-env <target> --commit <sha>`, which takes the
key from `packaging/updates/production.pub`. The engine id is
`<target>-<SHA-256 of the engine recipe>`; `release_macos_beta.sh` uses the same
calculator for macOS. The commit argument is not part of that identity: the
smoke step stamps it into each build's `.smoke.json` (`commit`) and `platforms`
refuses a report stamped for another commit. The installed host's `--headless-smoke-test` report states
its compiled identity (`update`), and the workflows fail when it is not the
expected one (`smoke_tauri_installer.py --update-target`).

The platform key in a feed is `<os>-<architecture>-<backend>-<kind>`:

| Edition | Platform key |
| --- | --- |
| macOS, Apple Silicon | `darwin-aarch64-mps-native` |
| Windows CUDA, CPU, DirectML | `windows-x86_64-cuda-native`, `windows-x86_64-cpu-native`, `windows-x86_64-directml-native` |
| Linux CPU, CUDA, ROCm AppImage | `linux-x86_64-cpu-native`, `linux-x86_64-cuda-native`, `linux-x86_64-rocm-native` |
| Unpacked Linux host | `linux-x86_64-<backend>-portable` |

The Stable channel rejects prereleases, and both channels reject entries whose
backend, package kind or worker protocol do not match the installed build. The
public beta feed is `https://herrei.github.io/localsr/updates/beta.json`.

## Feed format

The feed is Tauri's manifest format with a `localsr` object inside each platform
entry:

```json
{
  "version": "0.1.6-beta",
  "notes": "What changed and what to watch out for.",
  "platforms": {
    "darwin-aarch64-mps-native": {
      "url": "https://downloads.example.org/LocalSR-v0.1.6-beta-macOS-arm64.app.tar.gz",
      "signature": "BASE64_TAURI_SIGNATURE",
      "localsr": {
        "channel": "beta",
        "backend": "mps",
        "kind": "native",
        "protocol": 1,
        "engine_id": "engine-build-id",
        "size": 123456,
        "unpacked_size": 456789,
        "sha256": "64_HEX_CHARACTERS"
      }
    }
  }
}
```

Each entry's `localsr.statement_v2` is a small JSON document, base64-encoded, signed
with the updater key (`statement_v2_signature`). It states the release version,
channel, platform key, backend, kind, worker protocol, engine id, package `size`,
required unpacked size and package SHA-256, and for every application part
(`parts`), the engine manifest (`engine_manifest`) and every engine part
(`engine_parts`) its `name`, `size` and `sha256`. The feed itself is not signed, so the
app trusts those fields only when they equal the verified statement, and refuses a
version that is not newer than the installed one. A feed that relabels an older or
another edition's signed package, inflates a size, or renames, adds or drops a part is
rejected when the app reads it, before anything is downloaded. Only the download URLs
stay outside the statement: each file's own signature and the signed digest still decide
whether its bytes are accepted.

Statement format. `schema_version` 2 is the format above; the app accepts exactly this
format and requires it: an entry with no `statement_v2`, or with one that is unsigned,
unknown or of another version, is refused. A future format gets a new field name
(`statement_v3`) so one feed can serve several app versions. Clients before 0.3.8 read
only `localsr.statement` (`schema_version` 1: the same fields without the sizes and part
lists, and only the engine manifest's digest), which accepts no other fields, so every
entry keeps it unchanged next to `statement_v2` and those clients work with a new feed.
The signer writes and the feed check verifies both. Drop format 1 once no supported
client older than 0.3.8 remains.

The updater file is the `.app.tar.gz` on macOS, the NSIS installer on Windows and
the AppImage on Linux. A package above GitHub's 2 GiB file limit (the Linux CUDA
and ROCm AppImages) is published as 1,900 MiB parts. Its entry's `url` names the
first part, `localsr.parts` lists every part as a file with its own `name`, `url`,
`size`, `sha256` and `signature`, and `size`, `sha256` and the entry's `signature`
describe the whole AppImage. The app downloads and verifies each part, joins them,
and verifies the joined file before it can be installed. The parts must add up to
the package and stay below 2 GiB.

Sign an archive with `python scripts/beta_update_signing.py sign <archive>`; it
obtains the private key through the Keychain helper and never writes it to disk.
The Windows and Linux updater files are signed on the Mac as well:
`python scripts/beta_update_signing.py platforms <downloads> --feed
build/release-<version>/beta.json --commit <sha>` reads the build artifacts as
`gh run download` leaves them (one folder per release target). It first checks every
edition: the package against its checksum file and its installed smoke report
(version, update identity and release commit), and the engine package as the app's installer would
(part names inside the folder, no links, sizes, digests, a gzip tar of exactly
`file_count` safe regular files with the worker); for Windows CUDA the engine
manifest must be the one the smoke test installed. If anything fails it signs nothing.
Then it copies each file into a private temporary folder, checks the copy against
what it checked before, and signs only the copy, so a file that changes meanwhile is
never signed; a split AppImage is joined there and its parts are cut from that copy.
It adds the entries, each with its two signed statements, next to the macOS entry;
`release_macos_beta.sh` signs the macOS statements with
`beta_update_signing.py statement`. `python scripts/verify_prepared_release.py --feed
build/release-<version>/beta.json --commit <sha> --files build/release-<version>
--files <downloads>` then checks that the feed has exactly one entry per release
target with the right backend, kind, protocol and engine id, an engine package and
files below the size limit, and verifies both statements of every entry (including that the sizes and part lists match the entry) and every signature against
the updater public key over the local copies of the files. The engine manifest must
list the engine parts the feed names, and `unpacked_size` must cover the engine. All
files must be at the release's own GitHub download URL, or at the prefix named with
`--host`. The channel of the Windows and Linux entries is `beta` for a version with
a prerelease suffix and `stable` otherwise. Serve immutable files over HTTPS
and publish an entry only after installing it from the feed on a test machine.

A portable Linux update is a signed `.tar.gz` containing only `localsr-next`,
built with the embedded frontend. It replaces that executable and nothing else;
native libraries and the worker come from the installation or an engine package.
No published edition uses it.

## Engine payloads

The inference engine is versioned separately from the app. An unchanged engine
keeps its id across app releases. If the installed id equals the signed new id,
the client skips the separate engine download, extraction, copying and activation;
it installs only the application package. It continues to use an existing versioned
engine or the bundled engine. A changed engine is installed into
`LocalSR/next/engines/<id>/engine`. Every entry carries an `engine` object for users
who do need that engine:

```json
{
  "id": "new-engine-id",
  "manifest": { "name": "worker.engine-payload.json", "url": "…", "size": 1234, "sha256": "…", "signature": "…" },
  "parts": [
    { "name": "worker.engine.tar.gz.part-0001", "url": "…", "size": 123456, "sha256": "…", "signature": "…" }
  ]
}
```

The manifest uses `schema_version: 1` and `format: tar.gz.parts` with the
backend (`CUDA`, `ROCM`, `CPU`, `MPS` or `DIRECTML`), file count, unpacked size
and per-part checksums. Every part and the manifest are downloaded and
signature-checked individually; extraction verifies checksums, paths, file counts
and free disk space. Parts must stay below 2 GiB. `unpacked_size` reserves room
for the app and the engine together, and a protocol handshake must pass before the
new engine is activated. On Windows the NSIS hook accepts the updater's
managed-engine flag, so a CUDA update never asks the user to locate payload parts
by hand.

`release_targets.engine_id` hashes sorted relative paths and SHA-256 file digests
for the worker sources (`src/localsr`), the target's hashed dependency lock,
`requirements/build-tools.txt`, the PyInstaller spec, entrypoint and hooks, the
media/NVIDIA packaging recipes, macOS media helper and worker entitlements, and
worker build helpers. macOS uses `uv.lock`, with the local package version removed.
The informational `localsr.__version__` and its comments are excluded; other code
in that module is hashed. Text line endings are normalized. App versions, commit
IDs, Rust/frontend changes, release asset names and edits to the release tooling
(`release_targets.py`, signing and feed scripts) do not affect the result; a change
to the hashing recipe itself bumps `ENGINE_ID_SCHEME`.
The input list is deliberately conservative: a build-helper change can invalidate
an engine even if its final bytes happen to be identical. When adding an engine
input or changing a toolchain outside these files, update the recipe list/build
recipe so the id changes. Build with the reviewed dependency locks; a manually
changed environment is not evidence that the old engine id can be reused.

CI compiles this id into the host before building, and installed-package smoke
checks, signing and feed verification independently calculate the same id. The
feed's `engine_id`, `engine.id`, and signed statement carry it. Compatibility
continues to depend on worker protocol versions; different engine ids select
payloads and do not imply incompatible protocols.

The Windows and Linux workflows write the separate payload next to the app with
`scripts/prepare_engine_payload.py --target <target>`, using the bundled engine
or adopting Windows CUDA's adjacent parts. The application package still contains
its bundled engine (including the large Linux AppImage). Reusing the id avoids the
additional 16–19 GB engine payload and its installation; it does not turn that
AppImage into a small host-only download.

## Moving from Windows/Linux 0.3.3–0.3.6 to 0.3.7

Inspection of `release/v0.3.3-beta` through `release/v0.3.6-beta` shows the same
behavior: `UpdateConfig::enabled()` requires a nonempty updater public key,
backend and engine id. The Windows/Linux build workflows and build helper do not
set them (only the macOS release script does). These builds do not fetch the feed,
show an update-at-launch notice, or provide a feed-driven download-page link.
An explicit check returns “Signed updates have not been configured for this build”.
Changing `notes`, `url`, or any other feed field cannot reach those binaries.
Do not point an updater package URL at an HTML download page.

Put this notice on the download page and in the 0.3.7 release body:

> Windows/Linux users of 0.3.3–0.3.6: install 0.3.7 once from
> https://herrei.github.io/localsr/download/ to enable future in-app updates.
> Your settings, queue and downloaded models stay in your existing profile.

Finish or cancel jobs and quit every LocalSR process first. On Windows, download
the **same backend and architecture**, run the full 0.3.7 installer, and select the
existing installation folder (particularly if the old installation was moved).
For CUDA, keep every matching `.engine.tar.gz.part-*` file beside the installer.
On Linux, download the same backend's complete 0.3.7 AppImage (join all numbered
parts in order if provided), replace the old file while it is closed, and run
`chmod +x` on the new file. Keep its old name/path if launchers or a user service
refer to it. Restart the app/service and verify 0.3.7 before discarding the old copy.
Do not delete the LocalSR profile or model cache. Microsoft Store users update
through the Store. Subsequent direct Windows/Linux builds carry the updater
identity and can consume the signed feed.

## Verification and recovery

What the updater guards against is installing bytes it has not verified: a feed it
cannot trust, a download changed in transit, or a file swapped in its own folders
after the check. It installs and runs only bytes it verified where they are installed
from: Windows and macOS verify the package in memory and hand over those bytes, an
AppImage or portable update verifies the copy it then renames into place, and an
engine package is installed from the manifest bytes that were verified. Temporary
files are created exclusively, never through an existing file or link. A process
running as the same user can still replace the installed app directly; the updater
does not defend against that.

Downloads stream to partial files and must pass size, SHA-256 and minisign
checks. An interrupted or cancelled transfer can never be installed; complete
verified files are reused after another check. Installation verifies the files
again and checks free space.

Before installing, LocalSR saves settings and recipes, backs up the queue
database including its WAL, and copies the profile to `LocalSR/next/backups`. A
version marker makes the next app version back up existing data before it opens
or migrates the database. Downloaded models are never moved or removed.

Settings that cannot be read, or that come from a newer version, are never
overwritten by a routine save. The app offers to keep the original as a recovery
copy and start with defaults; recipes in that file can be recovered by hand.

An AppImage or portable Linux update runs a startup smoke check of the new host
before removing the previous one, restores it if the check fails, and rolls back at
the next start when a crash interrupted it. On macOS and Windows the platform
installers handle replacement; their failure paths are covered by the
installed-package checks in [Testing](testing.md), and a Windows installer that fails
halfway is repaired by running the full installer.

## Testing the updater locally

Debug builds accept `LOCALSR_TEST_UPDATE_CONFIG`, the path of a JSON file with
`public_key`, `feed`, `backend`, `engine_id` and `kind` (the fields of the build
identity). The feed must start with `http://127.0.0.1:`; downloads from that host
may then use plain HTTP. Two local builds, a disposable key from `tauri signer
generate` and a feed served from the loopback interface exercise the whole flow
without touching the public feed:

1. Build an older and a newer debug package with
   `scripts/build_tauri_preview.py --debug --bundles appimage` (or `nsis`); for the
   second build raise `version` in `desktop/src-tauri/tauri.conf.json` locally and
   pass `--skip-worker`.
2. Write the newer package's engine package with
   `scripts/prepare_engine_payload.py --target <target> --output <feed folder>
   --engine build/tauri-preview/worker-dist/engine`, copy the package there and
   sign every file with `tauri signer sign`.
3. Write `beta.json` with the newer version and an entry for the edition's
   platform key whose `engine_id` differs from the config's, with a format 2 release
   statement (`statement_v2`) signed by the test key (see [Feed format](#feed-format)), serve the folder with
   `python3 -m http.server --bind 127.0.0.1`, and start the older build with
   `LOCALSR_TEST_UPDATE_CONFIG` pointing at a config with the test public key.

Release builds ignore the override and require HTTPS. The regression suite covers
channel, backend and protocol matching, missing engine payloads, invalid
signatures with a recomputed checksum, interrupted transfers, split packages,
release statements, files swapped after their check, planted links at temporary
names, insufficient disk space, settings preservation, the AppImage swap with its
lock, startup rollback after a crash, and the Windows confirmation at the next start. The production candidate test downloads a real signed archive through
the app's own downloader and checks that truncation and substituted content are
rejected.

## Crash safety tests

Test-only fault points (`update_storage::fault`, compiled under `cfg(test)`) let the
tests fail, or "crash" without any cleanup, at every step of the AppImage swap and its
recovery, of the engine package install (`engine_payload::recover_install` finishes or
undoes a killed install at the next one) and of the Windows pending-install marker.
Each run asserts one working executable or engine, no journal and no stray files.
A stop is named by its position in the run, not by its name, so a step that runs more
than once (the journal is written twice, the engine archive has one extraction per
entry) is stopped at each occurrence. The swap runs with the journal beside the
application and in another folder, with a hard-linked and a copied backup, and
recovery is itself killed at every step after a crash at every step of the swap. The
tests also record which files and folders were synced, to pin the order "durable, then
renamed, then the old one deleted".

On the engine side, the old engine is deleted under the name `engine.deleting-<pid>`,
so an `engine.previous-<pid>` is always a whole engine and recovery restores only one
that holds the worker. Recovery touches only folders named
`engine.installing-|previous-|deleting-<pid>`. At every start the application also
runs it for the `engine` folder beside the executable (the Windows installer's
layout), so a crash between the two renames of an installation does not leave the
application without an engine until the next installation. Two installations of the
same engine folder at once are not supported (the application runs once, and the
Windows installer is one wizard); recovery assumes none runs.
