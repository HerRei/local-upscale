"""Registry seam for temporal video engines.

The frame-by-frame path (model_kind "spandrel_image") runs through the
existing InferenceEngine and needs no entry here. Every other kind names
vendored engine code that loads a downloaded bundle and processes clips.
Resolution failures raise TemporalEngineUnavailable with a message fit for
the status bar, so an engine missing from a build degrades gracefully.
"""

from __future__ import annotations

import functools
import logging
import platform
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from importlib.util import find_spec

SPANDREL_IMAGE_KIND = "spandrel_image"
MLX_PROBE_ARGUMENT = "--mlx-probe"
MLX_PROBE_SECONDS = 60

logger = logging.getLogger(__name__)


class TemporalEngineUnavailable(RuntimeError):
    pass


def seedvr2_runtime_issue() -> str | None:
    """The pinned DirectML Torch cannot register modern Diffusers custom ops."""
    try:
        torch_version = tuple(int(part) for part in version("torch").split(".")[:2])
        diffusers_version = tuple(int(part) for part in version("diffusers").split(".")[:2])
    except PackageNotFoundError:
        return (
            "SeedVR2 requires the optional video runtime. Frame-by-frame video remains available."
        )
    if torch_version < (2, 5) and diffusers_version >= (0, 38):
        return (
            "SeedVR2 is unavailable with this engine's PyTorch version. "
            "Use a compatible CUDA, ROCm or Metal engine for SeedVR2. "
            "Frame-by-frame video remains available."
        )
    return None


def flashvsr_runtime_issue() -> str | None:
    """FlashVSR runs on Apple Silicon via MLX or on NVIDIA CUDA / AMD ROCm via PyTorch."""
    if sys.platform == "darwin" and platform.machine().lower() == "arm64":
        if not mlx_installed():
            return (
                "FlashVSR requires MLX on Apple Silicon, which is not installed in this build. "
                "Frame-by-frame video remains available."
            )
        if not mlx_usable():
            return (
                "FlashVSR could not start MLX on this Mac. Frame-by-frame video remains available."
            )
        return None
    try:
        import torch
    except (ImportError, OSError):
        return (
            "FlashVSR requires Apple Silicon (macOS arm64), NVIDIA CUDA, or AMD ROCm. "
            "Frame-by-frame video remains available."
        )
    torch_version = getattr(torch, "version", None)
    if getattr(torch_version, "hip", None) or getattr(torch_version, "cuda", None):
        return None
    return (
        "FlashVSR requires Apple Silicon (macOS arm64), NVIDIA CUDA, or AMD ROCm. "
        "Frame-by-frame video remains available."
    )


def mlx_installed() -> bool:
    if "mlx.core" in sys.modules:
        return sys.modules["mlx.core"] is not None
    return find_spec("mlx") is not None


def mlx_probe_main() -> int:
    """Child side of ``mlx_usable``: load MLX and run one tiny Metal computation."""
    import mlx.core as mx

    mx.eval(mx.zeros(4) + 1)
    return 0


def mlx_usable() -> bool:
    """Whether MLX loads and computes on this Mac, checked in a child process.

    MLX ends the process with a trap, not an exception, when its Metal library does not
    load (0.6.0 on macOS 14 and 15, issue #183). Importing it in the engine itself would
    take the whole engine down at the handshake, so only a child ever tries first.
    """
    if sys.modules.get("mlx.core") is not None:
        return True
    return _mlx_child_check()


@functools.cache
def _mlx_child_check() -> bool:
    command = [sys.executable]
    if not getattr(sys, "frozen", False):
        command += ["-m", "localsr.worker"]
    try:
        completed = subprocess.run(
            [*command, MLX_PROBE_ARGUMENT],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=MLX_PROBE_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        logger.warning("The MLX check could not run: %s", error)
        return False
    if completed.returncode != 0:
        tail = completed.stderr.decode("utf-8", "replace").splitlines()[-8:]
        logger.warning(
            "MLX is not usable on this Mac (exit %s); FlashVSR is off:\n%s",
            completed.returncode,
            "\n".join(tail),
        )
        return False
    return True


def resolve_video_engine(kind: str):
    """Return the engine factory for a temporal model kind.

    Returns None for the frame-by-frame kind. Raises
    TemporalEngineUnavailable for kinds this build cannot serve.
    """
    if kind == SPANDREL_IMAGE_KIND:
        return None
    if kind == "seedvr2":
        try:
            from localsr.video_models.seedvr2.engine import SeedVR2Engine
        except ImportError as error:
            raise TemporalEngineUnavailable(
                "The SeedVR2 temporal engine is not installed in this build. "
                "Frame-by-frame video upscaling remains available."
            ) from error
        return SeedVR2Engine
    if kind == "flashvsr":
        if issue := flashvsr_runtime_issue():
            raise TemporalEngineUnavailable(issue)
        try:
            from localsr.video_models.flashvsr.engine import FlashVSREngine
        except ImportError as error:
            raise TemporalEngineUnavailable(
                "The FlashVSR temporal engine is not installed in this build. "
                "Frame-by-frame video upscaling remains available."
            ) from error
        return FlashVSREngine
    raise TemporalEngineUnavailable(
        f"Unknown video engine '{kind}'. Frame-by-frame video upscaling remains available."
    )
