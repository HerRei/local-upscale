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
| Limited maintenance capacity during university for roughly the next six months | Explicit user statement on 12 September 2026; account for this in scope and release expectations |

**Decisions and pending choices**

| ID | Decision | Recommendation/context | Status |
| --- | --- | --- | --- |
| B1 | Beta audience | Friends and voluntary testers reached through Reddit; prepare access for that audience while retaining signing and acceptance requirements | Confirmed by user; no publication or Reddit post authorized |
| B2 | Individual or registered-company publisher | Use the actual legal identity/entity; this controls signing eligibility | Asked; awaiting answer |
| B3 | Publisher country and existing developer/signing accounts | Store developer account and reserved LocalSR identity supplied; legal publisher type/country and Apple enrollment remain to confirm | Store identity confirmed; other details pending |
| B4 | Budget and build capacity | Assess the free Store MSIX route before buying a Windows certificate; confirm available isolated runners and plan for limited maintenance during university | Capacity constraint confirmed; budget and implementation choices pending |
| B5 | Included platforms and GPUs | Start with testable Apple-Silicon/Linux paths; advertise each Windows/other GPU package only after its actual installer passes | Proposed; not approved |
| B6 | Core versus Labs features | Image/SDR video core; HDR/SeedVR2/deflicker/video faces remain opt-in Labs until their quality evidence supports promotion | Proposed; not approved |
| B7 | Public model catalog and defaults | Review face-fork/training rights and RealPLKSR ambiguity; preserve local research models during that decision | Pending; no catalog changes authorized by this plan |
| B8 | Download destination, source visibility and feedback | A separate public distribution/issues repository is an option for later public beta; source can remain private | Proposed; not approved |
| B9 | Production update feed, key custody and channel policy | Direct editions: separate Stable/Beta feeds, protected key and matching engine packages. If selected, Store MSIX edition: Store-managed application updates with tested data preservation | Pending; depends on B11 |
| B10 | Beta version, release date and expansion to public access | Select after account readiness, scope and an initial signed candidate test cycle | Pending |
| B11 | Windows Store MSIX, direct installer, or both | User created an MSIX Store draft and supplied its reserved identity; prepare the Store package locally. Direct installer distribution and engine delivery strategy remain open | Local Store preparation in progress; no submission/publication approved |
| B12 | Maintenance and feedback expectations | Recommend a small, well-tested beta, one feedback channel, clear known issues and no promised update cadence during the next six months | Proposed; user confirmed limited time, not this exact operating plan |

**Current evidence**

Local preview evidence is recorded at [local-update-acceptance.json](local-update-acceptance.json). Source repository `HerRei/local-upscale` was confirmed private, with issues enabled, via the GitHub API on 12 September 2026. This does not give general testers access. The strict beta readiness check still reports ten unresolved blocking requirements; no gate has been relaxed or marked passed by this planning work.

The audience question is answered: friends and Reddit volunteers. The legal publisher account type remains unanswered. Public access is a planning requirement for this audience, not authorization to publish or contact testers.

The Store package identity is now recorded in [microsoft-store.md](microsoft-store.md).
The display name `HerRei` does not establish whether the account is Individual or
Company. The user requested conservative provisional hardware fields; suggested
targets are 16 GB RAM minimum, 32 GB recommended, CPU processing available, and
16 GB recommended GPU memory. For demanding SeedVR2 video, 64 GB RAM / 24 GB VRAM
were suggested. These are planning targets, not measured compatibility guarantees.

Microsoft's current documentation confirms free Store registration for [individuals](https://learn.microsoft.com/en-us/windows/apps/publish/whats-new-individual-developer) and [companies](https://blogs.windows.com/windowsdeveloper/2026/05/07/publish-to-microsoft-store-as-a-company-now-with-free-registration-and-faster-onboarding/). Store **MSIX** receives free Microsoft signing after certification; a Store **EXE/MSI** listing still requires the publisher to sign its installer. [Signing requirements](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/code-signing-options). The checklist's earlier assumption that Windows necessarily needs a paid signing provider has been corrected. LocalSR Store packaging and certification are not yet verified.
