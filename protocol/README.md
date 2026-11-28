# LocalSR worker protocol

The desktop host and inference engine exchange one JSON object per line over
stdin/stdout. Protocol version 1 includes a host/worker handshake, media probing,
job progress and results.

Compatibility rules:

- Workers always emit `worker_ready` first, preserving the version 1 startup contract.
- New hosts send `handshake_request` and refuse incompatible engine versions.
- Job IDs are host-generated UUIDs. Only one worker job runs at a time; queueing
  and crash recovery belong to the host.
- The worker holds job and probe requests to the schema's types and bounds. A request
  outside them ends with `job_failed` (`media_probe_failed` for a probe) that names the
  field, before any file is opened. A line that is not a JSON object, or a request whose
  `data` is not an object or that has no text `job_id`, is answered with a `log` message
  beginning "Protocol error" and ignored; the worker keeps serving.
- A job the disk cannot hold ends with `job_failed` carrying `disk_full: true` (an optional
  field, absent from older engines) and a sentence naming the folder and the sizes; the host
  then stops the rest of the queue.
- An engine that lists the `stage_results` feature accepts `stage_picture_max_dimension`
  (64 to 2048) on a `job_request`. Each step before the main model then ends with a
  `stage_result` (`job_id`, `stage_index`, `stage_count`, `step`, `fix`, `skipped`, `note`,
  `width`, `height`, `jpeg_base64`, `preview_width`, `preview_height`, `image_path`), sent
  before the next step's `stage_started`: the step's picture as a JPEG of at most that size,
  or, for a step that was skipped, no picture and the reason in `note`. Without the field
  the engine sends no `stage_result`. `stage_started` and `stage_completed` carry the
  optional `step` (the step's id) and `fix` (`""` when the stage fixes nothing).
  With `stage_results_directory` as well, the engine writes each step's picture there at full
  size as `<stage_index>.png` (8-bit RGB, with alpha when the picture has it, sRGB) and names
  it in `image_path`; the files are complete before `job_completed`. A step whose file cannot
  be written has an empty `image_path`; the job goes on. A cancelled or failed job leaves no
  partial file.
- An engine that lists the feature `patch_preview` answers `patch_request` (a square of one
  photo, run through the plan the request carries) with `patch_started`, then one
  `patch_stage` per step (`running`, then `done` with a lossless PNG, or `skipped` with a
  `note`), then `patch_completed`; `patch_failed` and `patch_cancelled` end it otherwise. It
  is taken by the worker's main loop like a job, so it never overlaps one, and
  `cancel_request` with the `patch_id` stops it. A `patch_request` still waiting when a
  newer one for the same `slot` arrives is dropped and answered with `patch_cancelled`.
- Arbitrary worker stdout is never interpreted as a shell command.
- Model paths and media bytes stay on the local machine.
- Catalog-pinned checkpoints are verified by size and SHA-256. A custom
  pickle-based checkpoint is accepted only when that individual job carries
  the explicit `allow_unverified_checkpoint` acknowledgement. Desktop jobs also carry
  `approved_checkpoint_sha256` for the chosen custom file, checked before loading it.

The JSON Schema is the language-neutral contract. Update its
`x-protocol-version`, the Python constants, Rust constant, and compatibility
tests together for a breaking protocol change.
