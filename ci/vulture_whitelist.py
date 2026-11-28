# ruff: noqa
# Vulture allowlist for `vulture src scripts packaging ci/vulture_whitelist.py`.
# Every name here is used in a way static analysis cannot see. Add a name only
# after confirming that; delete code instead when it is genuinely unused.

# Serialized dataclass fields: protocol messages, benchmark reports and
# catalog entries are emitted through dataclasses.asdict() or the catalog export.
VIDEO  # ModelPurpose enum member used by catalog data and the desktop
allow_unverified_checkpoint
approved_checkpoint_sha256
client_name
client_version
completed_frames
completed_tiles
cpu_score
cv_percent
end_to_end_fps
engine_kind
engine_version
estimated_remaining_seconds
gpu_suggestion
image_path
input_height
input_width
interpolation_bundle_dir
interpolation_model_id
keyframe_interval
lightning_lora_path  # EditJobRequest field, read by run_edit_job from the request dict
measured_frame_count
media_kind
median_inference_ms
min_unified_memory_gb
min_vram_gb
minimum_protocol_version
model_formats
mps_driver_allocated_memory
mps_recommended_max_memory
mps_tensor_allocated_memory
npu_hint
oom
output_temporary_directory
p05_ms
p95_inference_ms
pair_with
peak_device_memory_bytes
preview_enabled
preview_max_dimension
preview_max_fps
# Result preview fields serialized by asdict() and consumed by the Rust host.
preview_width
preview_height
process_ram
processed_megapixels_per_second
processing_stage
restart_required
disk_full
scratch_directory
shared_memory
source_url
supported_protocol_version
system_compressed_memory
system_memory_pressure_level
system_memory_pressure_percent
system_ram_available
system_ram_total
system_swap_total
system_swap_used
target_resolution
temporal_window
thermal_state
video_engines
video_low_memory
video_model_id
vram_estimate_mb
warmup_count
workload_version

# Worker protocol request contracts exercised by tests and external hosts.
HandshakeRequest
BenchmarkRequest
MediaProbeRequest
RegionRequest
IMAGE_JOB_FIELDS
VIDEO_JOB_FIELDS
EDIT_JOB_FIELDS
validate_edit_job
request_id
target_width
target_height
png_base64

# Framework callbacks and third-party object attributes.
_.MAX_IMAGE_PIXELS  # Pillow reads this only in the isolated export-preview child
_.do_GET  # http.server handler
_.log_message  # http.server handler
exc_type  # __exit__ protocol
exc_val  # __exit__ protocol
exc_tb  # __exit__ protocol
_.bit_rate  # PyAV stream
_.thread_type  # PyAV codec context
_.execution_mode  # onnxruntime.SessionOptions
_.enable_mem_pattern  # onnxruntime.SessionOptions
_.intra_op_num_threads  # onnxruntime.SessionOptions
_.graph_optimization_level  # onnxruntime.SessionOptions
_.enable_profiling  # onnxruntime.SessionOptions
_.profile_file_prefix  # onnxruntime.SessionOptions
_.uid  # tarfile.TarInfo
_.gid  # tarfile.TarInfo
_.gname  # tarfile.TarInfo
_.mtime  # tarfile.TarInfo
_.uname  # tarfile.TarInfo
_._model  # spandrel descriptor internals

# PyInstaller hooks and spec-file imports.
hiddenimports
datas
metadata_names
collect_runtime  # packaging/tauri_worker.spec
filter_binaries  # packaging/tauri_worker.spec

# Public helpers kept by decision and exercised by tests.
IMAGE_PIPELINE_STAGE_KINDS  # core/steps.py: tested in test_steps_registry.py
_.size_megabytes
_.file_size_bytes
_.total_size_bytes

# Threading server knobs read by the stdlib, never by our own code.
daemon_threads  # socketserver.ThreadingMixIn, packaging/hosting/stats
_.daemon_threads  # set on a ThreadingHTTPServer instance

# Dataclass field serialized through asdict(); removing it breaks the protocol.
_.media  # protocol/messages.py CapabilitiesInfo
EditJobRequest  # protocol/messages.py: the edit request shape, built by the desktop app
runtime_path  # EditJobRequest
accepted_terms  # EditJobRequest
custom_files  # EditJobRequest


# ctypes structure field written for Windows to read (core/gpu_guard.py job object).
LimitFlags  # JOBOBJECT_BASIC_LIMIT_INFORMATION
_fields_  # ctypes Structure layout (scripts/verify_appimage_gstreamer.py GList)

ImageBatchRequest  # protocol/messages.py: additive batch envelope built by the Rust host

# Catalog field exported to the desktop catalog with asdict() and read by the host and the front end.
time_factor

# ONNX Runtime session options, read by onnxruntime itself (core/audio_denoise.py).
log_severity_level
inter_op_num_threads

# Protocol field set by the Rust host for the audio cleanup model (protocol/messages.py).
audio_denoise_model_id

# Fields of a kept photo's job_completed, read by the host from the JSON (protocol/messages.py).
finished_tiles
total_tiles

# Step pictures (engine feature stage_results): the request field the Rust host sets and
# stage_result's skipped flag it reads (protocol/messages.py).
stage_picture_max_dimension
stage_results_directory
skipped

# Vendored mlx_flashvsr symbols kept unmodified for parity with the upstream port.
compatibility_mode
return_KV
_cross_kv_initialized
clear_cross_kv
timesteps
last_lq_cur_idx
DecoderResult

# Public dataclass/engine attributes on FlashVSREngine and FlashVSRTilePlan (inspected by tests).
padded_width
padded_height
tile_width
tile_height
max_tile_area

# localsr-benchmark-v3 result fields (serialized with asdict())
status_note
recommended_device
recommended_device_name
recommended_device_seconds_per_input_mp
recommended_cpu_threads
cpu_threads
cpu_avx2
framerate  # PyAV codec_context.framerate: set to 0/1 so Matroska writes no DefaultDuration

# Preview patch (engine feature patch_preview): the request message the Rust host sends,
# parsed through the schema by request_validation.validate_patch_request.
PatchRequest
