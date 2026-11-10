"""Check a worker request against the bounds protocol/worker-protocol.schema.json publishes.

The desktop app validates its settings before it sends a request, but the worker is a
separate process with its own input: a value the schema forbids must end the job with
one clear sentence before any image is decoded or model loaded, not as a black picture,
a multi-gigabyte allocation or a raw Python error halfway through.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


class InvalidRequest(ValueError):
    """The request breaks the published protocol; the message is shown to the user."""


@dataclass(frozen=True)
class Field:
    name: str
    kind: str  # "str", "int", "number", "bool" or "choice"
    required: bool = False
    nullable: bool = False
    minimum: float | None = None
    maximum: float | None = None
    above: float | None = None  # exclusive lower bound
    choices: tuple[str, ...] = ()
    non_empty: bool = False


_PRECISIONS = ("fp32", "fp16", "bf16")

# Fields every image and video job carries.
_JOB_FIELDS = (
    Field("job_id", "str", required=True, non_empty=True),
    Field("model_path", "str", required=True),
    Field("device", "str", required=True, non_empty=True),
    Field("tile_size", "int", required=True, minimum=32, maximum=2048),
    Field("halo", "int", required=True, minimum=0, maximum=256),
    Field("precision", "choice", required=True, choices=_PRECISIONS),
    Field("safe_memory", "bool", required=True),
    Field("scratch_directory", "str", nullable=True, non_empty=True),
    Field("output_temporary_directory", "str", nullable=True, non_empty=True),
    Field("face_model_path", "str", nullable=True),
    Field("face_fidelity", "number", minimum=0, maximum=1),
    Field("allow_unverified_checkpoint", "bool", nullable=True),
    Field("preview_enabled", "bool"),
    Field("preview_max_fps", "number", above=0, maximum=5),
    Field("preview_max_dimension", "int", minimum=64, maximum=640),
    Field("neural_engine", "bool"),
)

# "jpeg" and "tiff" are the long spellings the encoder also takes.
_IMAGE_FORMATS = ("png", "jpg", "jpeg", "tif", "tiff", "webp", "heic")

IMAGE_JOB_FIELDS = _JOB_FIELDS + (
    Field("image_path", "str", required=True, non_empty=True),
    Field("output_path", "str", required=True, non_empty=True),
    Field("output_format", "choice", required=True, choices=_IMAGE_FORMATS),
    Field("jpeg_quality", "int", required=True, minimum=1, maximum=100),
    Field("preserve_metadata", "bool", required=True),
    Field("output_scale", "int", nullable=True, minimum=1, maximum=8),
)

VIDEO_JOB_FIELDS = _JOB_FIELDS + (
    Field("video_path", "str", required=True, non_empty=True),
    Field("output_video_path", "str", required=True, non_empty=True),
    Field("hdr_mode", "choice", choices=("reject", "tone_map", "preserve")),
    Field("container", "choice", choices=("mp4", "mkv", "mov")),
    Field(
        "video_codec",
        "choice",
        choices=("auto", "av1", "vp9", "ffv1", "h264", "hevc", "prores"),
    ),
    Field("external_ffmpeg", "str", nullable=True),
    Field("video_low_memory", "bool"),
    Field("crf", "int", minimum=0, maximum=51),
    Field("fps", "number", nullable=True, above=0),
    Field("keyframe_interval", "int", nullable=True, minimum=1),
    Field("start_frame", "int", nullable=True, minimum=0),
    Field("end_frame", "int", nullable=True, minimum=0),
    Field("deflicker", "bool"),
    Field("deflicker_window", "int", minimum=1, maximum=9),
    Field("model_kind", "choice", choices=("spandrel_image", "seedvr2")),
    Field("bundle_dir", "str", nullable=True),
    Field("temporal_window", "int", minimum=0),
    Field("temporal_overlap", "int", minimum=0),
    Field("target_resolution", "int", minimum=0),
    Field("output_scale", "int", nullable=True, minimum=1, maximum=8),
    Field(
        "interpolation",
        "choice",
        choices=("off", "x2", "x4", "fps60", "fps120"),
    ),
    Field("interpolation_order", "choice", choices=("after_upscale", "before_upscale")),
    Field("interpolation_model_id", "str", nullable=True),
    Field("interpolation_bundle_dir", "str", nullable=True),
)

PROBE_FIELDS = (
    Field("media_path", "str", required=True),
    Field("max_dimension", "int", minimum=64, maximum=2048),
)


def _describe(field: Field) -> str:
    if field.kind == "choice":
        return "one of " + ", ".join(field.choices)
    if field.kind == "bool":
        return "true or false"
    if field.kind == "str":
        return "a non-empty text" if field.non_empty else "text"
    noun = "a whole number" if field.kind == "int" else "a number"
    if field.minimum is not None and field.maximum is not None:
        return f"{noun} from {field.minimum:g} to {field.maximum:g}"
    if field.minimum is not None:
        return f"{noun} of at least {field.minimum:g}"
    if field.above is not None and field.maximum is not None:
        return f"{noun} above {field.above:g} and at most {field.maximum:g}"
    if field.above is not None:
        return f"{noun} above {field.above:g}"
    return noun


def _shown(value: Any) -> str:
    text = repr(value)
    return text if len(text) <= 60 else text[:57] + "..."


def _acceptable(field: Field, value: Any) -> bool:
    if field.kind == "str":
        return isinstance(value, str) and (bool(value) or not field.non_empty)
    if field.kind == "bool":
        return isinstance(value, bool)
    if field.kind == "choice":
        return isinstance(value, str) and value.lower() in field.choices
    if isinstance(value, bool):
        return False
    if field.kind == "int":
        if not isinstance(value, int):
            return False
    elif not isinstance(value, (int, float)) or not math.isfinite(value):
        return False
    if field.minimum is not None and value < field.minimum:
        return False
    if field.maximum is not None and value > field.maximum:
        return False
    return field.above is None or value > field.above


def validate_fields(data: Any, fields: tuple[Field, ...], request: str) -> None:
    """Raise InvalidRequest for the first field that is missing, mistyped or out of range."""
    if not isinstance(data, dict):
        raise InvalidRequest(f"The {request} has no data object.")
    for field in fields:
        if field.name not in data:
            if field.required:
                raise InvalidRequest(f'The {request} is missing "{field.name}".')
            continue
        value = data[field.name]
        if value is None and field.nullable:
            continue
        if not _acceptable(field, value):
            raise InvalidRequest(
                f'The {request} has an invalid "{field.name}": it must be '
                f"{_describe(field)}, not {_shown(value)}."
            )


def validate_image_job(data: Any) -> None:
    validate_fields(data, IMAGE_JOB_FIELDS, "image job request")


def validate_video_job(data: Any) -> None:
    validate_fields(data, VIDEO_JOB_FIELDS, "video job request")


def usable_job_id(data: Any) -> str | None:
    """The job id a terminal message can name, or None when the request has none."""
    job_id = data.get("job_id") if isinstance(data, dict) else None
    return job_id if isinstance(job_id, str) and job_id else None
