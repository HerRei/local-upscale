**LocalSR beta decisions**

Started 12 September 2026. Companion to the [beta checklist](beta-release-checklist.md). Recommendations are not approvals. Update this file as the user answers; do not infer acceptance from elapsed time.

**Confirmed constraints**

| Constraint | Source/status |
| --- | --- |
| Preserve the active `.12` release, its checkouts, jobs and runners | Explicit user instruction; confirmed |
| Keep current work local; no publication or remote changes | Explicit user instruction; confirmed |
| Leave the current Mac GUI alone; use isolated environments for GUI acceptance | Explicit user instruction; confirmed |
| Preserve media, recipes, settings and downloaded models; clean disposable test artifacts | Explicit user instruction; confirmed |
| Decide beta scope and signing/account choices with the user | Current request; confirmed |

**Open decisions**

| ID | Decision | Recommendation/context | Status |
| --- | --- | --- | --- |
| B1 | Invited first beta or immediate public beta | 5–10 invited testers first, with signing and acceptance requirements retained | Asked; awaiting answer |
| B2 | Individual or registered-company publisher | Use the actual legal identity/entity; this controls signing eligibility | Asked; awaiting answer |
| B3 | Publisher country and existing developer/signing accounts | Confirm before choosing or purchasing a provider | Next after B2 |
| B4 | Budget and build capacity | Confirm current signing quotes and available isolated runners before setting a release date | Pending |
| B5 | Included platforms and GPUs | Start with testable Apple-Silicon/Linux paths; advertise each Windows/other GPU package only after its actual installer passes | Proposed; not approved |
| B6 | Core versus Labs features | Image/SDR video core; HDR/SeedVR2/deflicker/video faces remain opt-in Labs until their quality evidence supports promotion | Proposed; not approved |
| B7 | Public model catalog and defaults | Review face-fork/training rights and RealPLKSR ambiguity; preserve local research models during that decision | Pending; no catalog changes authorized by this plan |
| B8 | Download destination, source visibility and feedback | A separate public distribution/issues repository is an option for later public beta; source can remain private | Proposed; not approved |
| B9 | Production update feed, key custody and channel policy | Separate Stable/Beta feeds; protected key with backup; matching backend and engine packages | Pending |
| B10 | Beta version, release date and expansion to public access | Select after account readiness, scope and an initial signed candidate test cycle | Pending |

**Current evidence**

Local preview evidence is recorded at [local-update-acceptance.json](local-update-acceptance.json). Source repository `HerRei/local-upscale` was confirmed private, with issues enabled, via the GitHub API on 12 September 2026. This does not give general testers access. The strict beta readiness check still reports ten unresolved blocking requirements; no gate has been relaxed or marked passed by this planning work.

The first two questions are pending in the conversation. No audience or publisher choice has been made on the user's behalf.
