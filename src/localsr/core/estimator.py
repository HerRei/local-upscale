import math
from dataclasses import dataclass


@dataclass(frozen=True)
class ResourceEstimate:
    seconds: float
    seconds_low: float
    seconds_high: float
    calibrated: bool
    device_memory_bytes: int
    system_memory_bytes: int
    output_bytes: int
    working_disk_bytes: int
    tile_count: int
    effective_megapixels: float
    device_memory_remaining: int | None
    system_memory_remaining: int | None
    disk_remaining: int | None
    warnings: tuple[str, ...]
    blocking: bool


def format_bytes(value: float | None) -> str:
    if value is None or value <= 0:
        return "unknown"
    amount = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024 or unit == "TB":
            return f"{amount:.0f} {unit}" if unit in {"B", "KB"} else f"{amount:.1f} {unit}"
        amount /= 1024
    return "unknown"


def estimate_resources(
    *,
    image_width: int,
    image_height: int,
    scale: int,
    tile_size: int,
    halo: int,
    precision: str,
    device_type: str,
    available_device_memory: int | None,
    available_system_memory: int | None,
    available_disk: int | None,
    model_file_size: int,
    parameter_count: int = 0,
    memory_factor: float = 1.0,
    time_factor: float = 1.0,
    measured_seconds_per_megapixel: float | None = None,
    device_memory_shared: bool = False,
    model_scale: int | None = None,
) -> ResourceEstimate:
    width = max(1, image_width)
    height = max(1, image_height)
    scale = max(1, scale)
    native_scale = max(scale, model_scale or scale)
    tile_size = max(4, tile_size)
    halo = max(0, halo)
    precision_bytes = 2 if precision == "fp16" else 4

    output_bytes = width * height * scale * scale * 3
    native_output_bytes = width * height * native_scale * native_scale * 3
    input_float_bytes = width * height * 3 * 4
    parameter_bytes = max(model_file_size, parameter_count * precision_bytes)
    padded_tile = tile_size + 2 * halo

    # Transformer activations generally dominate model-weight storage. This
    # estimate is intentionally conservative and is used for warnings, not as
    # a promise that an allocation will fit.
    activation_bytes_per_pixel = 8_192 * memory_factor * (precision_bytes / 4)
    activation_bytes = int(padded_tile * padded_tile * activation_bytes_per_pixel)
    device_overhead = 320 * 1024 * 1024
    device_memory = int(parameter_bytes * 1.25 + activation_bytes + device_overhead)

    base_system_memory = int(
        input_float_bytes
        + output_bytes
        + min(native_output_bytes, 512 * 1024 * 1024)
        + model_file_size * 1.2
        + 256 * 1024 * 1024
    )
    # CPU and MPS allocations consume system/unified memory. CUDA VRAM is a
    # separate pool and therefore must not be counted twice against RAM.
    system_memory = (
        base_system_memory + device_memory
        if device_type in {"cpu", "mps"} or device_memory_shared
        else base_system_memory
    )
    working_disk = int(native_output_bytes + output_bytes * 1.1 + 64 * 1024 * 1024)
    tile_count = math.ceil(width / tile_size) * math.ceil(height / tile_size)
    effective_megapixels = tile_count * padded_tile * padded_tile / 1_000_000

    if measured_seconds_per_megapixel and measured_seconds_per_megapixel > 0:
        seconds = effective_megapixels * measured_seconds_per_megapixel
        seconds_low = seconds * 0.75
        seconds_high = seconds * 1.35
        calibrated = True
    else:
        # Hardware generation, thermals, model architecture, and I/O make an
        # exact first-run ETA impossible. Show a broad range until LocalSR has
        # measured this model/device combination on the user's machine.
        baseline = {
            "cuda": 18.0,
            "rocm": 22.0,
            "xpu": 28.0,
            "mps": 38.0,
            "cpu": 180.0,
        }.get(device_type, 75.0)
        fixed_overhead = {
            "cuda": 4.0,
            "rocm": 5.0,
            "xpu": 6.0,
            "mps": 8.0,
            "cpu": 12.0,
        }.get(device_type, 8.0)
        seconds = fixed_overhead + effective_megapixels * baseline * time_factor
        seconds_low = seconds * 0.45
        seconds_high = seconds * 2.2
        calibrated = False

    warnings = []
    blocking = False
    if (
        device_type in {"cuda", "rocm", "xpu"}
        and available_device_memory
        and device_memory > available_device_memory * 0.9
    ):
        warnings.append(
            f"Estimated GPU-memory use ({format_bytes(device_memory)}) exceeds the hard safe "
            f"limit for currently free GPU memory ({format_bytes(available_device_memory)}). "
            "Choose a smaller tile or CPU."
        )
        blocking = True
    elif (
        device_type in {"cuda", "rocm", "xpu"}
        and available_device_memory
        and device_memory > available_device_memory * 0.7
    ):
        warnings.append("GPU memory will be tight; Safe Memory Mode is recommended.")

    if device_type == "mps" and available_device_memory:
        unified_available = min(
            available_device_memory,
            available_system_memory or available_device_memory,
        )
        if system_memory > unified_available * 0.9:
            warnings.append(
                f"Estimated LocalSR peak ({format_bytes(system_memory)}) exceeds currently free "
                f"unified memory ({format_bytes(unified_available)}). The run is still allowed: "
                "macOS can compress or swap inactive memory, but it may become much slower."
            )
        elif system_memory > unified_available * 0.7:
            warnings.append(
                "Unified memory may become tight. The run is allowed; Safe Memory Mode can "
                "reduce pressure at the cost of speed."
            )

    if (
        device_type != "mps"
        and available_system_memory
        and system_memory > available_system_memory * 0.9
    ):
        warnings.append(
            f"Estimated RAM use ({format_bytes(system_memory)}) is too close to available RAM "
            f"({format_bytes(available_system_memory)})."
        )
        blocking = True
    elif (
        device_type != "mps"
        and available_system_memory
        and system_memory > available_system_memory * 0.7
    ):
        warnings.append("Available RAM is tight; close other applications first.")

    if available_disk and working_disk > available_disk * 0.9:
        warnings.append(
            f"Not enough working disk space: about {format_bytes(working_disk)} is required."
        )
        blocking = True

    return ResourceEstimate(
        seconds=max(1.0, seconds),
        seconds_low=max(1.0, seconds_low),
        seconds_high=max(1.0, seconds_high),
        calibrated=calibrated,
        device_memory_bytes=device_memory,
        system_memory_bytes=system_memory,
        output_bytes=output_bytes,
        working_disk_bytes=working_disk,
        tile_count=tile_count,
        effective_megapixels=effective_megapixels,
        device_memory_remaining=(
            max(0, available_device_memory - device_memory)
            if available_device_memory is not None
            else None
        ),
        system_memory_remaining=(
            max(0, available_system_memory - system_memory)
            if available_system_memory is not None
            else None
        ),
        disk_remaining=(
            max(0, available_disk - working_disk) if available_disk is not None else None
        ),
        warnings=tuple(warnings),
        blocking=blocking,
    )
