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

Private clips, checkpoint files, screenshots and machine-specific paths are not
included in this repository. These are functional checks on bounded samples,
not perceptual HDR certification, a complete multi-minute 4K upscale, or acceptance
of every platform's released installer. HAT remains SDR-trained; HDR detail and
block-boundary/temporal quality remain Labs. The existing release acceptance gates
are unchanged.
