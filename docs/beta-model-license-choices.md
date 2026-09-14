# Model license decisions for the beta

Updated 13 September 2026. No message to a model publisher was sent and no model
was removed or silently substituted.

The two HAT face forks retain stock-model pairing and checksum-verified user
imports. Training-data/checkpoint rights remain unresolved; their evaluation
results do not establish redistribution or commercial rights. Both remain in
the model scope without a bundled checkpoint or automatic public download.

## Corrected NomosWebPhoto / HFA2k evidence

The earlier recommendation to require manual imports relied on the non-standard
`CC-BY-0.4` text in the release descriptions. It omitted stronger publisher
evidence and is superseded by this review. **The user approved the corrected
policy on 13 September 2026, conditional on respecting the author's license and
intended use. It is implemented in the isolated beta source.** Retained native
review packages predate this change and still require final rebuilding.

Both model cards have explicit `license: cc-by-4.0` metadata. The same cards link
back to the GitHub releases and identify Philip Hofmann. His Phips profile links
to Phhofm, and the Phhofm repository links to Phips. The repository README also
expressly describes making models available on Hugging Face for application
downloads. This supports in-app downloads as an intended use. The transposed
`0.4` text remains in the descriptions; the conclusion below relies on the
explicit license declaration, not an assumed correction of that text.

Primary evidence:
[NomosWebPhoto publisher release](https://github.com/Phhofm/models/releases/tag/4xNomosWebPhoto_RealPLKSR),
[NomosWebPhoto pinned model card](https://huggingface.co/Phips/4xNomosWebPhoto_RealPLKSR/blob/49d5da19489e645e870eb076ea84815471f27ef4/README.md),
[HFA2k pinned model card](https://huggingface.co/Phips/4xHFA2k_ludvae_realplksr_dysample/blob/96c2a2e13fee3f9926a13c8d80c6923fcc9d15bc/README.md),
[HFA2k publisher release](https://github.com/Phhofm/models/releases/tag/4xHFA2k_ludvae_realplksr_dysample),
[publisher README](https://github.com/Phhofm/models#models),
[publisher profile](https://huggingface.co/Phips).

Exact checkpoint checks on 13 September 2026:

- HFA2k's Hugging Face `.pth` LFS record is 29,715,988 bytes with SHA-256
  `c6e44af18fd3159787b0dbf81d432a6c1ba12c736fc1184b107ed091e49e327c`,
  matching the LocalSR catalog pin exactly.
- NomosWebPhoto's Hugging Face main branch contains the author's `.safetensors`
  conversion: 29,590,440 bytes, SHA-256
  `9be0228f98156a100d6636d99b373ed2785b999723f9adc4cca504329ab157f2`.
  A fresh download matched that hash. The catalog's GitHub `.pth` download also
  matched its existing 29,683,482-byte / `a9db66c9b674c6a5025b6ef3bee71a57c33b8605d8a2de0980470f89002efbbe`
  pin. Restricted CPU loading with Torch 2.13.0 found **all 340 tensors identical**
  in keys, shape, dtype and values (7,389,680 elements). No installed model was
  replaced. These are provenance checks, not a new restoration-quality test.
- The cited training framework revision has Apache-2.0 source terms. Framework
  source licensing alone does not establish checkpoint or training-data rights.

The [CC BY 4.0 terms](https://creativecommons.org/licenses/by/4.0/legalcode.en)
allow sharing and adaptation, including commercially, subject to attribution,
retained notices, a license reference and disclosure of modifications. They do
not authorize claims of endorsement or added restrictions on the licensed rights.
They cover rights the licensor can grant; they do not guarantee third-party
rights, model quality or clearance for any particular input/output. Therefore
neither a blanket commercial-safety guarantee nor a non-commercial-only license
claim is appropriate.

**Approved policy:** retain both models and verified in-app downloads from the
author's published assets. The beta source replaces the non-commercial-only
acknowledgement/reminder with accurate CC BY 4.0 information, author credit,
source/license links and a notice that LocalSR does not modify the downloaded
weights. The author's license remains separate from LocalSR's own software
license. Existing filenames, source download URLs, sizes and SHA-256 pins are
unchanged; existing installed weights are not replaced. Both models retain
their Labs status. Face-fork imports retain their separate unresolved-rights
policy. No restoration checkpoint has been added to an installer.

Attribution carried by the model information and distribution notices:

> Model by Philip Hofmann (Phips / Phhofm). CC BY 4.0. Downloaded unchanged
> from the author. View model source and license terms.

The earlier draft inquiry is no longer a launch prerequisite solely because of
the `0.4` typo; an optional request to align the release text can be prepared
later. No publisher message has been authorized or sent.

Local evidence is retained under
`build/beta-review/research/model-license-20260913/`: pinned model cards,
source/hash records, publisher README and `nomos-tensor-equivalence.json`.
Disposable checkpoint copies used for comparison are removed after verification;
they are not added to the release package.

Local verification for this policy change: 54 frontend tests, 18 catalog tests
and three native download-policy tests passed. The frontend type checker reports
zero errors and warnings. Tests cover the two direct download actions, source
and license links, progress/cancellation controls, retained installed-model
attribution and rejection of automatic face-checkpoint downloads. These are
targeted source checks; installed-platform acceptance must be repeated on the
final rebuilt candidates.

Both exact publisher `.pth` files were also freshly downloaded through LocalSR's
Python downloader into a disposable model directory. Size/SHA-256 verification,
`ModelStore.is_installed`, restricted `ModelAdapter` loading and real CPU
32×32 → 128×128 inference passed for each; outputs were finite. Evidence:
`download-load-checks.json` in the directory above. Temporary weights were
removed, and the user's installed model directory was not used. The production
frontend build, generated-catalog check and separate beta metadata check passed.
The Qt-dependent legacy estimator module was not run; its catalog license-set
assertion was verified directly without importing a GUI.
