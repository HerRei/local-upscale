# Architecture Decision Record: 0007 - Model Library and Recipe Resolution

## Status

Accepted and implemented in 0.1.0-beta on top of ADR 0003's workspace.

## Context

The Enhance pane exposed three model decisions at once: a checkpoint dropdown, a
"Restore before upscale" dropdown and a face-aware checkbox. Each new verified checkpoint
lengthened those dropdowns, and Quick/Best were momentary commands, so after clicking one
nothing showed which recipe was active. The frontend preset ranking ignored purposes, so
Best for an illustration picked the photo model. The catalog's `support_tier: labs` carried
two meanings — experimental validation (SeedVR2) and, historically, an unclear license — so
policy questions about presets could not be answered from the data.

## Decision

- **Intent in, instrument out.** The pane asks for a task, a quality (Quick or Best, now a
  persistent selection), what the image contains (Photo or Illustration) and which problems
  to fix first (Noise, JPEG artifacts, Blur, Faces). It then shows one *plan card* naming the
  checkpoint each stage will run, its license, size, hardware fit and install state. The
  dropdowns are gone; "Change…" opens the library.
- **One resolver.** `resolvePlan`, `choosePresetModel` and `chooseFixModel` in
  `desktop/src/lib/state.ts` mirror `presets.py`: exact content matches rank above general
  models; Quick orders by speed then quality, Best by quality then speed; installed models win
  ties. Quick/Best never select a checkpoint whose rights are unresolved or non-commercial
  (`_rights_allow_preset` in `presets.py`, `rightsAllowPreset` in the frontend). Labs remains a
  validation label and does not exclude a model from a preset.
- **Two axes in the catalog.** Schema 2 of `model-catalog.json` adds `rights_status`
  (`verified | attribution | unresolved | non_commercial`), a user-facing `role`, the pipeline
  `stage`, the `fixes` a 1× checkpoint serves, the `content` it is for, and a `display_name`.
  `support_tier` keeps only its validation meaning. All fields are generated from the Python
  catalog by `scripts/export_desktop_catalog.py`, so the worker, the CLI and the desktop
  host read the same facts.
- **Pins.** "Make my Best · Photo" in the library stores `preset_pins["upscale/photo/best"]`.
  A pinned checkpoint is used for that slot until the user changes it; catalog updates never
  switch a pin silently.
- **Downloads fold into Start.** When a stage is missing and the catalog allows LocalSR to
  fetch it, Start reads "Download 288 MB, then upscale", downloads the stages in order (each
  SHA-256 checked by the host) and starts the job without another click. Checkpoints whose
  rights are unresolved keep the explicit user-import flow.
- **The library is a sheet over the workspace.** Groups by what a model does (Upscale photos,
  Illustration, Faces, Fix noise/blur/JPEG, Video (Labs)), plus Installed as the storage view
  with `remove_model`. Only checkpoints that passed the evidence gates in
  `docs/model-licenses.md` are listed. Below 920 px the sheet fills the window.
- **One restoration stage before the upscaler.** The host runs a single preprocess stage, so
  the noise/JPEG/blur chips are exclusive; Faces is an independent companion pass. Chaining
  several 1× stages is a host change and stays out of scope here.
- **Rename.** The *Denoise* task is labelled *Restore* ("Noise, blur, JPEG"); its identifier
  stays `denoise` for settings, recipes, the CLI and the worker protocol.

## Consequences

- Settings gain `quality`, `content`, `fixes` and `preset_pins`; recipes gain `quality`,
  `content` and `fixes`. Both default safely, and settings saved before this change infer the
  active quality from their selection.
- The release preflight (`validate_live_models.py`) still resolves Quick and Best through
  `presets.py`; the rights rule leaves today's choices unchanged (SPAN NomosUni, RealPLKSR
  NomosWebPhoto).
- Estimated time per recipe, sample crops in the library, "Preview on a crop" and a signed
  remote catalog remain later phases of the study.
