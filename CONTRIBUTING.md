# Contributing to LocalSR

LocalSR restores images and video on the user's computer. Changes should keep
processing observable, cancellation reliable and source files intact.

## Get started

The current interface is in [`desktop/`](desktop/README.md). It uses Svelte/Tauri
and a Python worker. The optional Qt Widgets client remains available with `--legacy`.
Use Python 3.11 and install the native dependencies listed in the desktop guide.

From the repository root:

```sh
./local-ci.sh setup
./local-ci.sh
```

See [development checks](docs/development.md) for individual commands and the
current type-checking boundary. The default Python suite runs headlessly.
Real-model downloads, physical GPU tests and installed-package checks are
separate acceptance steps.

## Working on a change

Keep a change focused enough to review. Describe the problem, resulting behavior
and the checks you ran. Add a regression test for a reproducible bug; visual-only
changes can use a checked screenshot and a short explanation.

Run the desktop app for interface changes and test the relevant installed package
when changing native integration, worker startup or bundled dependencies. Report
which operating system, device and driver you actually tested. Do not infer broad
GPU support from a CPU smoke test.

## Code boundaries

- Keep PyTorch and Spandrel in the inference worker. Worker failure must not take down the GUI.
- Keep worker stdout as JSON Lines; diagnostics belong on stderr.
- Route native operations through Rust, with access limited to the requested files.
- Preserve cancellation, atomic output replacement and temporary-file cleanup.
- Validate model sizes and SHA-256 hashes before deserialization.
- Keep resource limits enabled and display uncertainty in estimates.
- Keep model downloads optional and preserve each author's terms and attribution.

Prefer names and comments that explain a constraint or a non-obvious choice.
Remove obsolete branches and unused code when their replacements are verified.
Avoid broad refactors in a bug fix. Third-party vendored code and its modification
notices must retain their provenance; do not reformat it with application code.

Do not commit checkpoints, private media, runtime profiles, signing material,
virtual environments or local acceptance artifacts. Required, fixed SeedVR2
conditioning assets are explicitly listed in the package configuration.

## Reports and proposals

Use [hermes.reisner@gmail.com](mailto:hermes.reisner@gmail.com) while the public
beta tracker is being prepared. Send security reports privately as described in
[SECURITY.md](SECURITY.md). For a proposal, explain the use case and the effect on
processing time, memory, output quality and platform support where relevant.
