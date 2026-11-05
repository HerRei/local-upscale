# Dependency updates and the release train

Most of the upkeep runs by itself on GitHub-hosted runners with the workflow token.
Nothing here uses an API key or a secret, and nothing merges runtime code or publishes
a release without a person.

| When | Workflow | What it does |
| --- | --- | --- |
| Mon-Thu 05:00 (Zurich) | Dependabot (`.github/dependabot.yml`) | One grouped PR per ecosystem: Python (Mon), npm (Tue), Cargo (Wed), GitHub Actions (Thu). New versions wait 7 days (majors 30) before they are offered. Security fixes come any day, also grouped. |
| every CI or Preview run | `required.yml` | Sets the commit status **Required checks** from the latest CI and Preview runs of that exact commit. Preview counts only when its own path filter matches. This is the one check the `main` ruleset requires. |
| every Dependabot PR | `dependabot-automerge.yml` | Arms auto-merge only for the `dev-tools` groups (pytest, prettier, vitest, …; the list is in `scripts/dependabot_automerge.py`), and only when every commit is a verified Dependabot commit and only the expected manifest and lock files changed. It disarms auto-merge when a PR stops qualifying. |
| Fri 05:00 UTC | `deps-triage.yml` | Reruns infrastructure failures (the Mac mini's storage guard, the release-priority wait, cancelled jobs) once per commit. It labels at most two real breaks `claude-fix` and updates the `deps-status` issue. While `main` itself is red it labels nothing. |
| daily 05:00 UTC | `release-train.yml` | Opens a `Version X` PR when a release is due (see below). |

## What needs a person

- **Runtime updates** (the `runtime` and `security` groups, every major update) and
  **GitHub Actions updates.** The workflow token can't merge changes to workflow files.
  Review, then merge when **Required checks** is green.
- **Python packages that ship in the backend.** If an updated package appears in any
  `requirements/locks/*.txt` (pillow does, through `pyproject.toml`), regenerate
  those locks on the PR before merging:
  `python scripts/lock_backend_requirements.py --upgrade-package NAME==VERSION`. Without
  `--upgrade-package`, uv keeps every existing pin and the shipped set never changes.
  Run `tests/test_backend_packaging.py` and a CPU AppImage smoke build
  (`gh workflow run linux-appimages.yml --ref <branch>`). Change a pin in
  `requirements/backend-*.in` only where it blocks the update.
- **Lint tools** (ruff, pyright, vulture) are pinned in `ci.yml` too, so a bump changes
  both places.
- **`claude-fix` PRs**: run the deps-doctor skill. It fixes the break on a `doctor/<dep>`
  branch, runs the checks and shows the diff. Nothing is pushed before you OK it,
  because a push runs CI on the Mac mini.

## Release train

`release-train.yml` checks once a day and opens a candidate PR when all of these hold:

1. The last published release is at least 14 days old. Drafts don't count.
2. No candidate is pending: no open `release/v*` PR, and `main` isn't already at a
   version that hasn't been published.
3. **Required checks** is green for `main`'s exact commit. Auto-merges made with the
   workflow token start no CI on `main`, so when the status is missing the train starts
   CI and Preview there and tries again the next day.
4. Something besides `dev-tools` updates changed since the last release.

The PR comes from `scripts/bump_version.py --next`. It moves every version string,
marks the release plan's targets `candidate`, adds a CHANGELOG section and a draft of
`docs/releases/vX.md`, and puts a "vX is in preparation" note in the README. Edit the
notes, merge, then follow [releasing.md](releasing.md): build on the Mac from the
merged commit, and publish only when you decide to. After publishing,
`python scripts/bump_version.py --mark-published` turns the candidate markers into
published ones. It also lists the prose (README downloads, platforms, known
limitations) that still names the previous version.

Run it by hand any time with `gh workflow run release-train.yml` (add `-f force=true` to
skip the 14-day wait).

## The `main` ruleset

Settings → Rules → Rulesets, target `main`:

- Require a pull request before merging, with no required approvals.
- Require status checks: `Required checks`, source **GitHub Actions**. Only the
  workflow token can satisfy it, so a status set by hand doesn't count.
- Block force pushes and deletions.
- Bypass: repository admin, mode "pull requests only", for emergencies.

Turn it on only after `required.yml` is on `main` and `main` is green. Otherwise
nothing can merge.
