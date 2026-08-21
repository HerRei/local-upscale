"""Registry seam for temporal video engines.

The frame-by-frame path (model_kind "spandrel_image") runs through the
existing InferenceEngine and needs no entry here. Every other kind names
vendored engine code that loads a downloaded bundle and processes clips.
Resolution failures raise TemporalEngineUnavailable with a message fit for
the status bar, so an engine missing from a build degrades gracefully.
"""

from __future__ import annotations

SPANDREL_IMAGE_KIND = "spandrel_image"


class TemporalEngineUnavailable(RuntimeError):
    pass


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
