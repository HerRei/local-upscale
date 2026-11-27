"""Seeded film grain for the model-less ``grain`` step."""

from __future__ import annotations

import numpy as np
import torch

# Standard deviation of the grain at strength 100, in units of the full 0..1 range.
MAX_GRAIN_SIGMA = 0.1
# Grain is drawn a band of rows at a time, so an upscaled photo never needs a second
# full-size noise buffer next to its pixels.
_BAND_ROWS = 256


def add_grain(pixels: torch.Tensor, *, strength: int, seed: int) -> torch.Tensor:
    """Return ``pixels`` (C, H, W, values 0..1) with monochrome Gaussian grain.

    The same pixels, strength and seed give the same result on every run; strength 0
    returns an unchanged copy. ``pixels`` itself is not modified.
    """
    grained = pixels.detach().to(dtype=torch.float32, copy=True)
    if strength <= 0:
        return grained
    sigma = MAX_GRAIN_SIGMA * strength / 100.0
    generator = np.random.default_rng(seed)
    _, height, width = grained.shape
    for top in range(0, height, _BAND_ROWS):
        rows = min(_BAND_ROWS, height - top)
        noise = torch.from_numpy(generator.standard_normal((rows, width), dtype=np.float32))
        band = grained[:, top : top + rows, :]
        band.add_(noise.mul_(sigma)).clamp_(0.0, 1.0)
    return grained
