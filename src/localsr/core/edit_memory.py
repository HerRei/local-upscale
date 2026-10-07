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
    # macOS's own free level (kern.memorystatus_level) as bytes: what the system can
    # hand out without pressure, counting reclaimable file cache and compression.
    obtainable_ram: int = 0

    @property
    def free_ram(self) -> int:
        """Memory an edit can take now: the macOS free level when known, else available RAM.

        psutil's figure on macOS is free plus inactive pages only. On a busy 16 GB
        Mac it reads about 5.5 GiB while the kernel reports 70% free, and a 4.5 GiB
        FLUX.2 klein edit then runs without pressure or swap.
        """
        return self.obtainable_ram or self.available_ram


@dataclass(frozen=True)
class EditPlan:
    required_ram: int
    reserve_ram: int
    gpu_budget: int
    max_dimension: int
    process_limit: int


def reserve_for(total_ram: int, unified: bool = False) -> int:
    """Memory left to the OS and other apps, or a tenth of RAM on larger machines.

    On unified memory the free level already excludes what macOS keeps for itself,
    so 2 GiB is the cushion; beside a dedicated GPU the host keeps 4 GiB.
    """
    return max((2 if unified else 4) * GIB, int(total_ram * 0.10))


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


def _sysctl_int(name: str) -> int | None:
    """An integer sysctl on macOS, read in-process (no subprocess, no timeout)."""
    import ctypes

    try:
        libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    except OSError:
        return None
    value = ctypes.c_int64(0)
    size = ctypes.c_size_t(ctypes.sizeof(value))
    if libc.sysctlbyname(name.encode(), ctypes.byref(value), ctypes.byref(size), None, 0):
        return None
    return value.value


def read_host_memory(unified: bool = False) -> EditMemory:
    import psutil

    ram = psutil.virtual_memory()
    swap = psutil.swap_memory()
    pressure = "unknown"
    obtainable = 0
    if unified:
        level = _sysctl_int("kern.memorystatus_vm_pressure_level")
        free_percent = _sysctl_int("kern.memorystatus_level")
        pressure = {1: "low", 2: "warning", 4: "critical"}.get(level, "unknown")
        if free_percent is not None:
            obtainable = int(ram.total) * min(100, max(0, free_percent)) // 100
    return EditMemory(
        int(ram.total),
        int(ram.available),
        int(swap.used),
        unified=unified,
        pressure=pressure,
        obtainable_ram=obtainable,
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
    raise ValueError("Editing requires Apple Silicon Metal or a CUDA/ROCm GPU.")


def plan_edit(model: dict, memory: EditMemory, max_dimension: int = 512) -> EditPlan:
    if memory.pressure in {"critical", "high"} or (
        not memory.unified and memory.pressure == "warning"
    ):
        raise MemoryError("Memory pressure is high. Close other apps before editing.")
    reserve = reserve_for(memory.total_ram, memory.unified)
    limit = min(1024, max_dimension)
    if memory.unified:
        if memory.total_ram < model["min_unified_memory_gb"] * GIB:
            raise MemoryError("Choose a smaller editing model for this Mac.")
        if memory.total_ram < 36 * GIB:
            limit = min(limit, 512)
        elif memory.total_ram < 48 * GIB:
            limit = min(limit, 768)
    else:
        # Drivers may report a little less than the card's nominal capacity.
        capacity = memory.gpu_total + 256 * 1024**2
        if capacity < model["min_vram_gb"] * GIB:
            raise MemoryError("Choose a smaller editing model for this GPU.")
        if capacity < 12 * GIB:
            limit = min(limit, 512)
        elif capacity < 16 * GIB:
            limit = min(limit, 768)
    # A starting estimate of the heaviest phase; the supervisor watches the real
    # numbers while the model runs. Its process limit allows every mapped file
    # to stay resident, which is normal when memory is plentiful.
    required = estimate_edit_memory(model, memory.unified, limit)
    process_limit = int(mapped_size(model) * 1.1 + activation_memory(memory.unified, limit))
    if memory.free_ram - reserve < required:
        raise MemoryError(
            f"Editing needs about {required / GIB:.1f} GiB available plus "
            f"a {reserve / GIB:.1f} GiB reserve for other apps; "
            f"only {memory.free_ram / GIB:.1f} GiB is currently available. "
            "Close apps or choose a smaller model or edit size, and retry."
        )
    if memory.unified:
        # Metal may wire well over half of unified memory; stay under Apple's
        # recommended working set with room for the encoders and the OS.
        budget = min(required, int(memory.total_ram * 0.60), memory.free_ram - reserve)
    else:
        gpu_reserve = max(2 * GIB, int(memory.gpu_total * 0.15))
        budget = min(int(memory.gpu_total * 0.80), memory.gpu_free - gpu_reserve)
        if budget < 4 * GIB:
            raise MemoryError("Too little VRAM is free. Close GPU apps before editing.")
    return EditPlan(required, reserve, budget, limit, process_limit)


# On unified memory macOS compresses and swaps to make room, and "warning"
# pressure is routine while a model loads (measured: a FLUX.2 klein edit raised it
# at 34% free with no swap). The edit stops only when the Mac is in trouble.
HEAVY_SWAP = 2 * GIB
SUSTAINED_WARNING_SECONDS = 30.0
WARNING_SWAP = 512 * 1024**2


def check_edit_pressure(
    memory: EditMemory,
    plan: EditPlan,
    initial_swap: int,
    used: int,
    warning_seconds: float = 0.0,
    critical_samples: int = 1,
):
    """Stop the edit when the system is in trouble or the editor runs away.

    ``used`` is the editor's own memory: its physical footprint on macOS, where
    memory-mapped weights are clean pages the system can drop, else its RSS.
    ``warning_seconds`` is how long pressure has stayed at warning or above, and
    ``critical_samples`` how many consecutive samples have been critical.
    """
    swapped = memory.swap_used - initial_swap
    advice = "Close apps or choose a smaller model, and retry."
    if used > plan.process_limit:
        reason = (
            f"the editor used {used / GIB:.1f} GiB, more than its "
            f"{plan.process_limit / GIB:.1f} GiB limit"
        )
    elif memory.unified:
        if memory.pressure == "critical" and critical_samples >= 2:
            reason = "macOS reported critical memory pressure"
        elif swapped > HEAVY_SWAP:
            reason = f"the Mac swapped {swapped / GIB:.1f} GiB to disk"
        elif warning_seconds >= SUSTAINED_WARNING_SECONDS and swapped > WARNING_SWAP:
            reason = (
                f"memory pressure stayed high for {warning_seconds:.0f} s "
                f"while the Mac swapped {swapped / GIB:.1f} GiB"
            )
        else:
            return
    elif memory.free_ram < plan.reserve_ram:
        reason = f"only {memory.free_ram / GIB:.1f} GiB of memory was left"
    elif swapped > WARNING_SWAP:
        reason = f"the system swapped {swapped / GIB:.1f} GiB"
    elif memory.pressure in {"warning", "critical", "high"}:
        reason = "memory pressure became high"
    else:
        return
    raise MemoryError(f"Editing stopped to protect system memory: {reason}. {advice}")
