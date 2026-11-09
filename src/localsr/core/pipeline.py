"""Validation and progress math for bounded multi-stage image recipes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PipelineStage:
    kind: str
    model_id: str
    model_path: str
    fidelity: float | None = None
    execution: str = "sequential"
    role: str = ""
    fix: str | None = None


FIX_KINDS = frozenset({"blur", "jpeg", "noise"})
# Up to one fix per kind runs before the main model.
MAX_FIX_STAGES = len(FIX_KINDS)


class PipelineStageError(RuntimeError):
    def __init__(self, stage: PipelineStage, message: str):
        self.stage = stage
        super().__init__(f"{stage.kind} stage ({stage.model_id}) failed: {message}")


@dataclass(frozen=True)
class ImagePipeline:
    """Validated execution order shared by legacy and staged image requests."""

    stages: tuple[PipelineStage, ...]
    primary_index: int

    @property
    def primary(self) -> PipelineStage:
        return self.stages[self.primary_index]

    @property
    def preprocess_stages(self) -> tuple[PipelineStage, ...]:
        return self.stages[: self.primary_index]

    @property
    def face(self) -> PipelineStage | None:
        return self.stages[-1] if self.stages[-1].kind == "face_restore" else None


def resolve_image_pipeline(
    *,
    raw_stages: object,
    model_path: str,
    output_scale: int | None = None,
    face_model_path: str | None = None,
    face_fidelity: float = 0.7,
) -> ImagePipeline:
    """Resolve old requests and check stage paths before loading any model."""
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
    if Path(pipeline.primary.model_path) != Path(model_path):
        raise ValueError("The primary pipeline stage does not match the authenticated job model.")
    if pipeline.face is not None and (face_model_path or "") != pipeline.face.model_path:
        raise ValueError("The face pipeline stage does not match the authenticated face model.")
    return pipeline


def parse_pipeline_stages(raw: object) -> tuple[PipelineStage, ...]:
    if not isinstance(raw, list) or not raw:
        return ()
    if len(raw) > 5:
        raise ValueError("Image recipes support at most five bounded stages.")
    stages: list[PipelineStage] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("Every pipeline stage must be an object.")
        kind = str(item.get("kind") or "")
        if kind not in {"deblock", "restore", "upscale", "face_restore"}:
            raise ValueError(f"Unsupported image pipeline stage: {kind or '<empty>'}")
        model_id = str(item.get("model_id") or "")
        model_path = str(item.get("model_path") or "")
        if not model_id or not model_path:
            raise ValueError(f"The {kind} stage requires a model ID and model path.")
        fidelity = item.get("fidelity")
        parsed_fidelity = None if fidelity is None else float(fidelity)
        if parsed_fidelity is not None and not 0.0 <= parsed_fidelity <= 1.0:
            raise ValueError("Face restoration fidelity must be between 0 and 1.")
        fix = item.get("fix")
        if fix is not None and (
            not isinstance(fix, str) or fix not in FIX_KINDS or kind not in {"deblock", "restore"}
        ):
            raise ValueError("Only a restoration stage may name a fix: blur, jpeg or noise.")
        stages.append(
            PipelineStage(
                kind=kind,
                model_id=model_id,
                model_path=model_path,
                fidelity=parsed_fidelity,
                execution=str(item.get("execution") or "sequential"),
                role=str(item.get("role") or ""),
                fix=fix,
            )
        )

    kinds = [stage.kind for stage in stages]
    if any(stage.role not in {"", "fix", "primary", "face"} for stage in stages):
        raise ValueError("Unknown pipeline stage role.")
    primary_index = _primary_index(stages)
    if primary_index > MAX_FIX_STAGES:
        raise ValueError("Only three restoration/deblock stages may precede the primary stage.")
    if any(kind not in {"deblock", "restore"} for kind in kinds[:primary_index]):
        raise ValueError("Only restoration or deblock may precede the primary stage.")
    fixes = [stage.fix for stage in stages if stage.fix is not None]
    if len(set(fixes)) != len(fixes):
        raise ValueError("Each fix kind may run only once.")
    if any(stage.role not in {"", "fix"} for stage in stages[:primary_index]):
        raise ValueError("Only fix roles may precede the primary stage.")
    if stages[primary_index].role not in {"", "primary"}:
        raise ValueError("The primary stage must have the primary role.")
    trailing = kinds[primary_index + 1 :]
    if trailing not in ([], ["face_restore"]):
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

    Requests from hosts before 0.3.7 name no roles; their restore-only job has one
    restore stage, which is the primary.
    """
    explicit = [i for i, stage in enumerate(stages) if stage.role == "primary"]
    upscales = [i for i, stage in enumerate(stages) if stage.kind == "upscale"]
    if len(upscales) > 1:
        raise ValueError("An image recipe may contain only one upscale stage.")
    if explicit:
        if len(explicit) != 1 or (upscales and explicit != upscales):
            raise ValueError("An image recipe must contain exactly one primary stage.")
        index = explicit[0]
        if stages[index].kind not in {"restore", "deblock", "upscale"}:
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
