# Microsoft Store package

LocalSR has a reserved Store listing. Its identifiers are package metadata, not
signing material, and are recorded in
[`packaging/windows/msix/identity.json`](../packaging/windows/msix/identity.json):

| Field | Value |
| --- | --- |
| Package name | `HerRei.LocalSR` |
| Publisher | `CN=4A2AE7A2-7D02-49CF-8F16-C9209C756516` |
| Publisher display name | `HerRei` |
| Package family name | `HerRei.LocalSR_tvsg0jvwy7150` |
| Store ID | `9NTG848ZQTCQ` |

The listing is a draft. Nothing has been submitted, and this repository makes no
claim about certification.

## Building the package

Tauri produces EXE and MSI installers, so the MSIX is assembled with the Windows
SDK from a Tauri build and a frozen worker. Use a dedicated checkout and output
directories.

1. Install the pinned Windows engine dependencies and freeze the worker with
   `packaging/tauri_worker.spec`, using a dedicated `--distpath` and `--workpath`.
   Keep the whole `engine` directory, including `_internal`.
2. Build the host from `desktop/` with
   `npm run tauri -- build --no-bundle --config <absolute path to tauri-store.conf.json>`,
   with a dedicated `CARGO_TARGET_DIR`, `LOCALSR_UPDATE_PUBLIC_KEY` unset and the
   `microsoft-store` Cargo feature enabled. The Store overlay disables the
   updater artifacts, and the runtime refuses direct updater or cached engine
   overrides in this edition.
3. Stage the binaries:

   ```powershell
   python scripts/prepare_windows_msix.py `
     --version 1.0.0.0 --backend cpu `
     --app C:\LocalSR-Store\cargo\release\localsr-next.exe `
     --engine C:\LocalSR-Store\worker-dist\engine `
     --output C:\LocalSR-Store\candidate-1
   ```

   The script writes a desktop x64 manifest, the artwork in the required sizes,
   the license notices and the Tauri Store overlay. It rejects mismatched
   architectures, missing runtime files, bundled model checkpoints and an
   existing output directory. Without binaries it still produces a manifest
   preview: `python scripts/prepare_windows_msix.py --output build/store-manifest-preview --version 1.0.0.0`.
4. Package with schema validation on (do not pass `/nv`):

   ```powershell
   MakeAppx.exe pack /d C:\LocalSR-Store\candidate-1\layout /p C:\LocalSR-Store\candidate-1\LocalSR.msix /h SHA256
   ```

5. Register the layout or a locally test-signed package on a test machine, run
   the Windows App Certification Kit and the [manual acceptance](testing.md)
   cases. Local test signing is not Store signing.
6. Upload to the product's Packages page. Microsoft signs the package after
   certification, so no paid certificate is needed for this route.

The Store's four-part version must start above zero and end in `.0`; document
its mapping to the LocalSR version. The manifest sets Windows Desktop build
19041 as the compatibility floor. One x64 identity cannot select CPU, DirectML or
CUDA workers by GPU vendor, so the engine delivery route has to be decided
before uploading alternative builds of the same product.

Installed upgrades between test versions preserved settings, recipes, downloaded
models and the queue. Uninstalling the package can delete its data folder
(`%LOCALAPPDATA%\Packages\HerRei.LocalSR_tvsg0jvwy7150\LocalCache\Local\LocalSR`),
so back it up first; models in the shared `LocalSR/models` folder are unaffected.

## Before submission

- Rebuild the worker on the royalty-free media runtime (see
  [Platforms](platforms.md)); the test builds so far used a different runtime.
- Resolve the remaining WACK warning and run the acceptance cases on the final
  package.
- Verify the Microsoft-signed installation and update after certification.
- Check installation on another drive and Windows accessibility.
- Review `runFullTrust` in the certification notes: the app launches its local
  inference worker and reads media the user selected.
- Publish the privacy and support pages the listing links to.

References: [manual packaging](https://learn.microsoft.com/en-us/windows/msix/desktop/desktop-to-uwp-manual-conversion),
[MakeAppx](https://learn.microsoft.com/en-us/windows/msix/package/create-app-package-with-makeappx-tool),
[Store package requirements](https://learn.microsoft.com/en-us/windows/apps/publish/publish-your-app/msix/app-package-requirements),
[Tauri and the Microsoft Store](https://v2.tauri.app/distribute/microsoft-store/).
