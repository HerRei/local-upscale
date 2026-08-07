from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass
from enum import StrEnum

from .estimator import ResourceEstimate, estimate_resources
from .model_catalog import CatalogModel, ModelPurpose


class PresetMode(StrEnum):
    QUICK = "quick"
    BEST = "best"
    DENOISE = "denoise"


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


def _purpose_match(model: CatalogModel, purpose: ModelPurpose) -> int:
    if purpose in model.purposes:
        return 2
    if ModelPurpose.GENERAL in model.purposes:
        return 1
    return 0


def rank_models_for_preset(
    models: Sequence[CatalogModel],
    mode: PresetMode,
    *,
    purpose: ModelPurpose = ModelPurpose.PHOTO,
    output_scale: int = 4,
    installed_model_ids: Set[str] = frozenset(),
) -> tuple[CatalogModel, ...]:
    if output_scale < 1:
        raise ValueError("Output scale must be at least 1.")

    eligible = [
        model
        for model in models
        if model.native_scale >= output_scale and _purpose_match(model, purpose)
    ]
    if mode == PresetMode.QUICK:
        eligible.sort(
            key=lambda model: (
                -int(model.speed_tier),
                model.memory_factor,
                -(model.model_id in installed_model_ids),
                -int(model.quality_tier),
                model.model_id,
            )
        )
    else:
        eligible.sort(
            key=lambda model: (
                -int(model.quality_tier),
                -_purpose_match(model, purpose),
                -int(model.speed_tier),
                -(model.model_id in installed_model_ids),
                model.model_id,
            )
        )
    return tuple(eligible)


def select_model_for_preset(
    models: Sequence[CatalogModel],
    mode: PresetMode,
    **kwargs,
) -> CatalogModel:
    ranked = rank_models_for_preset(models, mode, **kwargs)
    if not ranked:
        raise NoCompatibleModelError("No compatible model is available for this preset.")
    return ranked[0]


def _device_priority(device: Mapping) -> tuple[int, int]:
    backend = str(device.get("type", "cpu"))
    order = {"cuda": 5, "rocm": 5, "mps": 4, "xpu": 3, "cpu": 1}
    return order.get(backend, 0), int(device.get("free_memory", 0))


def resolve_settings_for_model(
    *,
    model: CatalogModel,
    mode: PresetMode,
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
            mode == PresetMode.QUICK
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
                        f"{precision.upper()} selected for {mode.value} mode.",
                    ),
                )
                if not estimate.blocking:
                    return decision
                if best_blocked is None:
                    best_blocked = decision

    if best_blocked is not None:
        return best_blocked
    raise ValueError("LocalSR could not derive settings for the available hardware.")
