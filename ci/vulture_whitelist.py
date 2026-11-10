# ruff: noqa
# Vulture allowlist for `vulture src scripts packaging ci/vulture_whitelist.py`.
# Every name here is used in a way static analysis cannot see. Add a name only
# after confirming that; delete code instead when it is genuinely unused.

# Serialized dataclass fields: protocol messages, benchmark reports and
# catalog entries are emitted through dataclasses.asdict() or the catalog export.
VIDEO  # ModelPurpose enum member used by catalog data and the desktop
allow_unverified_checkpoint
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


# ctypes structure field written for Windows to read (core/gpu_guard.py job object).
LimitFlags  # JOBOBJECT_BASIC_LIMIT_INFORMATION

ImageBatchRequest  # protocol/messages.py: additive batch envelope built by the Rust host

# Catalog field exported to the desktop catalog with asdict() and read by the host and the front end.
time_factor
