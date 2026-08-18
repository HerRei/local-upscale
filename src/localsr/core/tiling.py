from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np


@dataclass
class TileCoordinates:
    # Input coordinates for the core tile (non-overlapping)
    core_x: int
    core_y: int
    core_w: int
    core_h: int

    # Input coordinates including the halo (might go out of bounds, requiring padding)
    halo_x: int
    halo_y: int
    halo_w: int
    halo_h: int

    # How much padding was needed for out-of-bounds halo regions
    pad_left: int
    pad_right: int
    pad_top: int
    pad_bottom: int

    # Where to write the output tile in the final scaled image
    out_x: int
    out_y: int
    out_w: int
    out_h: int


def generate_tiles(
    width: int, height: int, tile_size: int, halo: int, scale: int
) -> Iterator[TileCoordinates]:
    """
    Generate tile coordinates for an image of dimensions (width, height).
    """
    for y in range(0, height, tile_size):
        for x in range(0, width, tile_size):
            core_w = min(tile_size, width - x)
            core_h = min(tile_size, height - y)

            # Theoretical halo bounds
            halo_x = x - halo
            halo_y = y - halo
            halo_w = core_w + 2 * halo
            halo_h = core_h + 2 * halo

            # Actual bounds and padding
            pad_left = max(0, -halo_x)
            pad_top = max(0, -halo_y)
            pad_right = max(0, (halo_x + halo_w) - width)
            pad_bottom = max(0, (halo_y + halo_h) - height)

            # The out_ bounds are simply the core bounds multiplied by the scale
            out_x = x * scale
            out_y = y * scale
            out_w = core_w * scale
            out_h = core_h * scale

            yield TileCoordinates(
                core_x=x,
                core_y=y,
                core_w=core_w,
                core_h=core_h,
                halo_x=halo_x + pad_left,
                halo_y=halo_y + pad_top,
                halo_w=halo_w - pad_left - pad_right,
                halo_h=halo_h - pad_top - pad_bottom,
                pad_left=pad_left,
                pad_right=pad_right,
                pad_top=pad_top,
                pad_bottom=pad_bottom,
                out_x=out_x,
                out_y=out_y,
                out_w=out_w,
                out_h=out_h,
            )


def tile_face_overlap(
    tile: TileCoordinates,
    face_mask: np.ndarray,
) -> float:
    """Fraction of the tile's core region that overlaps a face.

    face_mask is a (H, W) boolean array at source-frame resolution.
    Returns 0.0 when the mask is empty.
    """
    if face_mask.size == 0:
        return 0.0
    core = face_mask[
        tile.core_y : tile.core_y + tile.core_h,
        tile.core_x : tile.core_x + tile.core_w,
    ]
    if core.size == 0:
        return 0.0
    return float(core.mean())
