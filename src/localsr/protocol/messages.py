import json
from dataclasses import asdict, dataclass


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

    def to_json(self) -> str:
        return json.dumps({"type": "job_request", "data": asdict(self)})


@dataclass
class VideoJobRequest:
    """Placeholder for the upcoming video upscale pipeline.

    The GUI sends this once video support is wired into the worker. The worker
    will decode frames in-process (PyAV), run a temporal SR model, and encode
    the result. The fields mirror JobRequest plus the parameters the video
    pipeline needs that the image pipeline does not.
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

    def to_json(self) -> str:
        return json.dumps({"type": "video_job_request", "data": asdict(self)})


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

    def to_json(self) -> str:
        return json.dumps({"type": "tile_update", "data": asdict(self)})


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
