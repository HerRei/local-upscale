"""Vendored HEART network (Apache-2.0 / MIT) and its Spandrel loader (MIT)."""

from .arch import HEART
from .loader import HEART_DETECTION_KEYS, HEARTArch, heart_support

__all__ = ["HEART", "HEART_DETECTION_KEYS", "HEARTArch", "heart_support"]
