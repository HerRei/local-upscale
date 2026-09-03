# Third-party notices

LocalSR application source is MIT licensed. Dependencies and optional model downloads retain their
own terms; this file is a distribution notice, not a replacement for their license texts.

- **Slint 1.9.2** — GPLv3, Slint royalty-free/community, or commercial licensing options. Official
  LocalSR builds use the royalty-free/community attribution path and render Slint's official
  `AboutSlint` widget in the application.
- **LocalSR Next Preview host** — Tauri 2 is MIT/Apache-2.0 and Svelte is MIT. Transitive
  components retain their upstream permissive or file-level terms (including Apache, BSD, ISC,
  MIT, MPL, Unicode, and Zlib terms), while their versions are locked by Cargo and npm. The
  preview uses the operating system's webview rather than bundling a browser engine.
- **PyTorch / TorchVision** — BSD-style licenses from the PyTorch project.
- **Spandrel** — MIT license.
- **OpenCV 4.10 headless runtime** — Apache-2.0; used only by the optional CPU face detector.
- **YuNet 2023mar face detector** — MIT, copyright Shiqi Yu; downloaded on demand from the
  OpenCV Zoo with an exact size and SHA-256 rather than bundled in the application.
- **SeedVR2 video integration** — vendored adapter code is covered by the included
  `src/localsr/video_models/seedvr2/NOTICE.md` and `vendor/LICENSE`; model bundles remain external.
- **Curated checkpoints** — each catalog entry shows its author, source, and license before
  download. CC-BY checkpoints require attribution. The official Real-ESRGAN ×2 project/asset is
  recorded as BSD-3-Clause and the official FBCNN project/model-zoo asset as Apache-2.0. The HAT-S
  Face checkpoint's separate weight/training-data rights could not be verified, so LocalSR does not
  auto-download or commercially recommend it. The Best and anime upstream pages use the
  non-standard identifier `CC-BY-0.4`; LocalSR treats their commercial terms as unclear pending
  clarification. Exact evidence and digests are in `docs/model-licenses.md`.

Review upstream license files and current terms before redistributing LocalSR or using outputs in a
commercial workflow.
