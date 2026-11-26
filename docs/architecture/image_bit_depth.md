# Photo Bit Depth (`output_bit_depth`)

Images enter and leave LocalSR as 8-bit by default (`output_bit_depth = 8`), keeping all existing pixel and container outputs unchanged. When `output_bit_depth = 16` is requested (for PNG or TIFF outputs only), 16 bits per channel are preserved from source decode through output encode.

## Protocol, Feature Gate, and Settings

- **Schema & messages**: `protocol/worker-protocol.schema.json` (and its packaged copy in `src/localsr/protocol/`) defines `output_bit_depth` on `jobRequest` with `enum: [8, 16]` and default `8`.
- **Feature gate**: The worker lists `"image_16bit"` in `engine_info.features` (`src/localsr/worker/server.py`). In the Rust host (`desktop/src-tauri/src/commands.rs`), `validate_bit_depth_engine` refuses `output_bit_depth == 16` unless the active engine announces `"image_16bit"`.
- **Wire compatibility**: `ImageJobRequest` (`desktop/src-tauri/src/protocol.rs`) skips serializing `output_bit_depth` when it equals `8`, so older engines without `"image_16bit"` never see the field.
- **Format gate**: Both `commands.rs` (`validate_settings`, `validate_start_input`, `save_recipe_checked`) and Python (`validate_image_job` in `src/localsr/worker/request_validation.py`, `require_output_format` in `src/localsr/core/image_io.py`) reject `output_bit_depth == 16` for any format other than PNG or TIFF before inference begins.
- **Settings & recipes**: `UiSettings`, `Recipe`, and `StartBatchInput` (`desktop/src-tauri/src/types.rs`) include `output_bit_depth` with a serde default of `8` without bumping `SCHEMA_VERSION`.

## Pixel and Metadata Flow

1. **Decode (`src/localsr/core/image_io.py`, `image_io_16bit.py`)**:
   - With `bit_depth=16`, camera RAW develops via LibRaw (`rawpy.postprocess(..., output_bps=16)`), 16-bit PNGs (RGB, RGBA, grey, grey+alpha, including Adam7) decode through OpenCV (`read_png_16`, using `cv2.imdecode` with `IMREAD_UNCHANGED` after Pillow header checks and the pixel budget), and high-bit-depth TIFFs decode through `tifffile` (`read_tiff_16`).
   - Any 16-bit alpha channel is kept as an `I;16` Pillow image (`self.alpha_channel`).
   - **16-bit ICC rule**: A non-RAW source with a valid embedded ICC profile is **not** converted to sRGB; working pixels stay in the source's colour space (`float32` in `[0, 1]`) and the 16-bit output embeds the source's profile bytes unchanged, preserving wide-gamut spaces losslessly. Embedded profiles are validated with LittleCMS (`ImageCms.buildTransform`); an invalid or mode-mismatched profile emits the same `Invalid ICC profile` warning and sets `has_invalid_icc = True` as the 8-bit path, and is not embedded. RAW inputs developed at 16 bits still target sRGB and embed the sRGB profile. The 8-bit path is unchanged.
2. **Inference & memmap (`src/localsr/core/inference.py`, `output_writer.py`, `image_job.py`, `image_batch.py`)**:
   - `OutputWriter` allocates a `uint16` memmap when `output_bit_depth == 16` (and `uint8` for `8`).
   - Tile outputs (`_infer_tile`, `_process_ddcolor_image`, `process_frame_face_aware`, `ImageBatchRunner._Lane`, `_passthrough_1x`, `_run_postprocess`, `fill_unfinished`) quantize `[0, 1]` to `uint16` (`* 65535`).
   - Live JPEG tile previews (`src/localsr/core/live_preview.py`) shift `uint16` down to 8-bit (`>> 8`) before JPEG compression.
3. **Encode (`src/localsr/core/image_io.py`, `image_io_16bit.py`)**:
   - `save_from_writer` dispatches `output_bit_depth == 16` to `_save_16bit_from_writer`, which handles `output_scale < scale` per-channel float32 `"F"` mode Lanczos resizing (rounded/clipped to `uint16`), Rec. 601 grey-keeping, float32 `"F"` mode 16-bit alpha resizing, and allow-listed EXIF/ICC embedding.
   - 16-bit PNGs are written by `write_png_16` via `cv2.imencode(".png", ...)` with `iCCP` and `eXIf` chunks inserted directly after `IHDR` (Pillow 13 does not write 48/64-bit RGB/RGBA PNGs); 16-bit TIFFs are written by `write_tiff_16` via `tifffile` (`adobe_deflate`, `software=False`, `metadata=None`).

## Disk and Memory Sizing

- `src/localsr/core/disk_space.py` (`image_need`, `encoded_bound`) and its Rust mirror `desktop/src-tauri/src/disk_room.rs` double both the memmap scratch estimate and the lossless PNG/TIFF encoded bound when `output_bit_depth == 16`, verified against `tests/fixtures/disk_estimate_cases.json`.
- `src/localsr/worker/image_batch.py` (`save_bytes`) doubles the per-save encoder memory estimate for 16-bit outputs.
