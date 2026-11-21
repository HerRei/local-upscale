from collections.abc import Sequence, Set
from enum import StrEnum

from .model_catalog import (
    CatalogModel,
    ModelPurpose,
)


class Preset(StrEnum):
    FAST = "fast"
    BALANCED = "balanced"
    QUALITY = "quality"
    ULTRA = "ultra"


class PresetMode(StrEnum):
    QUICK_UPSCALE = "quick_upscale"
    BEST_UPSCALE = "best_upscale"
    QUICK_DENOISE = "quick_denoise"
    BEST_DENOISE = "best_denoise"
    COMBO = "combo"


class NoCompatibleModelError(ValueError):
    pass


def _purpose_match(model: CatalogModel, purpose: ModelPurpose | str) -> int:
    target = str(purpose).lower()
    model_purposes = [str(p).lower() for p in model.purposes]

    if target in model_purposes:
        return 2
    if target in ("illustration", "anime") and any(
        p in ("illustration", "anime") for p in model_purposes
    ):
        return 2
    if target in ("deblur", "restoration") and any(
        p in ("deblur", "restoration") for p in model_purposes
    ):
        return 2
    if "general" in model_purposes:
        return 1
    return 0


def _rights_allow_preset(model: CatalogModel, purpose: ModelPurpose | str) -> bool:
    """Quick/Best never pick a checkpoint whose rights are unresolved or non-commercial.

    Labs status is a separate axis (validation), so a Labs model with verified
    rights stays eligible. Face companions are chosen by pairing with the primary
    model and imported by the user, so the FACE purpose is exempt.
    """
    if str(purpose).lower() == ModelPurpose.FACE.value:
        return True
    return model.commercial_use_status == "allowed"


def _mode_category(mode: Preset | PresetMode | str) -> str:
    val = str(mode.value if isinstance(mode, (Preset, PresetMode)) else mode).lower()
    if val in (Preset.FAST.value, PresetMode.QUICK_UPSCALE.value, PresetMode.QUICK_DENOISE.value):
        return "fast"
    if val in (Preset.BALANCED.value,):
        return "balanced"
    if val in (Preset.QUALITY.value,):
        return "quality"
    if val in (
        Preset.ULTRA.value,
        PresetMode.BEST_UPSCALE.value,
        PresetMode.BEST_DENOISE.value,
        PresetMode.COMBO.value,
    ):
        return "ultra"
    return "fast"


def _balanced_score(model: CatalogModel) -> float:
    quality_val = float(int(model.quality_tier))
    speed_val = float(int(model.speed_tier))
    speed_factor = getattr(model, "speed_factor", 0.5)
    mem_factor = float(model.memory_factor)
    return (quality_val * 2.0) + (speed_val * 1.5) + (speed_factor * 1.0) - (mem_factor * 0.8)


def rank_models_for_preset(
    models: Sequence[CatalogModel],
    mode: Preset | PresetMode | str,
    *,
    purpose: ModelPurpose | str = ModelPurpose.PHOTO,
    output_scale: int | None = None,
    installed_model_ids: Set[str] = frozenset(),
) -> tuple[CatalogModel, ...]:
    if output_scale is not None and output_scale < 1:
        raise ValueError("Output scale must be at least 1.")

    target_scale = output_scale
    if target_scale is None:
        p_lower = str(purpose).lower()
        target_scale = 1 if p_lower in ("deblur", "denoise", "restoration") else 4

    eligible = [
        model
        for model in models
        if model.native_scale >= target_scale
        and _purpose_match(model, purpose) > 0
        and _rights_allow_preset(model, purpose)
        and model.preset_eligible
    ]

    category = _mode_category(mode)
    if category == "fast":
        eligible.sort(
            key=lambda model: (
                -_purpose_match(model, purpose),
                -int(model.speed_tier),
                -getattr(model, "speed_factor", 0.0),
                model.memory_factor,
                model.size_bytes,
                -(model.model_id in installed_model_ids),
                -int(model.quality_tier),
                model.model_id,
            )
        )
    elif category == "balanced":
        eligible.sort(
            key=lambda model: (
                -_purpose_match(model, purpose),
                -_balanced_score(model),
                -(model.model_id in installed_model_ids),
                model.model_id,
            )
        )
    elif category == "quality":
        eligible.sort(
            key=lambda model: (
                -_purpose_match(model, purpose),
                -int(model.quality_tier),
                -getattr(model, "speed_factor", 0.0),
                -int(model.speed_tier),
                -(model.model_id in installed_model_ids),
                model.model_id,
            )
        )
    else:  # ultra / best
        eligible.sort(
            key=lambda model: (
                -_purpose_match(model, purpose),
                -int(model.quality_tier),
                -model.size_bytes,
                -int(model.speed_tier),
                -(model.model_id in installed_model_ids),
                model.model_id,
            )
        )
    return tuple(eligible)


def select_model_for_preset(
    models: Sequence[CatalogModel],
    mode: Preset | PresetMode | str,
    **kwargs,
) -> CatalogModel:
    ranked = rank_models_for_preset(models, mode, **kwargs)
    if not ranked:
        raise NoCompatibleModelError("No compatible model is available for this preset.")
    return ranked[0]
