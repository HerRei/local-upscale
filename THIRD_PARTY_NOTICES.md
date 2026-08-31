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
- **SeedVR2 video integration** — vendored adapter code is covered by the included
  `src/localsr/video_models/seedvr2/NOTICE.md` and `vendor/LICENSE`; model bundles remain external.
- **Curated checkpoints** — each catalog entry shows its author, source, and license before
  download. CC-BY checkpoints require attribution. The HAT-S Face checkpoint is
  CC BY-NC-SA 4.0 and non-commercial only. The Best and anime upstream pages use the non-standard
  identifier `CC-BY-0.4`; LocalSR treats their commercial terms as unclear pending clarification.

Review upstream license files and current terms before redistributing LocalSR or using outputs in a
commercial workflow.
