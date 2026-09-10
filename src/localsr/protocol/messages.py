import json
from dataclasses import asdict, dataclass
from typing import Protocol

# The worker protocol is deliberately versioned independently from the desktop
# application.  Version 1 is a backwards-compatible superset of the original
# Slint JSON-lines messages: old clients may continue to wait for worker_ready,
# while newer hosts negotiate capabilities with HandshakeRequest.
PROTOCOL_VERSION = 1
MIN_PROTOCOL_VERSION = 1


class WorkerMessage(Protocol):
    """A serializable event accepted by the worker's JSON-lines transport."""

    def to_json(self) -> str: ...


@dataclass
class HandshakeRequest:
    client_name: str
    client_version: str
    protocol_version: int = PROTOCOL_VERSION

    def to_json(self) -> str:
        return json.dumps({"type": "handshake_request", "data": asdict(self)})


@dataclass
class EngineInfo:
    protocol_version: int
    minimum_protocol_version: int
    engine_id: str
    engine_version: str
    features: list[str]
    model_formats: list[str]
    video_engines: list[str]

    def to_json(self) -> str:
        return json.dumps({"type": "engine_info", "data": asdict(self)})


@dataclass
class ProtocolError:
    code: str
    message: str
    supported_protocol_version: int = PROTOCOL_VERSION

    def to_json(self) -> str:
        return json.dumps({"type": "protocol_error", "data": asdict(self)})


@dataclass
class JobRequest:
    job_id: str
    image_path: str
    model_path: str
    output_path: str
    output_format: str
    device: str
    tile_size: int
    halo: int
    precision: str
    jpeg_quality: int
    preserve_metadata: bool
    safe_memory: bool
    output_scale: int | None = None
    face_model_path: str | None = None
    face_fidelity: float = 0.7
    allow_unverified_checkpoint: bool | None = None
    preview_enabled: bool = True
    preview_max_fps: float = 2.0
    preview_max_dimension: int = 320
    scratch_directory: str | None = None
    stages: list[dict[str, object]] | None = None

    def to_json(self) -> str:
        return json.dumps({"type": "job_request", "data": asdict(self)})


@dataclass
class VideoJobRequest:
    """Run frame-by-frame or temporal video restoration in the worker.

    ``model_kind=spandrel_image`` uses the normal tiled image engine for each
    frame. Other kinds route to a vendored temporal engine with an externally
    downloaded, checksum-pinned bundle.
    """

    job_id: str
    video_path: str
    model_path: str
    output_video_path: str
    container: str
    crf: int
    fps: float | None
    device: str
    tile_size: int
    halo: int
    precision: str
    safe_memory: bool
    keyframe_interval: int | None = None
    start_frame: int | None = None
    end_frame: int | None = None
    face_model_path: str | None = None
    face_fidelity: float = 0.7
    deflicker: bool = False
    deflicker_window: int = 3
    # Temporal-engine routing. "spandrel_image" is the frame-by-frame path;
    # any other kind names vendored engine code and carries its bundle.
    model_kind: str = "spandrel_image"
    bundle_dir: str | None = None
    temporal_window: int = 0
    temporal_overlap: int = 0
    # Target shortest-edge in pixels for resolution-based engines (SeedVR2
    # has no fixed scale factor). 0 lets the engine keep the input size.
    target_resolution: int = 0
    # Frame-by-frame engines run at the checkpoint's native scale, then
    # downsample each restored frame when a smaller 2×/3× output is requested.
    output_scale: int | None = None
    allow_unverified_checkpoint: bool | None = None
    preview_enabled: bool = True
    preview_max_fps: float = 2.0
    preview_max_dimension: int = 320

    hdr_mode: str = "reject"

    def to_json(self) -> str:
        return json.dumps({"type": "video_job_request", "data": asdict(self)})


@dataclass
class BenchmarkRequest:
    job_id: str
    model_path: str
    model_id: str
    model_name: str
    device: str
    workload: str = "v1"

    def to_json(self) -> str:
        return json.dumps({"type": "benchmark_request", "data": asdict(self)})


@dataclass
class InspectRequest:
    model_path: str

    def to_json(self) -> str:
        return json.dumps({"type": "inspect_request", "data": asdict(self)})


@dataclass
class CapabilitiesRequest:
    def to_json(self) -> str:
        return json.dumps({"type": "capabilities_request", "data": {}})


@dataclass
class PreviewRequest:
    image_path: str
    max_dimension: int = 1600

    def to_json(self) -> str:
        return json.dumps({"type": "preview_request", "data": asdict(self)})


@dataclass
class MediaProbeRequest:
    media_path: str
    max_dimension: int = 1600

    def to_json(self) -> str:
        return json.dumps({"type": "media_probe_request", "data": asdict(self)})


@dataclass
class CancelRequest:
    job_id: str

    def to_json(self) -> str:
        return json.dumps({"type": "cancel_request", "data": asdict(self)})


@dataclass
class ShutdownRequest:
    def to_json(self) -> str:
        return json.dumps({"type": "shutdown_request", "data": {}})


@dataclass
class WorkerReady:
    def to_json(self) -> str:
        return json.dumps({"type": "worker_ready", "data": {}})


@dataclass
class ModelInfo:
    architecture: str
    scale: int
    in_channels: int
    out_channels: int
    tiling_supported: bool
    half_supported: bool
    size_requirements_min: int
    size_requirements_mult: int
    filename: str
    warnings: list[str]
    size_requirements_square: bool = False
    parameter_count: int = 0
    model_file_size: int = 0

    def to_json(self) -> str:
        return json.dumps({"type": "model_info", "data": asdict(self)})


@dataclass
class CapabilitiesInfo:
    system_ram_total: int
    system_ram_available: int
    devices: list[dict]
    system_memory_pressure_percent: float = 0.0
    system_memory_pressure_level: str = "unknown"
    system_compressed_memory: int = 0
    system_swap_total: int = 0
    system_swap_used: int = 0

    def to_json(self) -> str:
        return json.dumps({"type": "capabilities_info", "data": asdict(self)})


@dataclass
class JobStarted:
    job_id: str

    def to_json(self) -> str:
        return json.dumps({"type": "job_started", "data": asdict(self)})


@dataclass
class StageStarted:
    job_id: str
    stage_index: int
    stage_count: int
    stage_kind: str
    model_id: str

    def to_json(self) -> str:
        return json.dumps({"type": "stage_started", "data": asdict(self)})


@dataclass
class StageProgress:
    job_id: str
    stage_index: int
    stage_count: int
    stage_kind: str
    completed_units: int
    total_units: int
    percentage: float

    def to_json(self) -> str:
        return json.dumps({"type": "stage_progress", "data": asdict(self)})


@dataclass
class StageCompleted:
    job_id: str
    stage_index: int
    stage_count: int
    stage_kind: str

    def to_json(self) -> str:
        return json.dumps({"type": "stage_completed", "data": asdict(self)})


@dataclass
class ProgressUpdate:
    job_id: str
    completed_tiles: int
    total_tiles: int
    percentage: float
    elapsed_seconds: float
    estimated_remaining_seconds: float
    active_tile_size: int
    device_free_memory: int = 0
    device_allocated_memory: int = 0
    system_ram_available: int = 0
    system_memory_pressure_percent: float = 0.0
    system_memory_pressure_level: str = "unknown"
    system_compressed_memory: int = 0
    system_swap_used: int = 0
    mps_tensor_allocated_memory: int = 0
    mps_driver_allocated_memory: int = 0
    mps_recommended_max_memory: int = 0

    def to_json(self) -> str:
        return json.dumps({"type": "progress", "data": asdict(self)})


@dataclass
class PreviewReady:
    image_path: str
    width: int
    height: int
    jpeg_base64: str

    def to_json(self) -> str:
        return json.dumps({"type": "preview_ready", "data": asdict(self)})


@dataclass
class PreviewFailed:
    image_path: str
    error_message: str

    def to_json(self) -> str:
        return json.dumps({"type": "preview_failed", "data": asdict(self)})


@dataclass
class MediaInfo:
    media_path: str
    media_kind: str
    width: int
    height: int
    frame_count: int = 0
    fps: float = 0.0
    duration_seconds: float = 0.0
    jpeg_base64: str = ""

    hdr_format: str = ""
    audio_warning: str = ""

    def to_json(self) -> str:
        return json.dumps({"type": "media_info", "data": asdict(self)})


@dataclass
class MediaProbeProgress:
    media_path: str
    stage: str

    def to_json(self) -> str:
        return json.dumps({"type": "media_probe_progress", "data": asdict(self)})


@dataclass
class MediaProbeFailed:
    media_path: str
    error_message: str

    def to_json(self) -> str:
        return json.dumps({"type": "media_probe_failed", "data": asdict(self)})


@dataclass
class TileUpdate:
    job_id: str
    phase: str
    completed_tiles: int
    total_tiles: int
    output_x: int
    output_y: int
    output_width: int
    output_height: int
    image_width: int
    image_height: int
    active_tile_size: int
    jpeg_base64: str = ""
    frame_index: int = -1

    def to_json(self) -> str:
        return json.dumps({"type": "tile_update", "data": asdict(self)})


@dataclass
class BenchmarkTile(TileUpdate):
    device: str = ""
    scene_id: str = ""

    def to_json(self) -> str:
        return json.dumps({"type": "benchmark_tile", "data": asdict(self)})


@dataclass
class LivePreviewFrame:
    job_id: str
    sequence: int
    preview_kind: str
    jpeg_base64: str
    output_x: int = 0
    output_y: int = 0
    output_width: int = 0
    output_height: int = 0
    image_width: int = 0
    image_height: int = 0
    frame_index: int = -1

    def to_json(self) -> str:
        return json.dumps({"type": "live_preview_frame", "data": asdict(self)})


@dataclass
class LivePreviewWarning:
    job_id: str
    message: str

    def to_json(self) -> str:
        return json.dumps({"type": "live_preview_warning", "data": asdict(self)})


@dataclass
class LogMessage:
    level: str
    message: str

    def to_json(self) -> str:
        return json.dumps({"type": "log", "data": asdict(self)})


@dataclass
class WarningMessage:
    message: str

    def to_json(self) -> str:
        return json.dumps({"type": "warning", "data": asdict(self)})


@dataclass
class JobCompleted:
    job_id: str
    elapsed_seconds: float = 0.0
    inference_seconds: float = 0.0
    output_path: str = ""

    def to_json(self) -> str:
        return json.dumps({"type": "job_completed", "data": asdict(self)})


@dataclass
class JobCancelled:
    job_id: str

    def to_json(self) -> str:
        return json.dumps({"type": "job_cancelled", "data": asdict(self)})


@dataclass
class JobFailed:
    job_id: str
    error_message: str

    def to_json(self) -> str:
        return json.dumps({"type": "job_failed", "data": asdict(self)})


@dataclass
class BenchmarkPreview:
    job_id: str
    render: dict

    def to_json(self) -> str:
        return json.dumps({"type": "benchmark_preview", "data": asdict(self)})


@dataclass
class BenchmarkStarted:
    job_id: str
    workload_version: str
    warmup_count: int
    measured_frame_count: int

    def to_json(self) -> str:
        return json.dumps({"type": "benchmark_started", "data": asdict(self)})


@dataclass
class BenchmarkStageStarted:
    job_id: str
    device: str
    stage: str
    stage_index: int
    stage_count: int
    percentage: float

    def to_json(self) -> str:
        return json.dumps({"type": "benchmark_stage_started", "data": asdict(self)})


@dataclass
class BenchmarkStageProgress:
    job_id: str
    device: str
    stage: str
    stage_index: int
    stage_count: int
    completed_units: int
    total_units: int
    percentage: float

    def to_json(self) -> str:
        return json.dumps({"type": "benchmark_stage_progress", "data": asdict(self)})


@dataclass
class BenchmarkStageCompleted:
    job_id: str
    device: str
    stage: str
    stage_index: int
    stage_count: int
    percentage: float

    def to_json(self) -> str:
        return json.dumps({"type": "benchmark_stage_completed", "data": asdict(self)})


@dataclass
class BenchmarkProgress:
    job_id: str
    completed_frames: int
    total_frames: int
    percentage: float
    # Optional human-readable phase label ("mps:s2-gallery", "cpu:warmup").
    # Absent in v1 progress; the host may surface it or ignore it.
    stage: str = ""

    def to_json(self) -> str:
        return json.dumps({"type": "benchmark_progress", "data": asdict(self)})


@dataclass
class BenchmarkCompleted:
    job_id: str
    result: dict[str, object]

    def to_json(self) -> str:
        return json.dumps({"type": "benchmark_completed", "data": asdict(self)})


@dataclass
class BenchmarkFailed:
    job_id: str
    error_message: str

    def to_json(self) -> str:
        return json.dumps({"type": "benchmark_failed", "data": asdict(self)})


@dataclass
class BenchmarkCancelled:
    job_id: str

    def to_json(self) -> str:
        return json.dumps({"type": "benchmark_cancelled", "data": asdict(self)})


@dataclass
class VideoStageProgress:
    job_id: str
    stage: str
    completed: int = 0
    total: int = 0
    frame_index: int = 0
    total_frames: int = 0
    elapsed_seconds: float = 0.0
    frames_processed: int = 0
    estimated_remaining_seconds: float = 0.0

    def to_json(self) -> str:
        return json.dumps({"type": "video_stage_progress", "data": asdict(self)})


@dataclass
class VideoFrameStarted:
    job_id: str
    frame_index: int
    total_frames: int

    def to_json(self) -> str:
        return json.dumps({"type": "video_frame_started", "data": asdict(self)})


@dataclass
class VideoFrameCompleted:
    job_id: str
    frame_index: int
    total_frames: int
    frames_processed: int
    elapsed_seconds: float
    estimated_remaining_seconds: float
    jpeg_base64: str = ""

    def to_json(self) -> str:
        return json.dumps({"type": "video_frame_completed", "data": asdict(self)})


@dataclass
class VideoTileProgress:
    job_id: str
    frame_index: int
    total_frames: int
    completed_tiles: int
    total_tiles: int
    elapsed_seconds: float
    estimated_remaining_seconds: float | None
    active_tile_size: int

    def to_json(self) -> str:
        return json.dumps({"type": "video_tile_progress", "data": asdict(self)})


@dataclass
class VideoJobCompleted:
    job_id: str
    output_path: str
    frames_processed: int
    elapsed_seconds: float = 0.0
    inference_seconds: float = 0.0

    def to_json(self) -> str:
        return json.dumps({"type": "video_job_completed", "data": asdict(self)})


@dataclass
class DetectFacesRequest:
    image_path: str

    def to_json(self) -> str:
        return json.dumps({"type": "detect_faces_request", "data": asdict(self)})


@dataclass
class FacesDetected:
    image_path: str
    boxes: list[dict]  # [{"x": int, "y": int, "w": int, "h": int, "confidence": float}, ...]

    def to_json(self) -> str:
        return json.dumps({"type": "faces_detected", "data": asdict(self)})


@dataclass
class FaceDetectionUnavailable:
    image_path: str
    message: str

    def to_json(self) -> str:
        return json.dumps({"type": "face_detection_unavailable", "data": asdict(self)})
