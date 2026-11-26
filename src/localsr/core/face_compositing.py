"""Tile classification and boundary blending for the face-aware pipeline.

Each tile is classified as 'face', 'general', or 'boundary' based on
how much of its core region overlaps a face bounding box. Boundary
tiles are run through both models and alpha-blended.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from .face_detection import smooth_alpha
from .image_formats import PILLOW_INPUT_FORMATS, require_ordinary_source

MASK_RESAMPLING = Image.Resampling.BILINEAR


def load_mask(mask_path: str | Path, width: int, height: int) -> np.ndarray:
    """Load an 8- or 16-bit single-channel mask and resize it to ``(height, width)``."""
    if width <= 0 or height <= 0:
        raise ValueError("Mask target dimensions must be positive.")
    path_str = str(mask_path)
    if not path_str or "\0" in path_str:
        raise ValueError("Mask path is invalid.")
    require_ordinary_source(path_str)
    with Image.open(path_str, formats=PILLOW_INPUT_FORMATS) as opened:
        exif = opened.getexif() if hasattr(opened, "getexif") else None
        img = ImageOps.exif_transpose(opened) if exif and exif.get(274, 1) != 1 else opened
        if img.mode in ("I;16", "I;16L", "I;16B", "I;16N"):
            arr = np.asarray(img, dtype=np.float32) / 65535.0
        elif img.mode == "I":
            raw = np.asarray(img, dtype=np.float32)
            scale = 255.0 if float(raw.max(initial=0.0)) <= 255.0 else 65535.0
            arr = raw / scale
        elif img.mode == "F":
            arr = np.asarray(img, dtype=np.float32)
        else:
            arr = np.asarray(img.convert("L"), dtype=np.float32) / 255.0
    arr = np.clip(arr, 0.0, 1.0).astype(np.float32, copy=False)
    if arr.shape != (height, width):
        arr = np.asarray(
            Image.fromarray(arr, mode="F").resize((width, height), MASK_RESAMPLING),
            dtype=np.float32,
        )
        arr = np.clip(arr, 0.0, 1.0)
    return arr


def blend_with_alpha(
    foreground: np.ndarray,
    background: np.ndarray,
    alpha: np.ndarray,
) -> np.ndarray:
    """Composite ``alpha * foreground + (1 - alpha) * background`` for ``(C, H, W)`` arrays."""
    if foreground.shape != background.shape:
        raise ValueError(
            f"Shape mismatch: foreground {foreground.shape} vs background {background.shape}"
        )
    alpha_3d = alpha[None, :, :] if alpha.ndim == 2 else alpha
    blended = foreground.astype(np.float32) * alpha_3d + background.astype(np.float32) * (
        1.0 - alpha_3d
    )
    if foreground.dtype == np.float32 and background.dtype == np.float32:
        return blended.clip(0.0, 1.0).astype(np.float32)
    if np.uint16 in (foreground.dtype, background.dtype):
        return np.rint(blended).clip(0, 65535).astype(np.uint16)
    return blended.clip(0, 255).astype(np.uint8)


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
        scale_y = out_h // alpha.shape[0]
        scale_x = out_w // alpha.shape[1]
        alpha = np.repeat(np.repeat(alpha, scale_y, axis=0), scale_x, axis=1)
        if alpha.shape[0] < out_h:
            alpha = np.pad(alpha, ((0, out_h - alpha.shape[0]), (0, 0)), mode="edge")
        if alpha.shape[1] < out_w:
            alpha = np.pad(alpha, ((0, 0), (0, out_w - alpha.shape[1])), mode="edge")
    # Broadcast alpha to (1, H, W) for channel-wise blending.
    face_mix = (
        identity_output.astype(np.float32) * (1.0 - fidelity)
        + face_output.astype(np.float32) * fidelity
    )
    return blend_with_alpha(face_mix, general_output, alpha)
