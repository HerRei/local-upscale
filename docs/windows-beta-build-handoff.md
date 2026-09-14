# Isolated Windows direct-edition build handoff

The user selected the Mac mini's existing Windows x64 VM on 13 September 2026.
**Builds are paused until the user explicitly says `.12` has concluded and
authorizes the next beta build.** Do not poll, schedule a build, use another host,
or infer approval from elapsed time. The native Intel PC remains the installed acceptance device.
The accepted Store CPU/DirectML MSIX is retained separately. No active `.12`
workflow, checkout or runner may be repurposed.

The remaining direct targets are `windows-x86_64-cpu`,
`windows-x86_64-directml` and `windows-x86_64-cuda`. DirectML needs its own
direct NSIS installer for the website/GitHub launch, followed by the separate
Store MSIX submission. It covers compatible AMD, Intel and NVIDIA adapters;
only Intel UHD 620 has current native Windows acceptance evidence. The source
already contains their backend lock/wheelhouse,
frozen-worker, Tauri/NSIS and split-engine packaging paths. The Store feature
must not be enabled for these direct editions. Use the production public key
from `packaging/updates/production.pub`, backend-specific engine IDs and the
reviewed update feed; keep private signing material off a disposable VM.

Concrete build prerequisites: a separate authorized Windows x64 VM/build machine,
MSVC Build Tools and Windows SDK, Rust, Node, Python 3.11, and disk space for the
selected pinned Torch wheelhouse, frozen engine and packages. Use a task-owned
checkout and caches, with bounded jobs. Run `scripts/backend_wheelhouse.py` for
the exact target, `pip check`, frozen-worker smoke, package architecture checks,
and the retained native acceptance after transfer. Run the NSIS build through
`scripts/build_tauri_preview.py --bundles nsis`; set the corresponding updater
backend/engine identity at compilation.

The Mac mini is an Intel Linux host with an existing QEMU/KVM Windows x64 guest.
The guest currently has 4 virtual CPUs and 3 GB of RAM. The first availability
check found release run `34748267597` building `.12` DirectML on
`macmini-windows-x64`. After the user's explicit go-ahead, check
guest activity and free storage before starting bounded beta builds in a separate
directory. Do not stop services, resize the VM, alter release caches or submit
beta work to the existing runner. If resources are insufficient, report the
measured shortfall rather than using the native acceptance PC as a builder.

[Official Tauri Windows packaging guidance](https://v2.tauri.app/distribute/windows-installer/).

The user chose **unsigned direct EXE/NSIS installers for this beta**, with no
paid Windows code-signing service. Label the downloads accordingly; Windows may
show unknown-publisher/SmartScreen warnings or block them under device policy.
Keep production update signatures and checksums. Microsoft Store signs its
separate MSIX distribution; the acceptance certificate stays test-only.
No signing purchase, build or publication is authorized by this choice.

## Reproducible sequence after explicit authorization

1. Check guest activity, free disk, memory/pagefile and installed Python 3.11 x64,
   Node, Rust/MSVC and Windows SDK tools. The prior VM allocation was 3 GB RAM;
   use one Cargo build job. Stop and report a measured capacity shortfall instead
   of resizing the guest or changing the active runner. Use a new directory such
   as `C:\LocalSR-Beta-Build-20260913`; never a runner `_work` checkout.
2. Transfer the final reviewed source archive and verify its SHA-256 before
   extraction. Record source inventory, exact tool versions and environment in
   the build report. Keep separate `src`, `venv`, `wheelhouse`, `cargo-target`,
   `cargo-cache`, `npm-cache`, `tmp` and `artifacts` folders. Use task-specific
   `CARGO_TARGET_DIR`, `CARGO_HOME`, `PIP_CACHE_DIR`, `npm_config_cache`, `TEMP`
   and `TMP`; do not change `HOME`, `RUSTUP_HOME` or global tool configuration.
3. Bootstrap the isolated venv with a recorded pip supporting `--build-constraint`.
   Run `scripts/backend_wheelhouse.py --target windows-x86_64-directml
   --cache-root <owned-wheelhouse> --report <owned-report.json>`. This verifies
   hashes, installs the target lock and source, and runs `pip check`. Keep the
   wheel inventory and all bundled notices. CPU/CUDA direct editions use their
   own unchanged target locks and separate environments/output directories.
4. Export the catalog, run Python and frontend checks, then freeze with
   `python -m PyInstaller --noconfirm --clean --distpath <owned-worker-dist>
   --workpath <owned-worker-build> packaging/tauri_worker.spec`.
   Run the frozen worker handshake, capability report and CPU image/video smoke;
   verify ONNX, DirectML/provider DLLs and notices are actually included. A VM
   without compatible graphics must not be recorded as physical GPU acceptance.
5. For Store, prepare a manifest preview with `scripts/prepare_windows_msix.py
   --output <new-manifest-dir> --version 1.0.8.0`. This package revision is an
   internal upgrade-test successor to 1.0.7.0, not an approved public version.
   From `desktop`, run `npm ci`, `npm run check`, `npm test`, then
   `npm run tauri -- build --ci --no-bundle --features microsoft-store
   --config <new-manifest-dir>/tauri-store.conf.json`. Verify Store updater
   behavior from that exact compiled binary.
6. Stage the full package with `scripts/prepare_windows_msix.py --output
   <new-layout-dir> --version 1.0.8.0 --app <owned-cargo-target>/release/localsr-next.exe
   --engine <owned-worker-dist>/engine --backend directml`. Run the selected SDK's
   `makeappx pack /d <new-layout-dir>/layout /p <new-unsigned.msix>` without reusing
   an existing output. Keep the unsigned Store upload package. Create a separate
   test-signed copy using the retained acceptance certificate for native testing;
   that test signature is not public Windows trust.
7. For direct CPU/DirectML/CUDA builds, omit `microsoft-store`, embed the reviewed public
   updater key/backend/channel feed/immutable engine identity, and use
   `scripts/build_tauri_preview.py --bundles nsis`. CUDA uses its existing
   `--external-engine-prefix <immutable-prefix>` path; verify every adjacent part
   and the manifest, each under 2 GiB. Public updater signatures are applied
   through the existing Keychain helper, never by copying the private key to the VM.
   Leave Authenticode signing disabled as explicitly requested. Verify the direct
   DirectML binary uses direct updates and the separate Store binary uses Store
   updates, despite sharing the backend and model scope.

## Native acceptance of the next package

Preserve the complete 1.0.7.0 package, test certificate and installation/upgrade
reports. Before replacing it, make an independent verified backup of settings,
recipes, model hashes and the logical queue, including SQLite WAL data.

- Install the new test-signed MSIX as an **upgrade**; verify package identity,
  version, installed worker dependencies and every preserved data category.
- On the unlocked native desktop, test images and SDR/HDR-compatible video,
  audio/play/seek, result A → running B → A switching, source/enhanced sliders,
  an unseen running queue item, denoise and upscale tiles, partial edges and
  current/next/whole-queue ETA. Explicitly observe external Open/Reveal.
- Repeat CPU and UHD 620 benchmark v2.1 separately; verify score/device labels,
  consistency gate and real tile previews. Recapture the benchmark Store image.
- Cancel during model preparation, active tiles, video and export; verify worker
  exit, scratch cleanup and successful next jobs. Exercise corrupted media and a
  bounded memory/output failure. Check the final NAFNet policy only after approval.
- Verify Store update controls do not invoke the direct updater. For direct
  editions, use production-signed compatible updates and test interruption,
  invalid signatures, insufficient space, install/restart, data preservation and
  failed-startup recovery. Do not infer a native installer rollback from the
  Linux portable-host rollback test.
- Run WACK on the new installed package and retain full XML/HTML logs. The
  earlier WARNING/DPI COM failure remains unresolved until that run establishes
  otherwise. Perform uninstall/reinstall recovery only with an independent
  verified backup: the prior test observed MSIX profile deletion on uninstall.
- Record exact hashes, final screenshots and outcomes; restore the accepted
  baseline if the candidate fails. Remove only owned disposable test files.

None of the commands above has been used to build the new candidate during the
pause. Source-worker tests do not fulfill these installed-package gates.
