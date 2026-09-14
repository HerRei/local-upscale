"""Registry seam for temporal video engines.

The frame-by-frame path (model_kind "spandrel_image") runs through the
existing InferenceEngine and needs no entry here. Every other kind names
vendored engine code that loads a downloaded bundle and processes clips.
Resolution failures raise TemporalEngineUnavailable with a message fit for
the status bar, so an engine missing from a build degrades gracefully.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

SPANDREL_IMAGE_KIND = "spandrel_image"


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
    raise TemporalEngineUnavailable(
        f"Unknown video engine '{kind}'. Frame-by-frame video upscaling remains available."
    )
