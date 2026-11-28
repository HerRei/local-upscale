"""Conservative admission and live pressure checks for native, quantized editing.

Profiles are estimates, not hardware acceptance results. No allocation probing,
swapping to discover capacity, or increase of the macOS working-set limit.
"""

from __future__ import annotations

import logging
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from localsr.core.disk_space import format_size

logger = logging.getLogger(__name__)

GIB = 1024**3
MAC_TIERS = (16, 24, 32, 36, 48, 64, 96, 128)
GPU_TIERS = (8, 12, 16, 20, 24, 32, 48)
ALLOWED_EDIT_DIMENSIONS = (1024, 768, 512)


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
    # Free space on the system volume, where macOS swaps to, and the part of it that
    # must stay free. 0 and 0 when unknown, which never stops an edit.
    disk_free: int = 0
    disk_reserve: int = 0
    # Linux: the part of swap_used held in compressed RAM (zram). Those pages already
    # count against available memory, so zram growing is no sign of disk thrashing.
    compressed_swap: int = 0
    # Linux PSI "full avg10": percent of the last 10 s in which every running task waited
    # for memory. None where the kernel reports no pressure (Windows, psi=0).
    memory_stall: float | None = None

    @property
    def disk_swap(self) -> int:
        return max(0, self.swap_used - self.compressed_swap)

    @property
    def free_ram(self) -> int:
        """Memory an edit can take now: the macOS free level when known, else available RAM.

        The macOS kernel's free level includes reclaimable memory, while
        psutil reports free and inactive pages.
        """
        return self.obtainable_ram or self.available_ram


@dataclass(frozen=True)
class EditPlan:
    required_ram: int
    reserve_ram: int
    gpu_budget: int
    max_dimension: int
    process_limit: int
    text_encoder: str = "cpu"
    vae: str = "cpu"
    offload: bool = True


PLACEMENT_CHOICES = ("auto", "gpu", "cpu")


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


def estimate_edit_memory(
    model: dict,
    unified: bool,
    limit: int,
    *,
    text_encoder: str = "cpu",
    vae: str = "cpu",
) -> int:
    """Resident host memory at the bundle's heaviest phase, in bytes.

    Weights are memory-mapped and touched lazily: the encoders are resident while
    the prompt and reference are encoded, the transformer while the image is
    sampled, and the phase that is over can be evicted. On unified memory the
    transformer lives in the same pool; beside a dedicated GPU the card holds
    the transformer and any component placed on the GPU, while the host holds
    components placed on the CPU.
    """
    sizes = {f["role"]: f["size_bytes"] for f in model["files"]}
    activations = activation_memory(unified, limit)
    conditioning = (
        0 if text_encoder == "gpu" else sizes.get("text_encoder", 0) + sizes.get("vision", 0)
    )
    vae_host = 0 if vae == "gpu" else sizes.get("vae", 0)
    sampling = vae_host + activations + (sizes["diffusion"] if unified else 0)
    return int(max(conditioning, sampling) * 1.1)


def minimum_unified_memory_gb(model: dict) -> int:
    """Smallest Mac memory size (in GiB) whose idle headroom admits a 512 px edit."""
    diffusion = next((f["size_bytes"] for f in model["files"] if f["role"] == "diffusion"), 0)
    vae = next((f["size_bytes"] for f in model["files"] if f["role"] == "vae"), 0)
    needed = estimate_edit_memory(model, True, 512)
    sampling = diffusion + vae + activation_memory(True, 512)
    for tier in MAC_TIERS:
        if (
            needed + reserve_for(tier * GIB, True) <= 0.70 * tier * GIB
            and sampling <= 0.60 * tier * GIB
        ):
            return tier
    return max(
        MAC_TIERS[-1],
        math.ceil(max((needed + 2 * GIB) / 0.70, sampling / 0.60, needed / 0.60) / GIB),
    )


def minimum_vram_gb(model: dict) -> int:
    """Smallest dedicated GPU memory size (in GiB) that holds the diffusion weights."""
    diffusion = next((f["size_bytes"] for f in model["files"] if f["role"] == "diffusion"), 0)
    for tier in GPU_TIERS:
        if diffusion <= 0.87 * tier * GIB:
            return tier
    return max(GPU_TIERS[-1], math.ceil(diffusion / (0.87 * GIB)))


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


PROC_SWAPS = Path("/proc/swaps")
PROC_MEMORY_PRESSURE = Path("/proc/pressure/memory")


def _zram_swap_used() -> int:
    """Bytes of swap in use on zram devices, from /proc/swaps (sizes in KiB)."""
    try:
        lines = PROC_SWAPS.read_text(encoding="utf-8").splitlines()[1:]
    except OSError:
        return 0
    used = 0
    for line in lines:
        fields = line.split()
        if len(fields) >= 5 and re.fullmatch(r"/dev/zram\d+", fields[0]):
            try:
                used += int(fields[3]) * 1024
            except ValueError:
                continue
    return used


def _memory_stall() -> float | None:
    """The kernel's "full avg10" memory pressure in percent, or None without PSI."""
    try:
        text = PROC_MEMORY_PRESSURE.read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r"^full avg10=(\d+(?:\.\d+)?)", text, re.MULTILINE)
    return float(match.group(1)) if match else None


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
    disk_free = disk_reserve = 0
    if unified:
        from localsr.core import disk_space

        try:
            volume = disk_space.volume_of(Path.home())
            disk_free, disk_reserve = volume.free, disk_space.reserve_for(volume)
        except (OSError, RuntimeError):
            pass
    compressed_swap, memory_stall = 0, None
    if not unified and sys.platform.startswith("linux"):
        compressed_swap, memory_stall = _zram_swap_used(), _memory_stall()
    return EditMemory(
        int(ram.total),
        int(ram.available),
        int(swap.used),
        unified=unified,
        pressure=pressure,
        obtainable_ram=obtainable,
        disk_free=disk_free,
        disk_reserve=disk_reserve,
        compressed_swap=compressed_swap,
        memory_stall=memory_stall,
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


def _validate_placement(name: str, value: str) -> str:
    if value not in PLACEMENT_CHOICES:
        raise ValueError(f"{name} must be auto, gpu or cpu.")
    return value


def _validate_vram_limit_mb(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or 0 < value < 1024:
        raise ValueError("edit_vram_limit_mb must be 0 or at least 1024 MiB.")
    return value


def _choose_unified_placement(
    model: dict,
    memory: EditMemory,
    reserve: int,
    limit: int,
    diffusion_bytes: int,
    vae_bytes: int,
    encoder_bytes: int,
    activations: int,
    total_bytes: int,
    text_encoder: str,
    vae: str,
) -> tuple[str, str, bool, int]:
    if text_encoder != "gpu" and vae != "gpu":
        required = estimate_edit_memory(model, True, limit)
        return "cpu", "cpu", True, required
    if model.get("family") == "mage-flow-edit-turbo":
        raise MemoryError(
            "Mage-Flow runs on the CPU on Apple Silicon because Metal produces blank output."
        )
    unified_cap = min(int(memory.total_ram * 0.60), memory.free_ram - reserve)
    vae_need = diffusion_bytes + vae_bytes + activations
    if vae == "gpu" and vae_need > unified_cap:
        raise MemoryError(
            f"Keeping the VAE on the GPU needs about {vae_need / GIB:.1f} GiB of unified memory, "
            f"but only {max(0, unified_cap) / GIB:.1f} GiB is available."
        )
    vae_place = "gpu" if vae == "gpu" else "cpu"
    te_need = (
        diffusion_bytes + (vae_bytes if vae_place == "gpu" else 0) + encoder_bytes + activations
    )
    if text_encoder == "gpu" and te_need > unified_cap:
        raise MemoryError(
            f"Keeping the text encoder on the GPU needs about {te_need / GIB:.1f} GiB of unified "
            f"memory, but only {max(0, unified_cap) / GIB:.1f} GiB is available."
        )
    te_place = "gpu" if text_encoder == "gpu" else "cpu"
    offload = not (
        te_place == "gpu" and vae_place == "gpu" and total_bytes + activations <= unified_cap
    )
    resident_gpu = (
        diffusion_bytes
        + (vae_bytes if vae_place == "gpu" else 0)
        + (encoder_bytes if te_place == "gpu" else 0)
        + activations
    )
    required = max(estimate_edit_memory(model, True, limit), int(resident_gpu * 1.1))
    return te_place, vae_place, offload, required


def _choose_discrete_placement(
    budget: int,
    diffusion_bytes: int,
    vae_bytes: int,
    encoder_bytes: int,
    activations: int,
    total_bytes: int,
    text_encoder: str,
    vae: str,
) -> tuple[str, str, bool]:
    vae_need = diffusion_bytes + vae_bytes + activations
    vae_fits = vae_need <= budget
    if vae == "cpu":
        vae_place = "cpu"
    elif vae == "gpu":
        if not vae_fits:
            raise MemoryError(
                f"Keeping the VAE on the GPU needs about {vae_need / GIB:.1f} GiB of VRAM, "
                f"but only {budget / GIB:.1f} GiB is budgeted."
            )
        vae_place = "gpu"
    else:
        vae_place = "gpu" if vae_fits else "cpu"

    te_need = (
        diffusion_bytes + (vae_bytes if vae_place == "gpu" else 0) + encoder_bytes + activations
    )
    te_fits = te_need <= budget
    if text_encoder == "cpu":
        te_place = "cpu"
    elif text_encoder == "gpu":
        if not te_fits:
            raise MemoryError(
                f"Keeping the text encoder on the GPU needs about {te_need / GIB:.1f} GiB of VRAM, "
                f"but only {budget / GIB:.1f} GiB is budgeted."
            )
        te_place = "gpu"
    else:
        te_place = "gpu" if (vae == "cpu" or vae_place == "gpu") and te_fits else "cpu"

    offload = not (te_place == "gpu" and vae_place == "gpu" and total_bytes + activations <= budget)
    return te_place, vae_place, offload


def _plan_discrete_for_limit(
    model: dict,
    memory: EditMemory,
    reserve: int,
    budget: int,
    limit: int,
    diffusion_bytes: int,
    vae_bytes: int,
    encoder_bytes: int,
    total_bytes: int,
    text_encoder: str,
    vae: str,
) -> EditPlan:
    activations = activation_memory(False, limit)
    process_limit = int(total_bytes * 1.1 + activations)
    te_place, vae_place, offload = _choose_discrete_placement(
        budget,
        diffusion_bytes,
        vae_bytes,
        encoder_bytes,
        activations,
        total_bytes,
        text_encoder,
        vae,
    )
    required = estimate_edit_memory(model, False, limit, text_encoder=te_place, vae=vae_place)
    if memory.free_ram - reserve < required:
        raise MemoryError(
            f"Editing needs about {required / GIB:.1f} GiB available plus "
            f"a {reserve / GIB:.1f} GiB reserve for other apps; "
            f"only {memory.free_ram / GIB:.1f} GiB is currently available. "
            "Close apps or choose a smaller model or edit size, and retry."
        )
    return EditPlan(required, reserve, budget, limit, process_limit, te_place, vae_place, offload)


def plan_edit(
    model: dict,
    memory: EditMemory,
    max_dimension: int = 512,
    *,
    text_encoder: str = "auto",
    vae: str = "auto",
    vram_limit_mb: int = 0,
    allow_any_size: bool = False,
) -> EditPlan:
    text_encoder = _validate_placement("edit_text_encoder", text_encoder)
    vae = _validate_placement("edit_vae", vae)
    vram_limit_mb = _validate_vram_limit_mb(vram_limit_mb)
    if memory.pressure in {"critical", "high"} or (
        not memory.unified and memory.pressure == "warning"
    ):
        raise MemoryError("Memory pressure is high. Close other apps before editing.")
    reserve = reserve_for(memory.total_ram, memory.unified)
    requested_limit = min(1024, max_dimension)
    sizes = {f["role"]: f["size_bytes"] for f in model["files"]}
    diffusion_bytes = sizes.get("diffusion", 0)
    vae_bytes = sizes.get("vae", 0)
    encoder_bytes = sizes.get("text_encoder", 0) + sizes.get("vision", 0)
    total_bytes = mapped_size(model)
    if memory.unified:
        if vram_limit_mb:
            logger.info("Ignoring edit_vram_limit_mb=%d MiB on unified memory.", vram_limit_mb)
        min_unified = model.get("min_unified_memory_gb")
        if min_unified is None:
            min_unified = minimum_unified_memory_gb(model)
        if memory.total_ram < min_unified * GIB:
            raise MemoryError("Choose a smaller editing model for this Mac.")
        limit = requested_limit
        # An oversized edit in unified memory swaps the whole Mac instead of failing
        # cleanly, so the Mac limits hold even when any size is allowed.
        if allow_any_size:
            logger.info("Ignoring edit_allow_any_size on unified memory.")
        if memory.total_ram < 36 * GIB:
            limit = min(limit, 512)
        elif memory.total_ram < 48 * GIB:
            limit = min(limit, 768)
        activations = activation_memory(True, limit)
        # The supervisor watches the real numbers while the model runs. Its process limit
        # allows every mapped file to stay resident, which is normal when memory is plentiful.
        process_limit = int(total_bytes * 1.1 + activations)
        te_place, vae_place, offload, required = _choose_unified_placement(
            model,
            memory,
            reserve,
            limit,
            diffusion_bytes,
            vae_bytes,
            encoder_bytes,
            activations,
            total_bytes,
            text_encoder,
            vae,
        )
        # Metal may wire well over half of unified memory; stay under Apple's
        # recommended working set with room for the encoders and the OS.
        budget = min(required, int(memory.total_ram * 0.60), memory.free_ram - reserve)
        if memory.free_ram - reserve < required:
            raise MemoryError(
                f"Editing needs about {required / GIB:.1f} GiB available plus "
                f"a {reserve / GIB:.1f} GiB reserve for other apps; "
                f"only {memory.free_ram / GIB:.1f} GiB is currently available. "
                "Close apps or choose a smaller model or edit size, and retry."
            )
        return EditPlan(
            required, reserve, budget, limit, process_limit, te_place, vae_place, offload
        )

    # Drivers may report a little less than the card's nominal capacity.
    capacity = memory.gpu_total + 256 * 1024**2
    min_vram = model.get("min_vram_gb")
    if min_vram is None:
        min_vram = minimum_vram_gb(model)
    if capacity < min_vram * GIB:
        raise MemoryError("Choose a smaller editing model for this GPU.")
    if vram_limit_mb:
        max_vram_mb = memory.gpu_total // (1024**2)
        if vram_limit_mb > max_vram_mb:
            raise ValueError(f"edit_vram_limit_mb must be at most {max_vram_mb} MiB for this GPU.")
        budget = vram_limit_mb * 1024**2
    else:
        gpu_reserve = max(1 * GIB, int(memory.gpu_total * 0.08))
        budget = min(int(memory.gpu_total * 0.90), memory.gpu_free - gpu_reserve)
        if budget < 4 * GIB:
            raise MemoryError("Too little VRAM is free. Close GPU apps before editing.")

    if allow_any_size:
        return _plan_discrete_for_limit(
            model,
            memory,
            reserve,
            budget,
            requested_limit,
            diffusion_bytes,
            vae_bytes,
            encoder_bytes,
            total_bytes,
            text_encoder,
            vae,
        )

    candidates = [size for size in ALLOWED_EDIT_DIMENSIONS if size <= requested_limit] or [
        requested_limit
    ]
    for candidate in candidates[:-1]:
        if diffusion_bytes + activation_memory(False, candidate) > budget:
            continue
        try:
            return _plan_discrete_for_limit(
                model,
                memory,
                reserve,
                budget,
                candidate,
                diffusion_bytes,
                vae_bytes,
                encoder_bytes,
                total_bytes,
                text_encoder,
                vae,
            )
        except MemoryError:
            continue
    return _plan_discrete_for_limit(
        model,
        memory,
        reserve,
        budget,
        candidates[-1],
        diffusion_bytes,
        vae_bytes,
        encoder_bytes,
        total_bytes,
        text_encoder,
        vae,
    )


# On unified memory macOS compresses and swaps to make room, and on a busy 16 GB
# Mac loading a model pushes a few GiB of other apps to swap by design. The stops
# aim at the two real dangers: thrashing, and filling the system disk with swap.
SWAP_FLOOR = 4 * GIB
SWAP_SHARE = 0.5
SWAP_GROWING = 512 * 1024**2
SUSTAINED_WARNING_SECONDS = 90.0
SUSTAINED_WARNING_SWAP = 2 * GIB

# Beside a dedicated GPU, Linux moves cold pages to swap long before memory runs short,
# into zram on many distributions: a PC with 18 GiB available stopped an edit after
# 0.5 GiB of swap. So swap counts only on disk, only while available memory is within two
# reserves of the stop line, and only what grew since. Linux PSI shows thrashing that
# available memory hides, such as mapped weights read again and again; systemd-oomd's
# defaults act at 50 % or more for 20-30 s, so the edit stops well before that.
TIGHT_RESERVES = 2
TIGHT_DISK_SWAP = 512 * 1024**2
MEMORY_STALL_PERCENT = 20.0
MEMORY_STALL_SECONDS = 10.0


def memory_stalled(memory: EditMemory) -> bool:
    """Whether Linux reports that every running task often waits for memory."""
    return memory.memory_stall is not None and memory.memory_stall >= MEMORY_STALL_PERCENT


def memory_tight(memory: EditMemory, plan: EditPlan) -> bool:
    """Whether available memory is within two reserves of the stop line."""
    return memory.free_ram < TIGHT_RESERVES * plan.reserve_ram


def check_edit_pressure(
    memory: EditMemory,
    plan: EditPlan,
    initial_swap: int,
    used: int,
    warning_seconds: float = 0.0,
    critical_samples: int = 1,
    warning_swap: int | None = None,
    *,
    stall_seconds: float = 0.0,
    tight_swap: int | None = None,
):
    """Stop the edit when the system is in trouble or the editor runs away.

    ``used`` is the editor's own memory: its physical footprint on macOS, where
    memory-mapped weights are clean pages the system can drop, else its RSS.
    ``warning_seconds`` is how long pressure has stayed at warning or above, and
    ``critical_samples`` how many consecutive samples have been critical, and
    ``warning_swap`` the swap in use when that pressure began (macOS).
    ``stall_seconds`` is how long the Linux memory stall has stayed at its limit, and
    ``tight_swap`` the disk swap in use when memory became tight, None while it is not
    (beside a dedicated GPU).
    """
    swapped = memory.swap_used - initial_swap
    advice = "Close apps or choose a smaller model, and retry."
    if used > plan.process_limit:
        reason = (
            f"the editor used {used / GIB:.1f} GiB, more than its "
            f"{plan.process_limit / GIB:.1f} GiB limit"
        )
        advice = "Choose a smaller size or model, and retry."
    elif memory.unified:
        if memory.pressure == "critical" and critical_samples >= 2:
            reason = "macOS reported critical memory pressure"
        elif swapped > max(SWAP_FLOOR, int(memory.total_ram * SWAP_SHARE)):
            reason = (
                f"the Mac swapped {swapped / GIB:.1f} GiB to disk, more than half of its memory"
            )
        elif swapped > SWAP_GROWING and memory.disk_free < memory.disk_reserve:
            reason = (
                f"only {format_size(memory.disk_free)} of disk space was free "
                f"on the system volume while the Mac swapped {swapped / GIB:.1f} GiB to it"
            )
            advice = "Free up disk space, and retry."
        elif (
            warning_seconds >= SUSTAINED_WARNING_SECONDS
            and memory.swap_used - (initial_swap if warning_swap is None else warning_swap)
            > SUSTAINED_WARNING_SWAP
        ):
            reason = (
                f"memory pressure stayed high for {warning_seconds:.0f} s "
                "while the Mac kept swapping"
            )
        else:
            return
    elif memory.free_ram < plan.reserve_ram:
        reason = f"only {memory.free_ram / GIB:.1f} GiB of memory was left"
    elif memory_stalled(memory) and stall_seconds >= MEMORY_STALL_SECONDS:
        reason = (
            f"the system waited for memory {memory.memory_stall:.0f}% of the time "
            f"for {stall_seconds:.0f} s"
        )
    elif (
        tight_swap is not None
        and memory_tight(memory, plan)
        and memory.disk_swap - tight_swap > TIGHT_DISK_SWAP
    ):
        reason = (
            f"the system swapped {(memory.disk_swap - tight_swap) / GIB:.1f} GiB to disk "
            f"while only {memory.free_ram / GIB:.1f} GiB of memory was available"
        )
    elif memory.pressure in {"warning", "critical", "high"}:
        reason = "memory pressure became high"
    else:
        return
    raise MemoryError(f"Editing stopped to protect system memory: {reason}. {advice}")
