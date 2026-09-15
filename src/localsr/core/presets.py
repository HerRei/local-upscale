from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass
from enum import StrEnum

from .estimator import ResourceEstimate, estimate_resources
from .model_catalog import (
    MODEL_CATALOG,
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


@dataclass(frozen=True)
class PresetSettings:
    model: CatalogModel
    device_id: str
    device_type: str
    tile_size: int
    halo: int
    precision: str
    safe_memory: bool
    requires_download: bool
    estimate: ResourceEstimate
    rationale: tuple[str, ...]


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


def get_preset_model(
    preset: Preset | PresetMode | str,
    purpose: ModelPurpose | str = ModelPurpose.PHOTO,
    catalog: Sequence[CatalogModel] | None = None,
) -> CatalogModel:
    """Return the top-ranked model for a preset and purpose."""
    models = catalog if catalog is not None else MODEL_CATALOG
    return select_model_for_preset(models, preset, purpose=purpose)


def _device_priority(device: Mapping) -> tuple[int, int]:
    backend = str(device.get("type", "cpu"))
    order = {"cuda": 5, "rocm": 5, "mps": 4, "xpu": 3, "directml": 2, "cpu": 1}
    return order.get(backend, 0), int(device.get("free_memory", 0))


def resolve_settings_for_model(
    *,
    model: CatalogModel,
    mode: Preset | PresetMode | str,
    devices: Sequence[Mapping],
    image_width: int,
    image_height: int,
    output_scale: int,
    available_system_memory: int | None,
    available_disk: int | None,
    model_half_supported: bool,
    parameter_count: int = 0,
    model_file_size: int = 0,
    installed_model_ids: Set[str] = frozenset(),
) -> PresetSettings:
    if image_width < 1 or image_height < 1:
        raise ValueError("An image must be selected before resolving a preset.")
    if output_scale < 1 or output_scale > model.native_scale:
        raise ValueError("The requested output scale is incompatible with the selected model.")
    if not devices:
        raise ValueError("No compute device is available.")

    best_blocked = None
    ordered_devices = sorted(devices, key=_device_priority, reverse=True)
    category = _mode_category(mode)

    for device in ordered_devices:
        device_type = str(device.get("type", "cpu"))
        recommended = sorted(
            {max(4, int(size)) for size in device.get("recommended_tile_sizes", [64])},
            reverse=True,
        )
        low_memory = 0 < int(device.get("free_memory", 0)) < 3 * 1024**3
        safe_memory = low_memory
        if safe_memory and len(recommended) > 1:
            recommended = recommended[1:] + recommended[:1]

        precision_order = ["fp32"]
        if (
            category == "fast"
            and model_half_supported
            and bool(device.get("supports_fp16", False))
            and device_type != "cpu"
        ):
            precision_order = ["fp16", "fp32"]

        for precision in precision_order:
            for tile_size in recommended:
                halo = min(model.recommended_halo, max(1, tile_size // 2))
                estimate = estimate_resources(
                    image_width=image_width,
                    image_height=image_height,
                    scale=output_scale,
                    model_scale=model.native_scale,
                    tile_size=tile_size,
                    halo=halo,
                    precision=precision,
                    device_type=device_type,
                    available_device_memory=int(device.get("free_memory", 0)) or None,
                    available_system_memory=available_system_memory,
                    available_disk=available_disk,
                    model_file_size=model_file_size or model.size_bytes,
                    parameter_count=parameter_count,
                    memory_factor=model.memory_factor,
                    time_factor=model.time_factor,
                    device_memory_shared=bool(device.get("is_integrated", False)),
                )
                mode_label = str(mode.value if isinstance(mode, (Preset, PresetMode)) else mode)
                decision = PresetSettings(
                    model=model,
                    device_id=str(device.get("id", "cpu")),
                    device_type=device_type,
                    tile_size=tile_size,
                    halo=halo,
                    precision=precision,
                    safe_memory=safe_memory,
                    requires_download=model.model_id not in installed_model_ids,
                    estimate=estimate,
                    rationale=(
                        f"{device.get('name', device.get('id', 'device'))} selected automatically.",
                        f"{tile_size}px tiles fit the reported device limits.",
                        f"{precision.upper()} selected for {mode_label} mode.",
                    ),
                )
                if not estimate.blocking:
                    return decision
                if best_blocked is None:
                    best_blocked = decision

    if best_blocked is not None:
        return best_blocked
    raise ValueError("LocalSR could not derive settings for the available hardware.")
