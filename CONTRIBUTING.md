# Contributing

LocalSR restores images and video on the user's own computer. Changes should keep
processing observable, cancellation reliable and source files untouched.

## Getting started

The app lives in [`desktop/`](desktop/README.md) (Svelte + Tauri) with a Python
inference worker in [`src/localsr/`](src/localsr). You need Python 3.11, Node.js
and a stable Rust toolchain. From the repository root:

```sh
./local-ci.sh setup
./local-ci.sh
```

`setup` installs the development dependencies; the second command runs every
check that works on a development machine. [Development](docs/development.md)
lists the individual steps. Model downloads, GPU runs and installed-package
checks are separate; see [Testing](docs/testing.md).

## Making a change

Keep a change small enough to review. In the pull request, describe the problem,
the resulting behaviour and the checks you ran. Add a regression test for a
reproducible bug. For visual changes, attach a screenshot.

Run the desktop app for interface changes, and test the installed package when
you touch native integration, worker startup or bundled dependencies. Say which
operating system, GPU and driver you used; a CPU smoke test says nothing about
GPU support.

## Boundaries to keep

- PyTorch and Spandrel stay in the inference worker. A worker failure must not
  take down the app.
- Worker stdout is JSON Lines; diagnostics go to stderr.
- Native operations go through Rust, with access limited to the files the user
  chose.
- Output files are replaced atomically; temporary files are cleaned up; jobs can
  be cancelled at any point.
- Model files are verified by size and SHA-256 before deserialization.
- Model downloads stay optional, and each author's terms and attribution stay
  visible.

Write comments that explain a constraint or a non-obvious choice. Remove code
when its replacement is verified rather than leaving both. Vendored third-party
code keeps its provenance and formatting.

Do not commit checkpoints, media, runtime profiles, signing material, virtual
environments or local test artifacts.

## Reports and proposals

Use [GitHub Issues](https://github.com/HerRei/local-upscale/issues) for bugs and
feature requests, and [SECURITY.md](SECURITY.md) for vulnerabilities. For a
proposal, explain the use case and its effect on processing time, memory, output
quality and platform support.
