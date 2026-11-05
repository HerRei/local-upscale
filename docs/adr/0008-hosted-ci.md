# Architecture Decision Record: 0008 - GitHub-hosted PR CI

## Status

Accepted on 2026-11-05 by the repository owner.

## Context

The self-hosted CI setup dates from the alpha, when the owner's Intel Mac mini
ran Linux cross-builds and an on-demand Windows VM. One runner slot meant PRs
queued behind one another and releases. The release-priority coordinator,
storage guard and `/ci-scratch` SSD workspace managed that shared host's limits.

In September 2026, Linux AppImage and Windows installer release builds moved to
GitHub-hosted runners, as did macOS tests. Hosted Apple Silicon also matches the
shipped arm64 target, unlike the mini's Intel macOS guest.

The 2026-11-05 review found 23 stale Dependabot PRs whose red checks were mostly
storage-guard failures and coordinator timeouts. Every same-repository PR,
including Dependabot dependency code, ran on a Linux runner account with
passwordless sudo and Docker group membership: root-equivalent access on the
same machine as the owner's only photo library. GitHub-hosted runners are free
for public repositories such as `HerRei/local-upscale`. The owner decided to
move PR CI off the mini and retire its runner.

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

After this merges, the mini runner and its Windows VM are retired on the host
side. This repository change does not stop or unregister them itself.
`desktop-release.yml` stays manual-only with self-hosted Linux/Windows legs and
is a candidate for removal; using it after retirement requires reprovisioning.
It, `cleanup-runner.yml`, `macos-runner-maintenance.yml`, and the shared
coordination/scratch scripts and their tests remain unchanged. PR checks no
longer need the mini or release-priority scheduling, and independent jobs can
run concurrently.

Hosted disks are finite. Linux test and Preview jobs free unused SDKs and
Docker images using the existing hosted release cleanup. CPU wheels avoid CUDA
dependencies, pip downloads are not cached, and venvs/build trees disappear
with the runner. Cold Rust builds and optional Linux media-runtime/package
builds may still approach disk or time limits. Preview retains its 150-minute
verification and 240-minute packaging limits; Python CI uses GitHub's default
job limit. The first PR run must confirm disk use, duration, native packages,
Windows long paths and dependency downloads on the hosted images. Migrated
setup steps flag these offline verification limits in comments.
