# HDR and benchmark preview acceptance

Branch: `codex/hdr-preservation`, isolated from `fix/v0.0.12-release-pipeline`.
No release workflow, tag, version, or installer metadata was changed.

Local checks on Apple silicon, 10 September 2026:

- Python: 546 passed, 2 skipped. The existing torch.meshgrid deprecation warning remains.
- Frontend: 61 passed, including asynchronous tile JPEG delivery, late-frame rejection,
  CPU selection independent of enhancement hardware, and first-frame ETA.
- Rust: 47 passed, including JSON roundtrips, separate device score retention across
  successive CPU/GPU runs, and forwarding HDR output mode.
- Svelte: zero errors/warnings. Production frontend and native macOS debug app built.
- Packaged native smoke: worker negotiation and ready state passed.
- Native CPU-only and MPS-only benchmark runs completed independently. The saved
  hardware cards retained CPU 3.81 output MP/s (stable, 1.15% timing spread) after
  the MPS run recorded 19.31 output MP/s (unstable, 10.02% timing spread). The latter
  is a diagnostic measurement, not a publishable stable score. Real render squares
  were visible during warm-up; scored iterations excluded preview work.
- Native HDR job: HAT-S on MPS, 216×384 → 432×768, two frames. The generated file is
  HEVC Main 10 / YUV420P10LE, HLG transfer, BT.2020 primaries and matrix, limited range.
  The app displayed real completed squares and an active model tile; frame 1 of 2
  already had a measured tile-based ETA. Reprocessing the same video uses live tiles
  instead of continuing to show its older completed comparison.
- Original rotated 4K HLG MOV: two-frame full-resolution codec export retained HLG,
  portrait geometry and stereo AAC. The unsupported additional spatial track was
  omitted with the established warning. The original's size and mtime were unchanged.
- Exact HAT-S Face and HAT-L Face checkpoints: real MPS float32 inference, 54×96 →
  216×384, two frames for each model in both HLG and PQ. Outputs were finite; maximum
  error in the source linear RGB block mean was below 0.000002 before codec quantization.
- Synthetic ten-bit gradients: >700 distinct encoded luma levels, plus decoded RGB
  checks. VFR, trim bounds, audio, transfer functions and float face blending passed.

Follow-up for the reported SeedVR2 MPS failure and preview alignment:

- Python: 552 passed, 2 skipped; the same meshgrid warning remains. Focused video,
  HDR, adapter and timing tests also passed after the final phase/ETA changes.
- Frontend: 65 passed. HDR preservation is disabled on SeedVR2 selection and restored
  for a custom checkpoint; persisted incompatible choices normalize to SDR. Output
  resolution reaches the worker, with legacy settings retaining their original scale.
- Rust: 47 passed; the rebuilt native app and Svelte checks passed.
- The packaged native app completed a separate five-frame SeedVR2 job from a 4K
  HLG sample, producing five decoded 256×454 SDR frames with BT.709 tags. Its
  overlay followed the real clip-processing phases. Custom resolution exports
  use a resolution suffix instead of the unrelated image-scale setting.
- The original 4K HLG MOV's first two frames decoded and converted to SDR in 2.16 s.
  Small three-channel colour contractions now avoid threaded BLAS overhead on strided
  decoder buffers. Planar/rotated and interleaved layouts give the same colour result.
- Actual SeedVR2-3B FP16 checkpoint on MPS: ten frames of that original source, three
  streamed clips, output 256×454 SDR in 266.5 s. Decoding the export confirmed ten
  nonconstant frames, YUV420P / BT.709 tags, 0.169 s duration and stereo AAC. The
  existing MPS high-watermark limit stayed at 0.62. No checkpoint or source was changed.
- Weight loading via CPU, five-frame MPS windows, 128-pixel VAE tiles and phase
  cleanup avoid the failures seen with the original loading/buffering path. The
  sampler's real output is not a guarantee of perceptual quality or 4K/8K feasibility.
- Geometry checks cover the original portrait video's 8640×15360 HAT output mapped
  to a 900×1600 canvas. Pending cells, decoded tile pixels and the active outline
  share the same boundaries, including the last partial column. The remove icon
  is centered in the native app; the HDR option is visibly disabled for SeedVR2.
- A native HAT-S job completed two frames at 216×384 → 864×1536. Completed image
  squares, pending cells and the active outline matched visually, including the
  partial edge column; frame 1 already displayed an ETA.

Private clips, checkpoint files, screenshots and machine-specific paths are not
included in this repository. These are functional checks on bounded samples,
not perceptual HDR certification, a complete multi-minute 4K upscale, or acceptance
of every platform's released installer. HAT remains SDR-trained; HDR detail and
block-boundary/temporal quality remain Labs. The existing release acceptance gates
are unchanged.
