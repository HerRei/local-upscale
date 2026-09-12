**LocalSR updates — local preview implementation**

The About area now has **Update LocalSR**: choose Stable or Beta, check for a compatible release, review its version, notes and total download size, download, then install and restart. Downloads may run during processing. Installation waits for the queue, cancellation and model downloads to finish. The native host enforces this as well as the interface.

Public updates remain disabled in this checkout. No release workflow, published manifest, signing credential or `.12` release was changed. The first beta still requires an initial installation containing the verification key and correct build identity.

**Build identity and release preparation**

Build the application with these compile-time variables (rebuild the Rust host when changing them):

| Variable | Meaning |
| --- | --- |
| `LOCALSR_UPDATE_PUBLIC_KEY` | Tauri updater public key, in the format written by `tauri signer generate`; never the private key |
| `LOCALSR_UPDATE_FEED` | HTTPS directory containing `stable.json` and `beta.json` |
| `LOCALSR_UPDATE_BACKEND` | Exact package backend, such as `cpu`, `cuda`, `rocm` or `mps` |
| `LOCALSR_ENGINE_ID` | Immutable engine build identity, using letters, digits, hyphens or underscores |
| `LOCALSR_UPDATE_KIND` | `native` for Tauri updater artifacts, or `portable` for the unpacked Linux host |

The target key is `<os>-<architecture>-<backend>-<kind>`, for example `linux-x86_64-rocm-portable` or `darwin-aarch64-mps-native`. Stable rejects prereleases. Both channels reject backend, package-kind and worker-protocol mismatches. Keep all application version files synchronized for a real release.

Enable Tauri's `bundle.createUpdaterArtifacts` in a **separate future beta build configuration** and provide its signing key through a protected environment/secret store. macOS uses signed updater app archives, Windows uses updater installers, and Linux native packages use AppImages. Updater signatures and platform code signing/notarization are separate requirements. See the [official Tauri updater documentation](https://v2.tauri.app/plugin/updater/).

A portable Linux update is a signed `.tar.gz` containing `localsr-next`, built with the embedded frontend (`tauri/custom-protocol`). It replaces that executable only. Native libraries and the inference worker must be provided through a compatible installation or an engine package; this archive cannot silently update arbitrary application files.

A static feed uses Tauri's manifest format with an additional `localsr` contract inside each platform entry:

```json
{
  "version": "0.0.13-beta.1",
  "notes": "Describe the actual changes and known limitations.",
  "platforms": {
    "linux-x86_64-rocm-portable": {
      "url": "https://downloads.example.org/localsr-rocm.tar.gz",
      "signature": "BASE64_TAURI_SIGNATURE",
      "localsr": {
        "channel": "beta",
        "backend": "rocm",
        "kind": "portable",
        "protocol": 1,
        "engine_id": "immutable-engine-build-id",
        "size": 123456,
        "unpacked_size": 456789,
        "sha256": "REPLACE_WITH_THE_EXACT_64_CHARACTER_SHA256"
      }
    }
  }
}
```

The example is deliberately not an enabled feed. Sign the actual archive using `tauri signer sign -f <private-key-file> <archive>` and copy the generated signature into the manifest. Do not commit private keys. Serve immutable files over HTTPS and publish only after testing those exact files.

**Separate inference engine**

An unchanged engine is adopted into `LocalSR/next/engines/<id>/engine` using hard links where possible, with copying as the fallback. Models remain in `LocalSR/models`. A new engine ID requires an `engine` object in the release contract:

```json
{
  "id": "new-engine-id",
  "manifest": { "name": "engine-payload.json", "url": "https://downloads.example.org/engine-payload.json", "size": 1234, "sha256": "EXACT_SHA256", "signature": "TAURI_SIGNATURE" },
  "parts": [
    { "name": "worker.engine.tar.gz.part001", "url": "https://downloads.example.org/worker.engine.tar.gz.part001", "size": 123456, "sha256": "EXACT_SHA256", "signature": "TAURI_SIGNATURE" }
  ]
}
```

The payload manifest uses the existing `schema_version: 1`, `format: tar.gz.parts`, backend, file count, unpacked bytes and part checksums. Every part and the manifest are individually downloaded and signature verified; extraction also checks checksums, paths, file counts and disk space. Parts must be smaller than 2 GiB. Use `unpacked_size` to reserve room for the application and engine together. A protocol handshake must pass before activating the new engine. The Windows NSIS hook accepts the updater's managed-engine flag, so a CUDA update does not ask users to find adjacent payload parts manually.

**Data and failure recovery**

Before installation, settings and recipes are saved, the queue database is backed up including WAL contents, and profile files are copied to `LocalSR/next/backups`. A version marker makes the next application version back up existing data before opening/migrating its database. Downloaded checkpoints are not moved or removed.

Unreadable or newer-format settings cannot be overwritten by a later normal save. The interface offers explicit recovery: preserve the original as a recovery copy and start with defaults. Recipes in that preserved file can be recovered manually. Recovery never deletes model files.

Downloads stream to partial files and must pass size, SHA-256 and Minisign verification. Cancelling or interrupting a transfer cannot make it installable. Complete verified files can be reused after another check. Installation verifies them again and checks free disk space. A Linux portable update runs a startup smoke check before removing the previous host; a failed check restores the previous host and restarts its worker. User-service launches restart through their exact systemd unit.

The Linux portable rollback check does not establish automatic rollback after every possible macOS/Windows installer failure. Those native signed upgrade and uninstall paths still require release-candidate acceptance on their actual platforms.

**Local acceptance**

Debug builds alone allow `LOCALSR_TEST_UPDATE_CONFIG` with a loopback-only feed, public key, backend, engine ID and kind. A disposable key and two local versioned builds exercise the complete UI flow without enabling a public updater. Release builds ignore that runtime override and require HTTPS.

Regression tests cover backend/channel/protocol matching, missing engine payloads, invalid signatures (even with a recomputed checksum), interrupted/cancelled transfers, insufficient disk space, settings preservation and startup rollback. Exact native upgrade results belong in the accompanying platform acceptance report. No private test signing key is retained after acceptance.
