**LocalSR Microsoft Store package preparation**

The user supplied the reserved Store product identity on 12 September 2026.
It is recorded in [identity.json](../packaging/windows/msix/identity.json):

| Field | Value |
| --- | --- |
| Package name | `HerRei.LocalSR` |
| Publisher | `CN=4A2AE7A2-7D02-49CF-8F16-C9209C756516` |
| Publisher display name | `HerRei` |
| Package family name | `HerRei.LocalSR_tvsg0jvwy7150` |
| Store ID | `9NTG848ZQTCQ` |

The publisher-derived family suffix matches the screenshot. These identifiers
are package metadata, not signing credentials. The Store product is a draft;
this repository does not assert that it has passed certification or is live.

**Local preparation**

[prepare_windows_msix.py](../scripts/prepare_windows_msix.py) creates a new
staging directory with a desktop x64 manifest, existing LocalSR artwork in the
required Store sizes, license notices and a separate Tauri Store overlay. With
binary inputs, it stages the complete frozen engine beside `localsr-next.exe`.
It rejects mismatched native architectures, missing runtime files, bundled
model checkpoints and attempts to overwrite an existing output directory.
Its report explicitly distinguishes preparation from Windows runtime testing.

Local verification: **18 packaging tests passed**, plus a real command-line
manifest preview and checks of the generated XML, identity and Store icon sizes.
Architecture/error-path tests use binary fixtures; they do not execute a Windows
worker. Temporary verification files were removed. No native MSIX binary was
built, installed, uploaded or certified by these local checks.

To inspect the manifest without Windows binaries:

```sh
python scripts/prepare_windows_msix.py --output build/store-manifest-preview --version 1.0.0.0
```

`1.0.0.0` is an example Store package version, not a change to LocalSR's release
version. Choose the actual beta package version before a native candidate build.
The Store's four-part version must start above zero and end in `.0`; keep the
mapping to LocalSR's application version documented. The manifest currently
uses Windows Desktop build 19041 as its compatibility floor. This setting does
not constitute installed acceptance on that OS build.

**Windows build and packaging steps**

Use an isolated Windows checkout and output directories; do not reuse or alter
the `.12` release checkout, runner, VM or build artifacts. Tauri currently builds
EXE/MSI installers, so an MSIX requires the separate Microsoft SDK packaging step.

1. Install the selected, pinned Windows engine dependencies and freeze the worker
   using `packaging/tauri_worker.spec`. Use a dedicated `--distpath` and `--workpath`.
   Preserve the entire `engine` directory, including `_internal`.
2. Build the Tauri host with `npm run tauri -- build --no-bundle --config
   <absolute-path-to-tauri-store.conf.json>`, from `desktop`. Set a dedicated
   `CARGO_TARGET_DIR`. Explicitly clear `LOCALSR_UPDATE_PUBLIC_KEY` before compiling
   this candidate. The Store overlay disables Tauri updater artifacts; the runtime
   update controls still need adaptation for Store-managed updates before release.
3. Stage those exact binaries. For example, from the repository root in PowerShell:

```powershell
python scripts/prepare_windows_msix.py `
  --version 1.0.0.0 --backend cpu `
  --app C:\LocalSR-Store\cargo\release\localsr-next.exe `
  --engine C:\LocalSR-Store\worker-dist\engine `
  --output C:\LocalSR-Store\candidate-1
```

4. From a Windows SDK developer shell, package the layout using its normal schema
   validation; do not pass `/nv` to skip validation:

```powershell
MakeAppx.exe pack /d C:\LocalSR-Store\candidate-1\layout /p C:\LocalSR-Store\candidate-1\LocalSR.msix /h SHA256
```

5. Register the layout in an isolated Windows test environment or test a locally
   signed test package, then run the Windows App Certification Kit and actual
   application acceptance. Local test signing does not provide Store signing.
6. Upload the tested MSIX to the existing product's Packages page only after the
   beta candidate is reviewed and publication/submission is authorized. Microsoft
   re-signs Store MSIX packages after certification; a paid CA certificate is not
   required for this route.

`--backend` records which worker was supplied; static PE checks do not prove that
its PyTorch backend works. A single x64 Store identity does not automatically
select CPU versus CUDA/DirectML packages according to GPU vendor. CPU, DirectML
(including compatible Intel integrated graphics) and CUDA are retained in the
agreed beta scope, with [testing coverage/Labs labels](beta-platform-matrix.md).
Implement the engine delivery strategy before uploading alternative x64 builds
of this product. The CPU example above is one worker layout; it does not add
DirectML to a CPU engine. [Intel GPU support](intel-gpu-support.md).

**Acceptance still required**

- Fresh installation with the complete engine and a usable WebView2 runtime.
- App launch, CPU inference and any advertised GPU backend on the actual package.
- Model download/import, video import/export, cancellation and benchmarks.
- Store-managed updates and the corresponding update UI; prevent direct updater
  or cached engine overrides from replacing the Store-managed engine.
- Existing settings/recipes/models when moving from the unpackaged app, plus
  persistence across a Store update. Verify MSIX data virtualization and uninstall
  behavior explicitly; the staging tool does not change data locations.
- Installation on another drive, Windows accessibility and the proposed hardware
  requirements where advertised.
- Review `runFullTrust` in certification notes: LocalSR's desktop UI launches its
  local inference worker and accesses user-selected media as the signed-in user.
- Model/license decisions, listing/privacy text, certification and final release
  readiness. None of these gates are marked passed by manifest preparation.

The remaining work is tracked in the [beta checklist](beta-release-checklist.md).
Local [listing text, reviewer instructions and the Windows screenshot plan](beta-store-submission.md)
are now prepared, along with a [privacy/support draft](beta-privacy-and-support.md).
The contact is confirmed as `hermes.reisner@gmail.com`, with GitHub Issues for bug
reports. The drafts still need publisher details and engine delivery; no Store field,
public website or installed package was changed by preparing these materials.
Sources: [Microsoft manual packaging](https://learn.microsoft.com/en-us/windows/msix/desktop/desktop-to-uwp-manual-conversion),
[MakeAppx](https://learn.microsoft.com/en-us/windows/msix/package/create-app-package-with-makeappx-tool),
[Store package requirements and signing](https://learn.microsoft.com/en-us/windows/apps/publish/publish-your-app/msix/app-package-requirements),
and [Tauri's current packaging support](https://v2.tauri.app/distribute/microsoft-store/).
