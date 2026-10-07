#!/usr/bin/env python3
"""Render the background of the LocalSR macOS disk image window.

The brand paper with one terracotta arrow pointing from the LocalSR icon to the
Applications shortcut. The window layout it is drawn for (content size, icon
size and icon centres) lives in scripts/make_macos_dmg.py and is imported from
there, so the arrow always sits between the two icons.

Writes background.png (1x) and background@2x.png next to this script; dmgbuild
combines the pair into one multi-resolution TIFF. Run after changing the
layout:

    .venv/bin/python packaging/macos/installer/build_background.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "scripts"))

from make_macos_dmg import (  # noqa: E402
    APP_ICON_CENTER,
    APPLICATIONS_ICON_CENTER,
    BACKGROUND_SIZE,
    ICON_SIZE,
)

# Brand colours shared with packaging/build_icons.py and the website: paper and
# the terracotta of the "LocalSR." dot.
PAPER_TOP = (247, 245, 238)
PAPER_BOTTOM = (233, 230, 219)
ACCENT = (179, 61, 38)
SUPERSAMPLE = 4

# Arrow geometry in points: a gap to each icon, a stroke with round caps and an
# open head.
ICON_GAP = 34
STROKE = 7
HEAD = 22


def render(scale: int) -> Image.Image:
    factor = scale * SUPERSAMPLE
    width, height = BACKGROUND_SIZE[0] * factor, BACKGROUND_SIZE[1] * factor
    image = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(image)
    for y in range(height):
        t = y / (height - 1)
        draw.line(
            (0, y, width, y),
            fill=tuple(
                round(a + (b - a) * t) for a, b in zip(PAPER_TOP, PAPER_BOTTOM, strict=True)
            ),
        )

    def p(x: float, y: float) -> tuple[float, float]:
        return x * factor, y * factor

    y = APP_ICON_CENTER[1]
    start = APP_ICON_CENTER[0] + ICON_SIZE / 2 + ICON_GAP
    tip = APPLICATIONS_ICON_CENTER[0] - ICON_SIZE / 2 - ICON_GAP
    stroke = round(STROKE * factor)
    radius = stroke / 2
    segments = [
        (p(start, y), p(tip, y)),
        (p(tip - HEAD, y - HEAD), p(tip, y)),
        (p(tip - HEAD, y + HEAD), p(tip, y)),
    ]
    for a, b in segments:
        draw.line((a, b), fill=ACCENT, width=stroke)
        for cx, cy in (a, b):
            draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=ACCENT)
    return image.resize(
        (BACKGROUND_SIZE[0] * scale, BACKGROUND_SIZE[1] * scale), Image.Resampling.LANCZOS
    )


def main() -> None:
    for scale, name in ((1, "background.png"), (2, "background@2x.png")):
        out = HERE / name
        image = render(scale)
        # 72 and 144 dpi tell tiffutil which page is the Retina one.
        image.save(out, dpi=(72 * scale, 72 * scale), optimize=True)
        print(f"wrote {out} ({image.width}x{image.height})")


if __name__ == "__main__":
    main()
