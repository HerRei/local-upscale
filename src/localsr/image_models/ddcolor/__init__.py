"""Vendored DDColor inference architecture and Lab colorization pipeline."""

from .engine import (
    ALREADY_COLORFUL_NOTE,
    COLORFUL_INPUT_CHROMA_THRESHOLD,
    DDCOLOR_INPUT_SIZE,
    ColorizeResult,
    DDColorConfig,
    DDColorDescriptor,
    colorize_rgb_array,
    colorize_tensor,
    infer_ddcolor_config,
    is_ddcolor_state_dict,
    lab_to_rgb,
    load_ddcolor_checkpoint,
    load_ddcolor_descriptor,
    mean_lab_chroma,
    rgb_to_lab,
    unwrap_ddcolor_state_dict,
)
from .model import DDColor

__all__ = [
    "ALREADY_COLORFUL_NOTE",
    "COLORFUL_INPUT_CHROMA_THRESHOLD",
    "DDCOLOR_INPUT_SIZE",
    "ColorizeResult",
    "DDColor",
    "DDColorConfig",
    "DDColorDescriptor",
    "colorize_rgb_array",
    "colorize_tensor",
    "infer_ddcolor_config",
    "is_ddcolor_state_dict",
    "lab_to_rgb",
    "load_ddcolor_checkpoint",
    "load_ddcolor_descriptor",
    "mean_lab_chroma",
    "rgb_to_lab",
    "unwrap_ddcolor_state_dict",
]
