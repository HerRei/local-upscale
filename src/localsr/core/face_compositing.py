"""Tile classification and boundary blending for the face-aware pipeline.

Each tile is classified as 'face', 'general', or 'boundary' based on
how much of its core region overlaps a face bounding box. Boundary
tiles are run through both models and alpha-blended.
"""

from __future__ import annotations

import numpy as np

from .face_detection import smooth_alpha


def classify_tile(
    face_alpha: np.ndarray,
    face_threshold_high: float = 0.85,
    face_threshold_low: float = 0.15,
) -> str:
    """Classify a tile based on mean face overlap of its core.

    Returns 'face', 'general', or 'boundary'.
    """
    if face_alpha.size == 0:
        return "general"
    mean_overlap = float(face_alpha.mean())
    if mean_overlap >= face_threshold_high:
        return "face"
    if mean_overlap <= face_threshold_low:
        return "general"
    return "boundary"


def blend_tile_outputs(
    face_output: np.ndarray,
    general_output: np.ndarray,
    face_alpha: np.ndarray,
    fidelity: float = 1.0,
    identity_output: np.ndarray | None = None,
) -> np.ndarray:
    """Blend restored faces over the general output with an identity endpoint.

    All inputs are (C, H, W) with face_output/general_output as uint8
    and face_alpha as float32 in [0, 1]. The alpha is at core resolution
    while the outputs are at core * scale resolution; the alpha is
    nearest-upscaled to match. Returns uint8 (C, H, W).
    """
    if face_output.shape != general_output.shape:
        raise ValueError(
            f"Shape mismatch: face {face_output.shape} vs general {general_output.shape}"
        )
    if identity_output is None:
        identity_output = general_output
    if identity_output.shape != general_output.shape:
        raise ValueError(
            f"Shape mismatch: identity {identity_output.shape} vs general {general_output.shape}"
        )
    if not 0.0 <= fidelity <= 1.0:
        raise ValueError("Face restoration fidelity must be between 0 and 1.")
    # The companion HAT checkpoint has no native fidelity parameter. LocalSR
    # therefore applies two spatial blends:
    #   face_mix = original_upscaled * (1-fidelity) + restored * fidelity
    #   output = general * (1-mask) + face_mix * mask
    # A value of 0 retains the resampled original inside the face mask; 1 uses
    # the strongest checkpoint result. Non-face pixels always remain general.
    alpha = smooth_alpha(face_alpha, kernel_size=5)
    # Upscale alpha from core resolution to output resolution (core * scale).
    out_h, out_w = face_output.shape[1], face_output.shape[2]
    if alpha.shape != (out_h, out_w):
        # Nearest-neighbor upscale via numpy repeat.
        scale_y = out_h // alpha.shape[0]
        scale_x = out_w // alpha.shape[1]
        alpha = np.repeat(np.repeat(alpha, scale_y, axis=0), scale_x, axis=1)
        # Handle any remainder from integer division.
        if alpha.shape[0] < out_h:
            alpha = np.pad(alpha, ((0, out_h - alpha.shape[0]), (0, 0)), mode="edge")
        if alpha.shape[1] < out_w:
            alpha = np.pad(alpha, ((0, 0), (0, out_w - alpha.shape[1])), mode="edge")
    # Broadcast alpha to (1, H, W) for channel-wise blending.
    alpha_3d = alpha[None, :, :]
    face_mix = (
        identity_output.astype(np.float32) * (1.0 - fidelity)
        + face_output.astype(np.float32) * fidelity
    )
    blended = face_mix * alpha_3d + general_output.astype(np.float32) * (1.0 - alpha_3d)
    if face_output.dtype == np.float32 and general_output.dtype == np.float32:
        return blended.clip(0, 1).astype(np.float32)
    return blended.clip(0, 255).astype(np.uint8)
