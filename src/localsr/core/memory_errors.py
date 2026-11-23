"""Running out of graphics memory: how to recognise it and how to tell the user.

PyTorch reports a full GPU allocator with its own wording ("MPS backend out of memory
(MPS allocated: ...). Use PYTORCH_MPS_HIGH_WATERMARK_RATIO=0.0 ..."). That text helps
nobody on the status line, so a failed job states what ran out and what to change.
"""

from __future__ import annotations

import re

# What PyTorch's MPS, CUDA (also ROCm), HIP and XPU allocators raise when they are full.
_DEVICE_OUT_OF_MEMORY = re.compile(
    r"(?:MPS backend|CUDA|HIP|XPU) out of memory|PYTORCH_MPS_HIGH_WATERMARK_RATIO"
    r"|PYTORCH_CUDA_ALLOC_CONF",
    re.IGNORECASE,
)


class DeviceOutOfMemoryError(RuntimeError):
    """A step that still did not fit after LocalSR tried every smaller way to run it."""

    def __init__(self, message: str, *, device_type: str = "", step: str = ""):
        super().__init__(message)
        self.device_type = device_type
        self.step = step


def _chain(error: BaseException):
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        yield current
        seen.add(id(current))
        current = current.__cause__ or current.__context__


def is_device_out_of_memory(error: BaseException) -> bool:
    """Whether ``error`` is a GPU allocator that ran out of memory (not system RAM)."""
    import torch

    out_of_memory = getattr(torch, "OutOfMemoryError", None)
    return (out_of_memory is not None and isinstance(error, out_of_memory)) or bool(
        _DEVICE_OUT_OF_MEMORY.search(str(error))
    )


def memory_failure_message(
    error: BaseException, *, video: bool = False, interpolating_after_upscale: bool = False
) -> str | None:
    """The status-line sentence for a job that ran out of graphics memory, or None.

    A message that already explains itself (SeedVR2's measured report, say) keeps its
    words: only PyTorch's own text, or LocalSR's last-resort error, is replaced.
    """
    raw = bool(_DEVICE_OUT_OF_MEMORY.search(str(error)))
    gave_up = next(
        (link for link in _chain(error) if isinstance(link, DeviceOutOfMemoryError)), None
    )
    if not raw and gave_up is None:
        return None
    if gave_up is not None and gave_up.device_type == "cpu":
        what = "LocalSR ran out of memory"
    else:
        what = "LocalSR ran out of graphics memory"
    step = f" during {gave_up.step}" if gave_up is not None and gave_up.step else ""
    if not video:
        advice = "Turn on Safe memory mode, or choose a smaller tile size or output scale."
    elif interpolating_after_upscale:
        advice = (
            "Turn on Safe memory mode, choose a smaller output scale or fewer fixes, "
            "or set Interpolate to Before upscaling."
        )
    else:
        advice = "Turn on Safe memory mode, or choose a smaller output scale or fewer fixes."
    return f"{what}{step}. {advice}"


__all__ = [
    "DeviceOutOfMemoryError",
    "is_device_out_of_memory",
    "memory_failure_message",
]
