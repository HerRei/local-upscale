# Post-mortem: updates from 0.5.0 to 0.5.2 refused (2026-11-23)

## Summary

From the release of 0.5.2 on 2026-11-23, every LocalSR install on 0.4.0 or 0.5.0
refused the in-app update to 0.5.2, on every edition. The app showed:

> I/O error: unknown field `arch`, expected one of `schema_version`, `format`,
> `backend`, `file_count`, `unpacked_bytes`, `parts` at line 2 column 8

A tester on Windows CUDA reported it eleven hours after the release. The cause was
ours: 0.5.2 changed a file that already installed releases read, and no test ran an
installed release's updater against the new release.

## Impact

- Who: everyone on 0.4.0 or 0.5.0 who updated from inside the app. macOS installs
  through Homebrew and new downloads from the website were not affected.
- What: the update stopped after the download, before anything was installed. The
  installed version kept working and no data was lost. Windows CUDA users downloaded
  about 2 GB of engine parts for nothing.
- A second fault was hidden behind the first: had 0.5.0 accepted the update, a
  Windows CUDA install would have ended on 0.5.2 without a working engine.

## Timeline (CET)

- 06:00 0.5.2 published: GitHub release, mirror and update feed.
- 17:10 A tester reports the error for the Windows CUDA edition.
- 17:25 Cause found: the feed names a schema 2 engine manifest; 0.4.0 and 0.5.0 read
  only schema 1 and refuse unknown fields.
- 17:40 Scope widened to every edition: 0.5.0 needs the engine package on every update,
  because each release has a new engine id, and all 0.5.2 manifests are schema 2.
- 17:45 Second fault found: 0.5.2 only uses an installed engine with a `meta.json`,
  which updaters up to 0.5.0 never write. Windows CUDA has no bundled engine to fall
  back on.
- 17:50 Decision: leave the 0.5.2 feed as it is. Fixing only the manifest would turn a
  refused update into a broken Windows CUDA install. Users can install 0.5.2 from the
  website; the fix ships with 0.6.0, to which 0.5.0 updates directly.
- Evening: fix, release gate and upgrade check written and verified (below).

## Causes

1. **A file old clients read was changed in place.** 0.5.2 added `version`, `os`,
   `arch`, `protocol_version` and `engine_id` to the engine manifest (schema 2) and the
   feed named that manifest for every client, old ones included.
2. **The old reader is strict on purpose.** 0.4.0 and 0.5.0 parse the manifest with
   unknown fields refused and `schema_version` 1 required: security hardening that also
   means the file can never gain a field for them.
3. **Nothing tested an installed release's updater.** Unit tests, the release verifier
   and the installer smoke tests all ran 0.5.2's own code against 0.5.2's files. The
   release checklist had no in-app update from the previous release on each edition.
4. **Reviews asked the wrong question for this change.** The schema 2 work was reviewed
   for what the new client accepts, not for what installed releases do with the new files.
5. **The engine layout changed without a migration.** 0.5.2 requires `meta.json` for an
   installed engine, but older updaters install engines without one.

Contributing: LocalSR has no telemetry for failed updates, by design, so detection
depended on a user report.

## What went well

- The old updater failed closed: no partial install, the old version kept working.
- The tester reported quickly and with the exact message.

## Changes (in 0.6.0)

| Change | Where |
|---|---|
| The feed names `<package>.engine-update.json`, the manifest in the form every updater since 0.4.0 reads, derived from the schema 2 manifest and signed with it | `prepare_engine_payload.py`, `beta_update_signing.py`, `release_macos_beta.sh`, the workflows |
| Release verification refuses a feed whose engine manifest an older updater would refuse | `verify_prepared_release.py` (`legacy_update_problem`) |
| A host without a bundled engine records an engine an older updater installed: files hashed, handshake passed, then the usual checks | `updates.rs` (`adopt_engine_from_older_updater`), `worker.rs` |
| The update from older releases runs with their own code: each tag's updater is built with a probe and installs this release's engine package; this host must then record and start that engine | `scripts/upgrade_check.py`, in the Windows CUDA and Linux CPU jobs and the macOS release script |
| That check is a required release gate | `check_desktop_architecture.py` |
| Rules: after publishing, add the tag to `OLDER_RELEASES`; review feed, statement, manifest and engine-layout changes for installed releases | `docs/releasing.md` (Older releases) |

## Verification

- `upgrade_check.py --as-in-0.5.2` builds the feed as 0.5.2 did and fails with the
  error the tester saw, word for word, from 0.5.0's own code.
- `upgrade_check.py` with the fix passes from 0.4.0, 0.5.0 and 0.5.2 on macOS with the
  real engine: each old updater installs the engine package, and the new host records
  that engine and completes the handshake with it. The Windows CUDA and Linux CPU jobs
  run the same check in CI.
- Still to do before 0.6.0 ships: the real in-app update from 0.5.0 to 0.6.0 of the
  Windows CUDA edition on the Windows test laptop.

## Open

- Users on 0.4.0 and 0.5.0 stay on their version until 0.6.0 is published, or install
  0.5.2 from the website.
