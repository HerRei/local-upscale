# ruff: noqa
# Reviewed vulture allowlist for `vulture src scripts packaging ci/vulture_whitelist.py`.
# Every name here is used in a way static analysis cannot see. Add a name only
# after confirming that; delete code instead when it is genuinely unused.

# Serialized dataclass fields: protocol messages, benchmark/estimate reports and
# catalog entries are emitted through dataclasses.asdict() or the catalog export.
allow_unverified_checkpoint
author
client_name
client_version
code
completed_frames
completed_tiles
cpu_score
cv_percent
device_memory_bytes
device_memory_remaining
disk_remaining
end_to_end_fps
engine_id
engine_kind
engine_version
error_message
estimated_remaining_seconds
image_path
input_height
input_width
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
oom
output_temporary_directory
p05_ms
p95_inference_ms
pair_with
peak_device_memory_bytes
preview_enabled
preview_max_dimension
preview_max_fps
process_ram
processed_megapixels_per_second
processing_stage
protocol_version
rationale
requires_download
scratch_directory
shared_memory
source_url
supported_protocol_version
system_compressed_memory
system_memory_bytes
system_memory_pressure_level
system_memory_pressure_percent
system_memory_remaining
system_ram_available
system_ram_total
system_swap_total
system_swap_used
target_resolution
temporal_window
thermal_state
used_bytes
video_engines
video_low_memory
vram_estimate_mb
warmup_count
working_disk_bytes
workload_version
VIDEO  # ModelPurpose enum member used by catalog data and the desktop

# Worker protocol request contracts exercised by tests and external hosts.
HandshakeRequest
BenchmarkRequest
MediaProbeRequest

# Framework callbacks and third-party object attributes.
_.forward  # torch.nn.Module
_.do_GET  # http.server handler
_.do_POST  # http.server handler
exc_type  # __exit__ protocol
exc_val  # __exit__ protocol
exc_tb  # __exit__ protocol
_.pix_fmt  # PyAV stream
_.codec_tag  # PyAV stream
_.bit_rate  # PyAV stream
_.thread_type  # PyAV codec context
_.threads  # PyAV codec context
_.execution_mode  # onnxruntime.SessionOptions
_.enable_mem_pattern  # onnxruntime.SessionOptions
_.intra_op_num_threads  # onnxruntime.SessionOptions
_.graph_optimization_level  # onnxruntime.SessionOptions
_.enable_profiling  # onnxruntime.SessionOptions
_.profile_file_prefix  # onnxruntime.SessionOptions
_.gid  # tarfile.TarInfo
_.gname  # tarfile.TarInfo
_.mtime  # tarfile.TarInfo
_.uname  # tarfile.TarInfo
_._model  # spandrel descriptor internals
cached_model  # tuple unpacking

# Legacy InferenceEngine compatibility fields, written but never read; removal candidates.
_._loaded_model
_._loaded_model_path

# PyInstaller hooks and spec-file imports.
hiddenimports
datas
metadata_names
collect_runtime  # packaging/tauri_worker.spec
filter_binaries  # packaging/tauri_worker.spec

# Public helpers kept by decision and exercised by tests.
get_model_by_id
clip_windows  # core/temporal.py scaffolding
stitch_clips
_.size_megabytes
_.file_size_bytes
_.total_size_bytes
_.get_path  # OutputWriter memmap path, used by tests
get_preset_model  # reference preset behavior mirrored by the desktop
resolve_settings_for_model

# Owned by the concurrent video codec work; review there.
_next_packet  # core/video_io.py
decode_frames  # core/video_io.py
face_area_ratio  # core/face_detection.py
active_tile  # core/video_pipeline.py
