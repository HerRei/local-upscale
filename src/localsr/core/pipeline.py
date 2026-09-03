"""Validation and progress math for bounded multi-stage image recipes."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PipelineStage:
    kind: str
    model_id: str
    model_path: str
    fidelity: float | None = None
    execution: str = "sequential"


class PipelineStageError(RuntimeError):
    def __init__(self, stage: PipelineStage, message: str):
        self.stage = stage
        super().__init__(f"{stage.kind} stage ({stage.model_id}) failed: {message}")


def parse_pipeline_stages(raw: object) -> tuple[PipelineStage, ...]:
    if not isinstance(raw, list) or not raw:
        return ()
    if len(raw) > 3:
        raise ValueError("Image recipes support at most three bounded stages.")
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
        stages.append(
            PipelineStage(
                kind=kind,
                model_id=model_id,
                model_path=model_path,
                fidelity=parsed_fidelity,
                execution=str(item.get("execution") or "sequential"),
            )
        )

    kinds = [stage.kind for stage in stages]
    upscale_indexes = [index for index, kind in enumerate(kinds) if kind == "upscale"]
    if len(upscale_indexes) > 1:
        raise ValueError("An image recipe may contain only one upscale stage.")
    if upscale_indexes:
        primary_index = upscale_indexes[0]
    else:
        restore_indexes = [index for index, kind in enumerate(kinds) if kind == "restore"]
        if len(restore_indexes) != 1:
            raise ValueError("A restore-only recipe must contain exactly one restore stage.")
        primary_index = restore_indexes[0]
    if primary_index > 1:
        raise ValueError("Only one restoration/deblock stage may precede the primary stage.")
    if primary_index == 1 and kinds[0] not in {"deblock", "restore"}:
        raise ValueError("Only restoration or deblock may precede upscaling.")
    if kinds[primary_index] == "restore" and primary_index != 0:
        raise ValueError("A restore-only recipe cannot contain another restoration stage.")
    trailing = kinds[primary_index + 1 :]
    if trailing not in ([], ["face_restore"]):
        raise ValueError("Face restoration is the only optional stage after upscaling.")
    if trailing and stages[-1].execution != "fused-with-upscale":
        raise ValueError("The current face checkpoint must be fused with its paired upscale stage.")
    return tuple(stages)


def stage_percentage(stage_index: int, stage_count: int, completed: int, total: int) -> float:
    if stage_count < 1 or not 0 <= stage_index < stage_count:
        raise ValueError("Invalid pipeline stage position.")
    fraction = max(0.0, min(1.0, completed / max(1, total)))
    return (stage_index + fraction) / stage_count * 100.0
