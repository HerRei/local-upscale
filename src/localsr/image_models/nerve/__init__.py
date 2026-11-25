"""Vendored NERVE network (Apache-2.0) and its Spandrel loader (MIT)."""

from .arch import NERVE
from .loader import NERVE_DETECTION_KEYS, NERVEArch, nerve_support

__all__ = ["NERVE", "NERVE_DETECTION_KEYS", "NERVEArch", "nerve_support"]
