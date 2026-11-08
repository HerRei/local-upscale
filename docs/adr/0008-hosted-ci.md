# Architecture Decision Record: 0008 - GitHub-hosted PR CI

## Status

Accepted on 2026-11-05 by the repository owner.

## Context

PR CI shared self-hosted runner capacity with release builds. GitHub-hosted
runners isolate PR workloads and allow independent jobs to run concurrently.
Apple Silicon runners also match the shipped macOS arm64 target.

## Decision

- Run `ci.yml` lint and Linux tests on `ubuntu-24.04`, Windows tests on
  `windows-2022`, and retain hosted `macos-latest` for Apple Silicon tests.
- Move both Preview matrices' Linux and Windows entries to those same hosted
  images, keeping the existing macOS verification entry. Lint, platform tests
  and Preview verification run in parallel. Optional private packaging remains
  manual and waits for the verification matrix.
- Remove release-priority coordination, persistent scratch/storage guards and
  self-hosted cleanup from these two workflows. Use the checkout and
  `RUNNER_TEMP`, with fresh Python 3.11 venvs and no global site-packages.
- Follow the existing hosted release workflows for image cleanup and native
  prerequisites, Python and Rust setup; retain the existing action SHAs, lint
  pins and test commands. Linux and Windows install the pinned CPU PyTorch
  wheels from the CPU index before project extras. Disable pip caches and keep
  Cargo output local to each ephemeral job; add no build caches.
- Install Ubuntu's `ffmpeg` in Linux tests to retain external-FFmpeg coverage.
  Keep platform and codec skips, signing requirements, release isolation and
  the seven-day private-preview artifact retention unchanged.

## Consequences

`desktop-release.yml` remained manual-only with self-hosted Linux/Windows legs
and required provisioned runners. Releases did not use it: Linux and Windows
packages come from `linux-appimages.yml` and `windows-installers.yml`, the Mac
build from `scripts/release_macos_beta.sh`. It was removed in 0.3.1 together
with `cleanup-runner.yml`, `macos-runner-maintenance.yml`, the runner
provisioning in `infra/`, the coordination, scratch, storage and artifact-server
scripts and their tests. Every workflow now runs on GitHub-hosted runners.

Hosted disks are finite. Linux test and Preview jobs free unused SDKs and
Docker images using the existing hosted release cleanup. CPU wheels avoid CUDA
dependencies, pip downloads are not cached, and venvs/build trees disappear
with the runner. Cold Rust builds and optional Linux media-runtime/package
builds may still approach disk or time limits. Preview retains its 150-minute
verification and 240-minute packaging limits; Python CI uses GitHub's default
job limit.
