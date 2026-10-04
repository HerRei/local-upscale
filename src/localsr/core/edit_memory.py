"""Conservative admission and live pressure checks for native, quantized editing.

Profiles are estimates, not hardware acceptance results. No allocation probing,
swapping to discover capacity, or increase of the macOS working-set limit.
"""

from __future__ import annotations

from dataclasses import dataclass

GIB = 1024**3


@dataclass(frozen=True)
class EditMemory:
    total_ram: int
    available_ram: int
    swap_used: int = 0
    gpu_total: int = 0
    gpu_free: int = 0
    unified: bool = False
    pressure: str = "unknown"


@dataclass(frozen=True)
class EditPlan:
    required_ram: int
    reserve_ram: int
    gpu_budget: int
    max_dimension: int
    process_limit: int


def reserve_for(total_ram: int) -> int:
    """Memory left to the OS and other apps: 4 GiB, or a tenth of RAM on larger machines."""
    return max(4 * GIB, int(total_ram * 0.10))


def activation_memory(unified: bool, limit: int) -> int:
    """Working buffers for one edit at the given longest edge."""
    return int((1.5 if unified else 1.0) * GIB * (limit / 512) ** 2)


def mapped_size(model: dict) -> int:
    return sum(f["size_bytes"] for f in model["files"])


def estimate_edit_memory(model: dict, unified: bool, limit: int) -> int:
    """Resident memory at the bundle's heaviest phase, in bytes.

    Weights are memory-mapped and touched lazily: the encoders are resident while
    the prompt and reference are encoded, the transformer while the image is
    sampled, and the phase that is over can be evicted. On unified memory the
    transformer lives in the same pool; beside a dedicated GPU the card holds it
    and the host holds the encoders.
    """
    sizes = {f["role"]: f["size_bytes"] for f in model["files"]}
    activations = activation_memory(unified, limit)
    conditioning = sizes.get("text_encoder", 0) + sizes.get("vision", 0)
    sampling = sizes.get("vae", 0) + activations + (sizes["diffusion"] if unified else 0)
    return int(max(conditioning, sampling) * 1.1)


def read_host_memory(unified: bool = False) -> EditMemory:
    import psutil

    ram = psutil.virtual_memory()
    swap = psutil.swap_memory()
    pressure = "unknown"
    if unified:
        import subprocess

        try:
            result = subprocess.run(
                ["sysctl", "-n", "kern.memorystatus_vm_pressure_level"],
                capture_output=True,
                text=True,
                timeout=1,
                check=True,
            )
            pressure = {"1": "low", "2": "warning", "4": "critical"}.get(
                result.stdout.strip(), "unknown"
            )
        except (OSError, subprocess.SubprocessError):
            pass
    return EditMemory(
        int(ram.total), int(ram.available), int(swap.used), unified=unified, pressure=pressure
    )


def read_edit_memory(device: str) -> EditMemory:
    from dataclasses import replace

    memory = read_host_memory(device == "mps")
    if device == "mps":
        return memory
    if device.startswith("cuda"):
        import torch

        free, total = torch.cuda.mem_get_info(device)
        return replace(memory, gpu_total=int(total), gpu_free=int(free))
    raise ValueError("Qwen editing requires Apple Silicon Metal or a CUDA/ROCm GPU.")


def plan_edit(model: dict, memory: EditMemory, max_dimension: int = 512) -> EditPlan:
    if memory.pressure in {"warning", "critical", "high"}:
        raise MemoryError("Memory pressure is high. Close other apps before editing.")
    reserve = reserve_for(memory.total_ram)
    limit = min(1024, max_dimension)
    if memory.unified:
        if memory.total_ram < model["min_unified_memory_gb"] * GIB:
            raise MemoryError("Choose a smaller Qwen quantization for this Mac.")
        if memory.total_ram < 36 * GIB:
            limit = min(limit, 512)
        elif memory.total_ram < 48 * GIB:
            limit = min(limit, 768)
    else:
        # Drivers may report a little less than the card's nominal capacity.
        capacity = memory.gpu_total + 256 * 1024**2
        if capacity < model["min_vram_gb"] * GIB:
            raise MemoryError("Choose a smaller Qwen quantization for this GPU.")
        if capacity < 12 * GIB:
            limit = min(limit, 512)
        elif capacity < 16 * GIB:
            limit = min(limit, 768)
    # A starting estimate of the heaviest phase; the supervisor watches the real
    # numbers while the model runs. Its process limit allows every mapped file
    # to stay resident, which is normal when memory is plentiful.
    required = estimate_edit_memory(model, memory.unified, limit)
    process_limit = int(mapped_size(model) * 1.1 + activation_memory(memory.unified, limit))
    if memory.available_ram - reserve < required:
        raise MemoryError(
            f"Editing needs about {required / GIB:.1f} GiB available plus "
            f"a {reserve / GIB:.1f} GiB reserve for other apps; "
            f"only {memory.available_ram / GIB:.1f} GiB is currently available. "
            "Close apps, choose Q2 or a smaller edit size, and retry."
        )
    if memory.unified:
        # Metal may wire well over half of unified memory; stay under Apple's
        # recommended working set with room for the encoders and the OS.
        budget = min(required, int(memory.total_ram * 0.60), memory.available_ram - reserve)
    else:
        gpu_reserve = max(2 * GIB, int(memory.gpu_total * 0.15))
        budget = min(int(memory.gpu_total * 0.80), memory.gpu_free - gpu_reserve)
        if budget < 4 * GIB:
            raise MemoryError("Too little VRAM is free. Close GPU apps before editing.")
    return EditPlan(required, reserve, budget, limit, process_limit)


def check_edit_pressure(memory: EditMemory, plan: EditPlan, initial_swap: int, rss: int):
    if (
        memory.available_ram < plan.reserve_ram
        or memory.swap_used > initial_swap + 512 * 1024**2
        or memory.pressure in {"warning", "critical", "high"}
        or rss > plan.process_limit
    ):
        raise MemoryError(
            "Editing stopped to protect system memory. Close apps or use a smaller model."
        )
