# Updates

## In the app

**About → Update LocalSR** lets you choose the Stable or Beta channel, check for
a compatible release, read its notes and total download size, download it, and
install and restart. The beta also checks its feed once at launch and shows a
notice when a newer build exists. Downloads can run while jobs are processing;
installation waits until the queue, any cancellation and any model download have
finished, and the native host enforces that as well as the interface.

The Microsoft Store edition receives application and engine updates from the
Store; its in-app updater is disabled.

### Anonymous update-check count

After a successful check, direct builds send one request to
`https://macmini-ci.tail34a4e0.ts.net/ping/update-check?v=<version>&t=<platform key>&c=<channel>`,
for example `v=0.1.2-beta&t=darwin-aarch64-mps-native&c=beta`. It carries no
install or user ID and no cookies, times out after five seconds and never affects
the check. The Mac mini counts active installs per day without storing the IP
address (see `packaging/hosting/stats/`). Turn it off with **Send an anonymous
update-check count** in the update dialog (setting `anonymous_update_count`).
Store editions and loopback test feeds never send it; `LOCALSR_USAGE_PING` points
a build at another HTTPS endpoint.

## Build identity

Each build carries its update configuration at compile time; changing a value
means rebuilding the Rust host.

| Variable | Meaning |
| --- | --- |
| `LOCALSR_UPDATE_PUBLIC_KEY` | Updater public key in the format written by `tauri signer generate`; never the private key |
| `LOCALSR_UPDATE_FEED` | HTTPS directory containing `stable.json` and `beta.json` |
| `LOCALSR_UPDATE_BACKEND` | The package's engine: `cpu`, `cuda`, `rocm`, `mps`, … |
| `LOCALSR_ENGINE_ID` | Immutable identity of the bundled engine build |
| `LOCALSR_UPDATE_KIND` | `native` for Tauri updater artifacts, `portable` for the unpacked Linux host |

The platform key in a feed is `<os>-<architecture>-<backend>-<kind>`, for example
`darwin-aarch64-mps-native` or `linux-x86_64-rocm-portable`. The Stable channel
rejects prereleases, and both channels reject entries whose backend, package kind
or worker protocol do not match the installed build. The public beta feed is
`https://herrei.github.io/localsr/updates/beta.json`.

## Feed format

The feed is Tauri's manifest format with a `localsr` object inside each platform
entry:

```json
{
  "version": "0.1.1-beta",
  "notes": "What changed and what to watch out for.",
  "platforms": {
    "darwin-aarch64-mps-native": {
      "url": "https://downloads.example.org/LocalSR-v0.1.1-beta-macOS-arm64.app.tar.gz",
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

Sign an archive with `python scripts/beta_update_signing.py sign <archive>`; it
obtains the private key through the Keychain helper and never writes it to disk.
Serve immutable files over HTTPS and publish an entry only after installing it
from the feed on a test machine.

A portable Linux update is a signed `.tar.gz` containing only `localsr-next`,
built with the embedded frontend. It replaces that executable and nothing else;
native libraries and the worker come from the installation or an engine package.

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
backend, file count, unpacked size and per-part checksums. Every part and the
manifest are downloaded and signature-checked individually; extraction verifies
checksums, paths, file counts and free disk space. Parts must stay below 2 GiB.
`unpacked_size` reserves room for the app and the engine together, and a
protocol handshake must pass before the new engine is activated. On Windows the
NSIS hook accepts the updater's managed-engine flag, so a CUDA update never asks
the user to locate payload parts by hand.

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

A portable Linux update runs a startup smoke check before removing the previous
host and restores it if the check fails. On macOS and Windows the platform
installers handle replacement; their failure paths are covered by the
installed-package checks in [Testing](testing.md).

## Testing the updater locally

Debug builds accept `LOCALSR_TEST_UPDATE_CONFIG` with a loopback feed, a
disposable key, a backend, an engine id and a kind, so two local builds can
exercise the whole flow without touching the public feed. Release builds ignore
the override and require HTTPS. The regression suite covers channel, backend and
protocol matching, missing engine payloads, invalid signatures with a recomputed
checksum, interrupted transfers, insufficient disk space, settings preservation
and startup rollback. The production candidate test downloads a real signed
archive through the app's own downloader and checks that truncation and
substituted content are rejected.
