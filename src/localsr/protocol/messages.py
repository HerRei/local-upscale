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

    def to_json(self) -> str:
        return json.dumps({"type": "job_request", "data": asdict(self)})


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
    system_ram_available: int = 0

    def to_json(self) -> str:
        return json.dumps({"type": "progress", "data": asdict(self)})


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
