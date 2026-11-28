"""Validation and progress math for bounded multi-stage image recipes."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .image_formats import require_ordinary_source
from .steps import (
    DEFAULT_FIX_ORDER,
    DEV_STEPS_ENV,
    FACE_STAGE_KIND,
    FIX_KINDS,
    FIX_STAGE_KINDS,
    IMAGE_PIPELINE_ROLES,
    IMAGE_PRIMARY_STAGE_KINDS,
    LIMITS,
    PHASES,
    STEPS,
    StepDefinition,
    dev_steps_enabled,
    is_step_stage,
    parameter_for,
    resolve_step,
    validate_step_params,
)


@dataclass(frozen=True)
class PipelineStage:
    kind: str
    model_id: str = ""
    model_path: str = ""
    fidelity: float | None = None
    execution: str = "sequential"
    role: str = ""
    fix: str | None = None
    step_id: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    mask_path: str | None = None

    def __post_init__(self) -> None:
        if not self.step_id:
            resolved = self.fix or ("jpeg" if self.kind == "deblock" else self.kind)
            object.__setattr__(self, "step_id", resolved)


_PHASE_INDEX: dict[str, int] = {phase: index for index, phase in enumerate(PHASES)}
_PRIMARY_PHASE = _PHASE_INDEX["primary"]
_FACE_PHASE = _PHASE_INDEX["face"]


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
        label = stage.model_id or stage.step_id or stage.kind
        super().__init__(f"{stage.kind} stage ({label}) failed: {message}")


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
        if self.primary_index is None:
            return None
        nxt = self.primary_index + 1
        if nxt < len(self.stages) and self.stages[nxt].kind == FACE_STAGE_KIND:
            return self.stages[nxt]
        return None

    @property
    def postprocess_stages(self) -> tuple[PipelineStage, ...]:
        if self.primary_index is None:
            return ()
        start = self.primary_index + 1 + (1 if self.face is not None else 0)
        return self.stages[start:]


def resolve_image_pipeline(
    *,
    raw_stages: object,
    model_path: str,
    output_scale: int | None = None,
    face_model_path: str | None = None,
    face_fidelity: float = 0.7,
    media: str = "image",
) -> ImagePipeline:
    """Resolve old requests and check stage paths before loading any model."""
    if output_scale == 1 and not model_path and _is_fix_only_stages(raw_stages):
        stages = _parse_fix_only_stages(raw_stages, media)
        return ImagePipeline(stages, primary_index=None)

    stages = parse_pipeline_stages(raw_stages, media=media)
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
        return ImagePipeline(
            pipeline.preprocess_stages + pipeline.postprocess_stages, primary_index=None
        )
    if Path(pipeline.primary.model_path) != Path(model_path):
        raise ValueError("The primary pipeline stage does not match the authenticated job model.")
    if pipeline.face is not None and (face_model_path or "") != pipeline.face.model_path:
        raise ValueError("The face pipeline stage does not match the authenticated face model.")
    return pipeline


def _is_fix_only_stages(raw: object) -> bool:
    if not isinstance(raw, list) or not raw:
        return False
    for item in raw:
        if not isinstance(item, dict):
            return False
        if item.get("kind") in FIX_STAGE_KINDS and item.get("role") == "fix":
            continue
        try:
            step = resolve_step(
                str(item.get("kind") or ""),
                fix=item.get("fix"),
                step_id=item.get("step"),
                steps=STEPS,
            )
        except ValueError:
            return False
        if not is_step_stage(step) or item.get("role") == "primary":
            return False
    return True


def _parse_fix_only_stages(raw: object, media: str) -> tuple[PipelineStage, ...]:
    if not isinstance(raw, list) or not raw:
        return ()
    _check_stage_count(raw)
    parsed = [_parse_stage_item(item, media) for item in raw]
    fix_count = sum(1 for _, step in parsed if step.is_fix_stage)
    if fix_count > MAX_FIX_STAGES:
        raise ValueError(f"Only {MAX_FIX_STAGES} restoration/deblock stages may run.")
    stages = [stage for stage, _ in parsed]
    _check_each_runs_once(stages, [step for _, step in parsed])
    return tuple(stages)


def _check_stage_count(raw: list[object]) -> None:
    # Model stages and the steps a job adds by id have separate bounds.
    if len(raw) > LIMITS.max_image_pipeline_stages + LIMITS.max_step_stages:
        raise ValueError(
            f"Image recipes support at most {LIMITS.max_image_pipeline_stages} bounded stages."
        )


def _check_each_runs_once(
    stages: list[PipelineStage] | tuple[PipelineStage, ...], steps: list[StepDefinition]
) -> None:
    fixes = [stage.fix for stage in stages if stage.fix is not None]
    if len(set(fixes)) != len(fixes):
        raise ValueError("Each fix kind may run only once.")
    added = [step.id for step in steps if is_step_stage(step)]
    if len(set(added)) != len(added):
        raise ValueError("Each added step may run only once.")


def _parse_stage_item(item: object, media: str) -> tuple[PipelineStage, StepDefinition]:
    if not isinstance(item, dict):
        raise ValueError("Every pipeline stage must be an object.")
    raw_step = item.get("step")
    kind = str(item.get("kind") or "")
    fix = item.get("fix")
    if fix is not None and (not isinstance(fix, str) or fix not in FIX_KINDS):
        raise ValueError(f"Only a restoration stage may name a fix: {_FIX_LIST_TEXT}.")
    step = resolve_step(kind, fix=fix, step_id=raw_step, steps=STEPS)
    if not step.in_image_pipeline:
        raise ValueError(f"Unsupported image pipeline stage: {kind or step.id}")
    if media not in step.media:
        raise ValueError(f"The {step.id} step does not run on {media} jobs.")
    if step.dev_only and not dev_steps_enabled():
        raise ValueError(
            f"The {step.id} step is a development step; this engine runs it only with "
            f"{DEV_STEPS_ENV}=1."
        )
    if not kind:
        kind = step.stage_kind or step.id
    model_id = str(item.get("model_id") or "")
    model_path = str(item.get("model_path") or "")
    if step.needs_model and (not model_id or not model_path):
        raise ValueError(f"The {kind} stage requires a model ID and model path.")
    fidelity = item.get("fidelity")
    parsed_fidelity = None if fidelity is None else float(fidelity)
    if (
        parsed_fidelity is not None
        and not _FACE_FIDELITY_MIN <= parsed_fidelity <= _FACE_FIDELITY_MAX
    ):
        raise ValueError("Face restoration fidelity must be between 0 and 1.")
    params = validate_step_params(step, item.get("params"))
    raw_mask = item.get("mask_path")
    mask_path: str | None = None
    if raw_mask is not None:
        if not isinstance(raw_mask, str) or not raw_mask.strip() or "\0" in raw_mask:
            raise ValueError(f"The {step.id} stage has an invalid mask path.")
        if not step.supports_mask:
            raise ValueError(f"The {step.id} stage does not support a mask.")
        require_ordinary_source(raw_mask)
        mask_path = raw_mask
    if step.requires_mask and mask_path is None:
        raise ValueError(f"The {step.id} stage needs a mask.")
    role = str(item.get("role") or "")
    allowed_roles = IMAGE_PIPELINE_ROLES | ({step.role} if step.role else set())
    if role not in allowed_roles:
        raise ValueError("Unknown pipeline stage role.")
    stage = PipelineStage(
        kind=kind,
        model_id=model_id,
        model_path=model_path,
        fidelity=parsed_fidelity,
        execution=str(item.get("execution") or "sequential"),
        role=role,
        fix=fix,
        step_id=step.id,
        params=params,
        mask_path=mask_path,
    )
    return stage, step


def parse_pipeline_stages(raw: object, *, media: str = "image") -> tuple[PipelineStage, ...]:
    if not isinstance(raw, list) or not raw:
        return ()
    _check_stage_count(raw)
    parsed = [_parse_stage_item(item, media) for item in raw]
    stages: list[PipelineStage] = [stage for stage, _ in parsed]
    steps_list: list[StepDefinition] = [step for _, step in parsed]
    # Fixes, the primary and Faces; a step stage with a model counts with the added steps.
    model_stages = [step.needs_model and not is_step_stage(step) for step in steps_list]
    if sum(model_stages) > LIMITS.max_image_pipeline_stages:
        raise ValueError(
            f"Image recipes support at most {LIMITS.max_image_pipeline_stages} bounded stages."
        )

    kinds = [stage.kind for stage in stages]
    primary_index = _primary_index(stages)
    fix_before = sum(model_stages[:primary_index])
    if fix_before > MAX_FIX_STAGES:
        raise ValueError(
            f"Only {MAX_FIX_STAGES} restoration/deblock stages may precede the primary stage."
        )
    for stage, step, model_stage in zip(
        stages[:primary_index],
        steps_list[:primary_index],
        model_stages[:primary_index],
        strict=True,
    ):
        if model_stage:
            if stage.kind not in FIX_STAGE_KINDS:
                raise ValueError("Only restoration or deblock may precede the primary stage.")
            if stage.role not in {"", "fix"}:
                raise ValueError("Only fix roles may precede the primary stage.")
        else:
            if _PHASE_INDEX[step.phase] >= _PRIMARY_PHASE:
                raise ValueError("Only restoration or deblock may precede the primary stage.")
            if stage.role == "primary":
                raise ValueError("Only fix roles may precede the primary stage.")
    _check_each_runs_once(stages, steps_list)
    if stages[primary_index].role not in {"", "primary"}:
        raise ValueError("The primary stage must have the primary role.")
    trailing_start = primary_index + 1
    if trailing_start < len(stages) and kinds[trailing_start] == FACE_STAGE_KIND:
        face_stage = stages[trailing_start]
        if kinds[primary_index] != "upscale" or face_stage.execution != "fused-with-upscale":
            raise ValueError(
                "The current face checkpoint must be fused with its paired upscale stage."
            )
        if face_stage.role not in {"", "face"}:
            raise ValueError("The trailing stage must have the face role.")
        trailing_start += 1
    for stage, step in zip(stages[trailing_start:], steps_list[trailing_start:], strict=True):
        if (
            _PHASE_INDEX[step.phase] <= _FACE_PHASE
            or not is_step_stage(step)
            or stage.role == "primary"
        ):
            raise ValueError("Face restoration is the only optional stage after the primary stage.")
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
