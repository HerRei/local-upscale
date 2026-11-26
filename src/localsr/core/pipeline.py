"""Validation and progress math for bounded multi-stage image recipes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .steps import (
    DEFAULT_FIX_ORDER,
    FACE_STAGE_KIND,
    FIX_KINDS,
    FIX_STAGE_KINDS,
    IMAGE_PIPELINE_ROLES,
    IMAGE_PIPELINE_STAGE_KINDS,
    IMAGE_PRIMARY_STAGE_KINDS,
    LIMITS,
    parameter_for,
)


@dataclass(frozen=True)
class PipelineStage:
    kind: str
    model_id: str
    model_path: str
    fidelity: float | None = None
    execution: str = "sequential"
    role: str = ""
    fix: str | None = None


# Up to one fix per kind runs before the main model.
MAX_FIX_STAGES = LIMITS.max_fix_stages
_FACE_FIDELITY = parameter_for("face_restore", "fidelity")
_FACE_FIDELITY_MIN = 0.0 if _FACE_FIDELITY.minimum is None else float(_FACE_FIDELITY.minimum)
_FACE_FIDELITY_MAX = 1.0 if _FACE_FIDELITY.maximum is None else float(_FACE_FIDELITY.maximum)
_FIX_LIST_TEXT = (
    ", ".join(DEFAULT_FIX_ORDER[:-1]) + f" or {DEFAULT_FIX_ORDER[-1]}"
    if len(DEFAULT_FIX_ORDER) > 1
    else "".join(DEFAULT_FIX_ORDER)
)


class PipelineStageError(RuntimeError):
    def __init__(self, stage: PipelineStage, message: str):
        self.stage = stage
        super().__init__(f"{stage.kind} stage ({stage.model_id}) failed: {message}")


@dataclass(frozen=True)
class ImagePipeline:
    """Validated execution order shared by legacy and staged image requests."""

    stages: tuple[PipelineStage, ...]
    primary_index: int | None

    @property
    def primary(self) -> PipelineStage | None:
        return None if self.primary_index is None else self.stages[self.primary_index]

    @property
    def preprocess_stages(self) -> tuple[PipelineStage, ...]:
        return self.stages if self.primary_index is None else self.stages[: self.primary_index]

    @property
    def face(self) -> PipelineStage | None:
        if self.primary_index is None or not self.stages:
            return None
        return self.stages[-1] if self.stages[-1].kind == FACE_STAGE_KIND else None


def resolve_image_pipeline(
    *,
    raw_stages: object,
    model_path: str,
    output_scale: int | None = None,
    face_model_path: str | None = None,
    face_fidelity: float = 0.7,
) -> ImagePipeline:
    """Resolve old requests and check stage paths before loading any model."""
    if output_scale == 1 and not model_path and _is_fix_only_stages(raw_stages):
        stages = _parse_fix_only_stages(raw_stages)
        return ImagePipeline(stages, primary_index=None)

    stages = parse_pipeline_stages(raw_stages)
    if not stages:
        stages = (
            PipelineStage(
                kind="restore" if output_scale == 1 else "upscale",
                model_id="legacy-primary",
                model_path=model_path,
            ),
        )
        if face_model_path:
            stages += (
                PipelineStage(
                    kind="face_restore",
                    model_id="legacy-face",
                    model_path=face_model_path,
                    fidelity=face_fidelity,
                    execution="fused-with-upscale",
                ),
            )
    primary_index = _primary_index(stages)
    pipeline = ImagePipeline(stages, primary_index)
    assert pipeline.primary is not None
    if output_scale == 1 and pipeline.primary.kind == "upscale":
        # At 1× in Upscale, drop the upscaling stage (and any fused face pass)
        # so the engine never loads or runs an SR model only to resize back.
        return ImagePipeline(pipeline.preprocess_stages, primary_index=None)
    if Path(pipeline.primary.model_path) != Path(model_path):
        raise ValueError("The primary pipeline stage does not match the authenticated job model.")
    if pipeline.face is not None and (face_model_path or "") != pipeline.face.model_path:
        raise ValueError("The face pipeline stage does not match the authenticated face model.")
    return pipeline


def _is_fix_only_stages(raw: object) -> bool:
    if not isinstance(raw, list):
        return False
    return all(
        isinstance(item, dict) and item.get("kind") in FIX_STAGE_KINDS and item.get("role") == "fix"
        for item in raw
    )


def _parse_fix_only_stages(raw: object) -> tuple[PipelineStage, ...]:
    if not isinstance(raw, list) or not raw:
        return ()
    if len(raw) > MAX_FIX_STAGES:
        raise ValueError(f"Only {MAX_FIX_STAGES} restoration/deblock stages may run.")
    stages = [_parse_stage_item(item) for item in raw]
    fixes = [stage.fix for stage in stages if stage.fix is not None]
    if len(set(fixes)) != len(fixes):
        raise ValueError("Each fix kind may run only once.")
    return tuple(stages)


def _parse_stage_item(item: object) -> PipelineStage:
    if not isinstance(item, dict):
        raise ValueError("Every pipeline stage must be an object.")
    kind = str(item.get("kind") or "")
    if kind not in IMAGE_PIPELINE_STAGE_KINDS:
        raise ValueError(f"Unsupported image pipeline stage: {kind or '<empty>'}")
    model_id = str(item.get("model_id") or "")
    model_path = str(item.get("model_path") or "")
    if not model_id or not model_path:
        raise ValueError(f"The {kind} stage requires a model ID and model path.")
    fidelity = item.get("fidelity")
    parsed_fidelity = None if fidelity is None else float(fidelity)
    if (
        parsed_fidelity is not None
        and not _FACE_FIDELITY_MIN <= parsed_fidelity <= _FACE_FIDELITY_MAX
    ):
        raise ValueError("Face restoration fidelity must be between 0 and 1.")
    fix = item.get("fix")
    if fix is not None and (
        not isinstance(fix, str) or fix not in FIX_KINDS or kind not in FIX_STAGE_KINDS
    ):
        raise ValueError(f"Only a restoration stage may name a fix: {_FIX_LIST_TEXT}.")
    return PipelineStage(
        kind=kind,
        model_id=model_id,
        model_path=model_path,
        fidelity=parsed_fidelity,
        execution=str(item.get("execution") or "sequential"),
        role=str(item.get("role") or ""),
        fix=fix,
    )


def parse_pipeline_stages(raw: object) -> tuple[PipelineStage, ...]:
    if not isinstance(raw, list) or not raw:
        return ()
    if len(raw) > LIMITS.max_image_pipeline_stages:
        raise ValueError(
            f"Image recipes support at most {LIMITS.max_image_pipeline_stages} bounded stages."
        )
    stages: list[PipelineStage] = [_parse_stage_item(item) for item in raw]

    kinds = [stage.kind for stage in stages]
    if any(stage.role not in IMAGE_PIPELINE_ROLES for stage in stages):
        raise ValueError("Unknown pipeline stage role.")
    primary_index = _primary_index(stages)
    if primary_index > MAX_FIX_STAGES:
        raise ValueError(
            f"Only {MAX_FIX_STAGES} restoration/deblock stages may precede the primary stage."
        )
    if any(kind not in FIX_STAGE_KINDS for kind in kinds[:primary_index]):
        raise ValueError("Only restoration or deblock may precede the primary stage.")
    fixes = [stage.fix for stage in stages if stage.fix is not None]
    if len(set(fixes)) != len(fixes):
        raise ValueError("Each fix kind may run only once.")
    if any(stage.role not in {"", "fix"} for stage in stages[:primary_index]):
        raise ValueError("Only fix roles may precede the primary stage.")
    if stages[primary_index].role not in {"", "primary"}:
        raise ValueError("The primary stage must have the primary role.")
    trailing = kinds[primary_index + 1 :]
    if trailing not in ([], [FACE_STAGE_KIND]):
        raise ValueError("Face restoration is the only optional stage after the primary stage.")
    if trailing and (
        kinds[primary_index] != "upscale" or stages[-1].execution != "fused-with-upscale"
    ):
        raise ValueError("The current face checkpoint must be fused with its paired upscale stage.")
    if trailing and stages[-1].role not in {"", "face"}:
        raise ValueError("The trailing stage must have the face role.")
    return tuple(stages)


def _primary_index(stages: list[PipelineStage] | tuple[PipelineStage, ...]) -> int:
    """The main model's stage: the one named primary, else the upscaler.

    Requests from hosts without several fixes name no roles; their restore-only job has
    one restore stage, which is the primary.
    """
    explicit = [i for i, stage in enumerate(stages) if stage.role == "primary"]
    upscales = [i for i, stage in enumerate(stages) if stage.kind == "upscale"]
    if len(upscales) > 1:
        raise ValueError("An image recipe may contain only one upscale stage.")
    if explicit:
        if len(explicit) != 1 or (upscales and explicit != upscales):
            raise ValueError("An image recipe must contain exactly one primary stage.")
        index = explicit[0]
        if stages[index].kind not in IMAGE_PRIMARY_STAGE_KINDS:
            raise ValueError("The primary stage must restore or upscale.")
        return index
    if upscales:
        return upscales[0]
    if any(stage.role for stage in stages):
        raise ValueError("A restore-only recipe must name its primary stage.")
    restores = [i for i, stage in enumerate(stages) if stage.kind == "restore"]
    if len(restores) != 1:
        raise ValueError("A restore-only recipe must contain exactly one restore stage.")
    return restores[0]
