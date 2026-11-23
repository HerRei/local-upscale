# HEART vendored code — attribution

This directory contains the HEART inference network and a Spandrel loader for
it, so that users' own HEART checkpoints load with Spandrel 0.4.2.

HEART (Hybrid Efficient Attention with Rank-factorized bias Transformer) was
created by Philip Hofmann (Phips / Phhofm), based on HAT-iLN (arXiv:2504.06629)
and SST's RIB (arXiv:2603.06738).

## `arch.py`: the network (Apache-2.0 / MIT)

- Source: [chaiNNer-org/spandrel#334](https://github.com/chaiNNer-org/spandrel/pull/334)
  (`Phhofm/spandrel`, branch `heart`) at head commit
  `2778b9f8231fd3fcc766aff83d37c50c6f45e146`, combining:
  - `libs/spandrel/spandrel/architectures/HEART/__arch/arch_util.py` (`iLN`)
  - `libs/spandrel/spandrel/architectures/HEART/__arch/hat_iln_arch.py`
  - `libs/spandrel/spandrel/architectures/HEART/__arch/heart_arch.py`
- Note on PR #334: PR #334's description states `LICENSE in __arch/`, although
  the `__arch/LICENSE` file was omitted from the PR diff. Every upstream source
  of this code was verified directly under permissive licences:
  - [`Phhofm/traiNNer-redux`](https://github.com/Phhofm/traiNNer-redux):
    `traiNNer/archs/heart_arch.py` (added in commit
    `cb3f6f256b9131109d00893bad53abb4d08e033b` on 2026-09-22),
    `traiNNer/archs/hat_iln_arch.py`, and `traiNNer/archs/arch_util.py`, all
    under `LICENSE.txt` (Apache-2.0 since commit
    `cd4e58872b3cd12ad024247165b8727de023c911` on 2022-05-17).
  - [`Phips/HEART`](https://huggingface.co/Phips/HEART): `heart_arch.py` and
    `LICENSE` (Apache-2.0).
  - [`XPixelGroup/HAT`](https://github.com/XPixelGroup/HAT): `HAT` base layers
    (`LICENSE` originally MIT in `95afc2467c`, updated to Apache-2.0 in
    `40736039f8`).
  - [`2minkyulee/i-LN`](https://github.com/2minkyulee/i-LN): `iLN/LICENSE.txt`
    (Apache-2.0, commit `6445f6e`).
  - [`Rift73/DRFT`](https://github.com/Rift73/DRFT): `LICENSE` (MIT), source of
    the `iLN` EMA std clamp noted in `traiNNer-redux` commit `9bc44e6`.
  - [`dslisleedh/SST`](https://github.com/dslisleedh/SST): `LICENSE` (MIT,
    commit `3870e53abec0fcccf78e0309028d5528624ed185`), source of Rank-factorized
    Implicit Neural Bias (RIB).
- Modifications by LocalSR:
  - Combined `arch_util.py` (`iLN`), the `hat_iln_arch.py` building blocks used
    by HEART (`AffineTransform`, `drop_path`, `DropPath`, `ChannelAttention`,
    `CAB`, `Mlp`, `PatchEmbed`, `PatchUnEmbed`, `Upsample`), and `heart_arch.py`
    into `arch.py`.
  - Removed the unused HAT-iLN top-level classes (`WindowAttention`, `HAB`,
    `OCAB`, `AttenBlocks`, `RHAG`, `HAT_iLN`, `hat_iln_*`) and the unused
    `heart()` wrapper function. None of them are referenced by `HEART` or hold
    weights, so checkpoints keep the exact same keys.
  - Reformatted with ruff (`line-length = 100`).

## `loader.py`: detection and hyperparameters (MIT)

- Source: [chaiNNer-org/spandrel#334](https://github.com/chaiNNer-org/spandrel/pull/334)
  at head commit `2778b9f8231fd3fcc766aff83d37c50c6f45e146`,
  `libs/spandrel/spandrel/architectures/HEART/__init__.py` (MIT, Copyright (c)
  2024 The ChaiNNer Organization, reproduced below).
- Modifications by LocalSR: imports `HEART` from `.arch` and `Architecture`,
  `ArchSupport`, `ImageModelDescriptor`, `SizeRequirements`, `StateDict` from
  `spandrel`; drops the `typing_extensions.override` decorator; exposes
  `HEART_DETECTION_KEYS` and `heart_support()` for LocalSR's registry;
  reformatted with ruff.

Weights are not bundled. Users load their own HEART checkpoints, whose licences
are set by their authors.

## Apache License 2.0 (`Phhofm/traiNNer-redux` / `Phips/HEART` / `XPixelGroup/HAT` / `2minkyulee/i-LN`)

```
                                 Apache License
                           Version 2.0, January 2004
                        http://www.apache.org/licenses/

   TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION

   1. Definitions.

      "License" shall mean the terms and conditions for use, reproduction,
      and distribution as defined by Sections 1 through 9 of this document.

      "Licensor" shall mean the copyright owner or entity authorized by
      the copyright owner that is granting the License.

      "Legal Entity" shall mean the union of the acting entity and all
      other entities that control, are controlled by, or are under common
      control with that entity. For the purposes of this definition,
      "control" means (i) the power, direct or indirect, to cause the
      direction or management of such entity, whether by contract or
      otherwise, or (ii) ownership of fifty percent (50%) or more of the
      outstanding shares, or (iii) beneficial ownership of such entity.

      "You" (or "Your") shall mean an individual or Legal Entity
      exercising permissions granted by this License.

      "Source" form shall mean the preferred form for making modifications,
      including but not limited to software source code, documentation
      source, and configuration files.

      "Object" form shall mean any form resulting from mechanical
      transformation or translation of a Source form, including but
      not limited to compiled object code, generated documentation,
      and conversions to other media types.

      "Work" shall mean the work of authorship, whether in Source or
      Object form, made available under the License, as indicated by a
      copyright notice that is included in or attached to the work
      (an example is provided in the Appendix below).

      "Derivative Works" shall mean any work, whether in Source or
      Object form, that is based on (or derived from) the Work and for which the
      editorial revisions, annotations, elaborations, or other modifications
      represent, as a whole, an original work of authorship. For the purposes
      of this License, Derivative Works shall not include works that remain
      separable from, or merely link (or bind by name) to the interfaces of,
      the Work and Derivative Works thereof.

      "Contribution" shall mean any work of authorship, including
      the original version of the Work and any modifications or additions
      to that Work or Derivative Works thereof, that is intentionally
      submitted to Licensor for inclusion in the Work by the copyright owner
      or by an individual or Legal Entity authorized to submit on behalf of
      the copyright owner. For the purposes of this definition, "submitted"
      means any form of electronic, verbal, or written communication sent
      to the Licensor or its representatives, including but not limited to
      communication on electronic mailing lists, source code control systems,
      and issue tracking systems that are managed by, or on behalf of, the
      Licensor for the purpose of discussing and improving the Work, but
      excluding communication that is conspicuously marked or otherwise
      designated in writing by the copyright owner as "Not a Contribution."

      "Contributor" shall mean Licensor and any individual or Legal Entity
      on behalf of whom a Contribution has been received by Licensor and
      subsequently incorporated within the Work.

   2. Grant of Copyright License. Subject to the terms and conditions of
      this License, each Contributor hereby grants to You a perpetual,
      worldwide, non-exclusive, no-charge, royalty-free, irrevocable
      copyright license to reproduce, prepare Derivative Works of,
      publicly display, publicly perform, sublicense, and distribute the
      Work and such Derivative Works in Source or Object form.

   3. Grant of Patent License. Subject to the terms and conditions of
      this License, each Contributor hereby grants to You a perpetual,
      worldwide, non-exclusive, no-charge, royalty-free, irrevocable
      (except as stated in this section) patent license to make, have made,
      use, offer to sell, sell, import, and otherwise transfer the Work,
      where such license applies only to those patent claims licensable
      by such Contributor that are necessarily infringed by their
      Contribution(s) alone or by combination of their Contribution(s)
      with the Work to which such Contribution(s) was submitted. If You
      institute patent litigation against any entity (including a
      cross-claim or counterclaim in a lawsuit) alleging that the Work
      or a Contribution incorporated within the Work constitutes direct
      or contributory patent infringement, then any patent licenses
      granted to You under this License for that Work shall terminate
      as of the date such litigation is filed.

   4. Redistribution. You may reproduce and distribute copies of the
      Work or Derivative Works thereof in any medium, with or without
      modifications, and in Source or Object form, provided that You
      meet the following conditions:

      (a) You must give any other recipients of the Work or
          Derivative Works a copy of this License; and

      (b) You must cause any modified files to carry prominent notices
          stating that You changed the files; and

      (c) You must retain, in the Source form of any Derivative Works
          that You distribute, all copyright, patent, trademark, and
          attribution notices from the Source form of the Work,
          excluding those notices that do not pertain to any part of
          the Derivative Works; and

      (d) If the Work includes a "NOTICE" text file as part of its
          distribution, then any Derivative Works that You distribute must
          include a readable copy of the attribution notices contained
          within such NOTICE file, excluding those notices that do not
          pertain to any part of the Derivative Works, in at least one
          of the following places: within a NOTICE text file distributed
          as part of the Derivative Works; within the Source form or
          documentation, if provided along with the Derivative Works; or,
          within a display generated by the Derivative Works, if and
          wherever such third-party notices normally appear. The contents
          of the NOTICE file are for informational purposes only and
          do not modify the License. You may add Your own attribution
          notices within Derivative Works that You distribute, alongside
          or as an addendum to the NOTICE text from the Work, provided
          that such additional attribution notices cannot be construed
          as modifying the License.

      You may add Your own copyright statement to Your modifications and
      may provide additional or different license terms and conditions
      for use, reproduction, or distribution of Your modifications, or
      for any such Derivative Works as a whole, provided Your use,
      reproduction, and distribution of the Work otherwise complies with
      the conditions stated in this License.

   5. Submission of Contributions. Unless You explicitly state otherwise,
      any Contribution intentionally submitted for inclusion in the Work
      by You to the Licensor shall be under the terms and conditions of
      this License, without any additional terms or conditions.
      Notwithstanding the above, nothing herein shall supersede or modify
      the terms of any separate license agreement you may have executed
      with Licensor regarding such Contributions.

   6. Trademarks. This License does not grant permission to use the trade
      names, trademarks, service marks, or product names of the Licensor,
      except as required for reasonable and customary use in describing the
      origin of the Work and reproducing the content of the NOTICE file.

   7. Disclaimer of Warranty. Unless required by applicable law or
      agreed to in writing, Licensor provides the Work (and each
      Contributor provides its Contributions) on an "AS IS" BASIS,
      WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
      implied, including, without limitation, any warranties or conditions
      of TITLE, NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A
      PARTICULAR PURPOSE. You are solely responsible for determining the
      appropriateness of using or redistributing the Work and assume any
      risks associated with Your exercise of permissions under this License.

   8. Limitation of Liability. In no event and under no legal theory,
      whether in tort (including negligence), contract, or otherwise,
      unless required by applicable law (such as deliberate and grossly
      negligent acts) or agreed to in writing, shall any Contributor be
      liable to You for damages, including any direct, indirect, special,
      incidental, or consequential damages of any character arising as a
      result of this License or out of the use or inability to use the
      Work (including but not limited to damages for loss of goodwill,
      work stoppage, computer failure or malfunction, or any and all
      other commercial damages or losses), even if such Contributor
      has been advised of the possibility of such damages.

   9. Accepting Warranty or Additional Liability. While redistributing
      the Work or Derivative Works thereof, You may choose to offer,
      and charge a fee for, acceptance of support, warranty, indemnity,
      or other liability obligations and/or rights consistent with this
      License. However, in accepting such obligations, You may act only
      on Your own behalf and on Your sole responsibility, not on behalf
      of any other Contributor, and only if You agree to indemnify,
      defend, and hold each Contributor harmless for any liability
      incurred by, or claims asserted against, such Contributor by reason
      of your accepting any such warranty or additional liability.

   END OF TERMS AND CONDITIONS
```

## MIT Licenses (`chaiNNer-org/spandrel` PR #334, `dslisleedh/SST`, `Rift73/DRFT`)

```
MIT License

Copyright (c) 2024 The ChaiNNer Organization (Spandrel loader)
Copyright (c) 2026 Dongheon Lee (dslisleedh/SST, Rank-factorized Implicit Neural Bias)
Copyright (c) 2026 Rift73 (Rift73/DRFT, i-LN EMA std clamping)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
