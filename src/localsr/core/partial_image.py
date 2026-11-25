"""An image whose job was stopped after some of its tiles were finished.

When the stop keeps the finished part, the tiles done so far stay as they are and the
rest of the picture is a plain resize of the image the model was given, so the result
is a complete picture of the requested size. The finished tiles are reported in the
saved image's pixels, for the app to mark them.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import torch


class TilesStopped(InterruptedError):
    """A stop that keeps the finished part, after at least one tile was finished.

    ``writer`` holds the model's output with those tiles written; ``finished`` lists them
    as (x, y, width, height) in that output's pixels.
    """

    def __init__(self, writer, finished: list[tuple[int, int, int, int]], total: int):
        super().__init__("Stopped; the finished tiles are kept.")
        self.writer = writer
        self.finished = list(finished)
        self.total = total
        self.info = None


def fill_unfinished(
    output: np.ndarray, finished: list[tuple[int, int, int, int]], source: torch.Tensor
) -> None:
    """Fill every pixel of ``output`` (C, H, W uint8) outside ``finished`` with ``source``
    (C, h, w float in 0..1) resized bilinearly to the output's size.

    The output is a whole multiple of the source (the model's scale), so each band of
    output rows is resized from its own source rows exactly as the whole picture would be:
    bands bound the memory of a large picture without seams.
    """
    channels, height, width = output.shape
    scale = height // source.shape[1]
    if scale < 1 or source.shape[1] * scale != height or source.shape[2] * scale != width:
        raise ValueError("The output must be a whole multiple of the source.")
    mask = np.zeros((height, width), dtype=bool)
    for x, y, w, h in finished:
        mask[y : y + h, x : x + w] = True
    band = 64  # source rows per band
    for first in range(0, source.shape[1], band):
        last = min(source.shape[1], first + band)
        top, bottom = first * scale, last * scale
        keep = mask[top:bottom]
        if keep.all():
            continue
        # One source row of context each side, so the filter sees what it would whole.
        lo, hi = max(0, first - 1), min(source.shape[1], last + 1)
        resized = torch.nn.functional.interpolate(
            source[:, lo:hi].unsqueeze(0).float(),
            scale_factor=(scale, scale),
            mode="bilinear",
            align_corners=False,
            recompute_scale_factor=False,
        )[0]
        piece = resized[:, (first - lo) * scale : (first - lo) * scale + (bottom - top)]
        pixels = piece.clamp(0, 1).mul(255).round().byte().numpy()
        for channel in range(channels):
            np.copyto(output[channel, top:bottom], pixels[channel], where=~keep)


def saved_tiles(
    finished: list[tuple[int, int, int, int]], scale: int, output_scale: int
) -> list[list[int]]:
    """The finished tiles in the saved image's pixels (it may be resized below the model's
    scale)."""
    factor = output_scale / scale if scale else 1.0
    return [
        [round(x * factor), round(y * factor), round(w * factor), round(h * factor)]
        for x, y, w, h in finished
    ]


def partial_output_path(requested: str) -> str:
    """`<name>_partial.<ext>` beside the requested output, numbered if that name is taken."""
    path = Path(requested)
    candidate = path.with_name(f"{path.stem}_partial{path.suffix}")
    number = 2
    while os.path.lexists(candidate):
        candidate = path.with_name(f"{path.stem}_partial-{number}{path.suffix}")
        number += 1
    return str(candidate)
