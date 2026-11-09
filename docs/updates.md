# Updates

## In the app

Every direct download updates itself: the macOS app, the Windows CPU, DirectML and
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
- **Windows:** the signed NSIS installer is the update. LocalSR records the
  installed version in the profile backup and removes its downloads, then the Tauri
  updater starts the installer in passive mode with `/UPDATE` and
  `/LOCALSR_ENGINE_MANAGED=1` and LocalSR exits; the installer starts LocalSR again.
  The CUDA installer's NSIS hook sees the flag and skips its own engine installation
  from adjacent parts, because the update has already installed the engine package.
- **Linux AppImage:** the signed AppImage is the update. LocalSR replaces the file
  named by `APPIMAGE` itself, streaming from disk (the Tauri updater would read the
  whole AppImage into memory): it puts the verified download next to the AppImage,
  swaps it in, runs the new image's `--headless-smoke-test`, and restores the
  previous image if that check fails. The running image stays mounted until the
  restart. A copy that does not run from its AppImage (no `APPIMAGE`, or the host
  outside `APPDIR`) refuses the update, and the AppImage's folder must be writable.
- **Portable Linux host** (`portable` kind): see [Feed format](#feed-format).

### Anonymous update-check count

After a successful check, direct builds send one request to
`https://macmini-ci.tail34a4e0.ts.net/ping/update-check?v=<version>&t=<platform key>&c=<channel>`,
for example `v=0.1.6-beta&t=darwin-aarch64-mps-native&c=beta` or
`t=windows-x86_64-cuda-native`. It carries no install or user ID and no cookies,
times out after five seconds and never affects the check. The Mac mini counts active
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
key from `packaging/updates/production.pub` and makes the engine id
`<target>-<first 12 characters of the commit>`; `release_macos_beta.sh` sets the
same values for macOS. The installed host's `--headless-smoke-test` report states
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
`gh run download` leaves them (one folder per release target), checks each package
against its checksum file, its installed smoke report (version and update identity)
and its engine package, signs the package, its parts and its engine package, and
adds the entries next to the macOS entry. To sign a split AppImage it joins the
parts in a temporary folder and deletes the joined copy afterwards.
`python scripts/verify_prepared_release.py --feed build/release-<version>/beta.json
--commit <sha>` then checks that the feed has exactly one entry per release target
with the right backend, kind, protocol and engine id, an engine package, signed
files below the size limit and parts that add up. Serve immutable files over HTTPS
and publish an entry only after installing it from the feed on a test machine.

A portable Linux update is a signed `.tar.gz` containing only `localsr-next`,
built with the embedded frontend. It replaces that executable and nothing else;
native libraries and the worker come from the installation or an engine package.
No published edition uses it.

## Engine payloads

The inference engine is versioned separately from the app. An unchanged engine
is adopted into `LocalSR/next/engines/<id>/engine` with hard links where
possible. When a release changes the engine id, its entry carries an `engine`
object:

```json
{
  "id": "new-engine-id",
  "manifest": { "name": "engine-payload.json", "url": "…", "size": 1234, "sha256": "…", "signature": "…" },
  "parts": [
    { "name": "worker.engine.tar.gz.part001", "url": "…", "size": 123456, "sha256": "…", "signature": "…" }
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

Engine ids follow the release commit, so every release entry carries an engine
package. The Windows and Linux workflows write it next to the package with
`scripts/prepare_engine_payload.py --target <target>`: from the bundled engine, or
for Windows CUDA by adopting the installer's adjacent parts, which already have
this format. An update therefore downloads the package and the engine: for a Linux
CUDA or ROCm AppImage about twice the AppImage's size.

## Verification and recovery

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
before removing the previous one and restores it if the check fails. On macOS and
Windows the platform installers handle replacement; their failure paths are covered
by the installed-package checks in [Testing](testing.md).

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
   platform key whose `engine_id` differs from the config's, serve the folder with
   `python3 -m http.server --bind 127.0.0.1`, and start the older build with
   `LOCALSR_TEST_UPDATE_CONFIG` pointing at a config with the test public key.

Release builds ignore the override and require HTTPS. The regression suite covers
channel, backend and protocol matching, missing engine payloads, invalid
signatures with a recomputed checksum, interrupted transfers, split packages,
insufficient disk space, settings preservation, the AppImage swap and startup
rollback. The production candidate test downloads a real signed archive through
the app's own downloader and checks that truncation and substituted content are
rejected.
