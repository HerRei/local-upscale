# Source and documentation review

13 September 2026. This review covers the isolated beta source and public project
presentation. It does not replace installed-package acceptance or clear the
remaining [dependency and distribution work](beta-dependency-review.md).

## Changes

- Reworked the project, desktop, contributor and documentation entry points.
  The README includes an actual Windows app capture with image credit, current
  build commands, package-specific hardware coverage and model limitations.
- Added a separate guide to all twelve catalog models, including author/license
  links and the distinction between verified downloads and restricted imports.
- Removed stale handoff instructions and private connection details from public
  test summaries. Complete original operator records remain in local review
  storage with their original hashes.
- Applied consistent first-party Python, Rust, Svelte, TypeScript and CSS
  formatting. Pinned development formatters and local CI enforce the style.
  Simplified verbose comments and test descriptions without changing inference.
- Corrected local CI's beta metadata selection and Python source path. Updated
  stale alpha/model-policy assertions and fixed partial HTTP request handling
  in the production updater test server.
- Published the authorized [GitHub profile README update](https://github.com/HerRei/HerRei/commit/cb9b0611b134fbc8bbf9736ada8fb1c6e4466a56).
  LocalSR is presented as a public beta and source release in preparation.

The application's MIT notice, upstream credits and third-party license notices
are retained. All 90 vendored files match the start-of-review hashes.

## Verification

These results describe the source-polish snapshot retained before Slint was
retired. The [subsequent cleanup record](slint-retirement.md) gives the current
test counts and package checks; the earlier archive and logs remain unchanged.

| Check | Result |
| --- | --- |
| Python suite | 625 passed, 3 skipped; one upstream Torch meshgrid warning |
| Svelte/TypeScript suite | 110 passed; Svelte check reports no errors or warnings |
| Rust suite | 66 passed; the one normally ignored production-artifact test was run separately and passed |
| Production updater | Actual signed CPU AppImage accepted; truncated content and substituted bytes rejected; temporary outputs removed |
| Static checks | Ruff, Pyright, rustfmt, Clippy, workflow validation, model catalog export and release gates pass |
| Builds | Svelte production build, local Slint compilation, Python wheel and source-package data checks pass |
| README layout | Seven pages at desktop and phone widths: 14 checks pass; no broken local links or images |
| Documentation links | All 60 Markdown files checked; 155 local file/directory links resolve |
| Source scan | No merge markers, chat boundaries, assistant boilerplate, recognized credential patterns or private host paths found in the current snapshot |
| Preserved scope | Release workflows, alpha manifests/notes, application license and vendored sources match the starting hashes |

The scan covers the current working snapshot, not Git history or every possible
secret format. Formatting and automated tests do not prove the absence of all
bugs. Native installers retained from earlier acceptance were not rebuilt or
relabeled by this review.

## Review materials

Local evidence is in `build/beta-review/source-polish/`: test logs, before/after
inventories, rendered README previews, profile publication verification and a
separately checksummed application source archive. The archive uses an explicit
file list and excludes Git history, private operator backups, credentials, build
caches and restoration checkpoints. It includes the existing attributed SeedVR2
conditioning assets needed by the worker.

The source-publication approach is approved under decision B19. The application
source archive is a review snapshot; it is not the complete corresponding-source
package for all bundled native dependencies. Final dependency reconciliation,
native rebuilds and release review remain on the [beta checklist](beta-release-checklist.md).
