#!/usr/bin/env python3
"""Generate deterministic synthetic illustration fixtures for colour, QR, text, and line art."""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "illustration"

COLOUR_PATCHES: list[tuple[str, tuple[int, int, int]]] = [
    ("saturated_red", (230, 40, 40)),
    ("saturated_green", (40, 200, 40)),
    ("saturated_blue", (40, 70, 230)),
    ("saturated_yellow", (240, 215, 40)),
    ("saturated_cyan", (40, 205, 220)),
    ("saturated_magenta", (220, 45, 190)),
    ("near_white", (250, 250, 250)),
    ("paper_cream", (245, 240, 228)),
    ("bluish_grey", (160, 175, 195)),
    ("mid_grey", (128, 128, 128)),
    ("near_black", (20, 20, 24)),
    ("pastel_pink", (242, 190, 200)),
    ("pastel_mint", (185, 235, 205)),
    ("pastel_sky", (175, 210, 242)),
    ("skin_light", (238, 196, 170)),
    ("skin_warm", (198, 145, 108)),
]


def build_colour_patches() -> Image.Image:
    """Create a 256x256 4x4 grid of 64x64 flat colour patches."""
    arr = np.zeros((256, 256, 3), dtype=np.uint8)
    for idx, (_name, rgb) in enumerate(COLOUR_PATCHES):
        row, col = divmod(idx, 4)
        arr[row * 64 : (row + 1) * 64, col * 64 : (col + 1) * 64] = rgb
    return Image.fromarray(arr, mode="RGB")


def _draw_finder_pattern(grid: np.ndarray, top: int, left: int) -> None:
    """Stamp a standard 7x7 QR finder pattern and 1-module white separator."""
    h, w = grid.shape
    y0, y1 = max(0, top - 1), min(h, top + 8)
    x0, x1 = max(0, left - 1), min(w, left + 8)
    grid[y0:y1, x0:x1] = 0
    grid[top : top + 7, left : left + 7] = 1
    grid[top + 1 : top + 6, left + 1 : left + 6] = 0
    grid[top + 2 : top + 5, left + 2 : left + 5] = 1


def make_qr_matrix(modules: int = 25, seed: int = 122) -> np.ndarray:
    """Build a deterministic QR-like binary module matrix (1 = black, 0 = white)."""
    rng = random.Random(seed)
    grid = np.array(
        [[rng.randint(0, 1) for _ in range(modules)] for _ in range(modules)],
        dtype=np.uint8,
    )
    # Timing patterns on row 6 and column 6
    for i in range(8, modules - 8):
        bit = 1 if i % 2 == 0 else 0
        grid[6, i] = bit
        grid[i, 6] = bit
    _draw_finder_pattern(grid, 0, 0)
    _draw_finder_pattern(grid, 0, modules - 7)
    _draw_finder_pattern(grid, modules - 7, 0)
    return grid


def build_qr_sheet() -> tuple[Image.Image, dict[str, object]]:
    """Build a 512x512 sheet of QR-like patterns at 1..6 px/module on bluish-grey paper."""
    bg_rgb = (165, 180, 200)
    canvas = Image.new("RGB", (512, 512), bg_rgb)
    arr = np.array(canvas, dtype=np.uint8)
    matrix = make_qr_matrix(modules=25, seed=122)
    n_mod = matrix.shape[0]
    quiet = 3  # modules of white quiet zone around each code

    # Place 1..6 px/module codes with generous spacing on a 512x512 canvas.
    placements = [
        (1, 20, 20),
        (2, 75, 20),
        (3, 165, 20),
        (4, 290, 20),
        (5, 20, 220),
        (6, 240, 220),
    ]
    entries: list[dict[str, object]] = []
    for mod_px, origin_x, origin_y in placements:
        box_px = (n_mod + 2 * quiet) * mod_px
        arr[origin_y : origin_y + box_px, origin_x : origin_x + box_px] = (255, 255, 255)
        qr_x = origin_x + quiet * mod_px
        qr_y = origin_y + quiet * mod_px
        scaled = np.repeat(np.repeat(matrix, mod_px, axis=0), mod_px, axis=1)
        code_px = n_mod * mod_px
        patch = arr[qr_y : qr_y + code_px, qr_x : qr_x + code_px]
        patch[scaled == 1] = (0, 0, 0)
        entries.append(
            {
                "module_px": mod_px,
                "box_xywh": [origin_x, origin_y, box_px, box_px],
                "qr_xywh": [qr_x, qr_y, code_px, code_px],
                "modules": n_mod,
            }
        )

    manifest = {
        "background_rgb": list(bg_rgb),
        "matrix": matrix.tolist(),
        "codes": entries,
    }
    return Image.fromarray(arr, mode="RGB"), manifest


def build_small_text() -> Image.Image:
    """Render PIL default-font text at several sizes on light, dark, and tinted panels."""
    img = Image.new("RGB", (384, 256), (252, 252, 252))
    draw = ImageDraw.Draw(img)

    # Top half: black text on near-white
    sizes = [8, 10, 12, 16, 20, 24]
    y = 8
    for size in sizes:
        font = ImageFont.load_default(size=size)
        draw.text((12, y), f"{size}px: Quick fox 0123456789 ABCxyz", fill=(15, 15, 18), font=font)
        y += max(size + 5, 14)

    # Bottom-left: white text on deep navy
    draw.rectangle([0, 140, 191, 255], fill=(22, 34, 66))
    y = 148
    for size in (8, 10, 12, 16):
        font = ImageFont.load_default(size=size)
        draw.text((10, y), f"Inv {size}px 9876", fill=(248, 248, 250), font=font)
        y += size + 8

    # Bottom-right: coloured text on pastel cream
    draw.rectangle([192, 140, 383, 255], fill=(246, 238, 224))
    samples = [
        (10, (210, 35, 45), "Red label 10px"),
        (12, (25, 125, 55), "Green label 12px"),
        (14, (35, 80, 205), "Blue label 14px"),
        (16, (150, 75, 25), "Warm ink 16px"),
    ]
    y = 148
    for size, color, text in samples:
        font = ImageFont.load_default(size=size)
        draw.text((202, y), text, fill=color, font=font)
        y += size + 9

    return img


def build_line_art() -> Image.Image:
    """Create vector-style line art with crisp ink lines, flat fills, and gradients."""
    w, h = 384, 256
    arr = np.zeros((h, w, 3), dtype=np.uint8)

    # Sky vertical gradient in top-left quadrant
    t_y = np.linspace(0.0, 1.0, 128)[:, None]
    sky = (1.0 - t_y) * np.array([70, 155, 235]) + t_y * np.array([220, 238, 252])
    arr[:128, :192] = np.clip(np.rint(sky[:, None, :]), 0, 255).astype(np.uint8)

    # Warm radial gradient in top-right quadrant
    yy, xx = np.mgrid[0:128, 192:w]
    dist = np.hypot(xx - 288.0, yy - 64.0) / 72.0
    t_r = np.clip(dist, 0.0, 1.0)[..., None]
    sphere = (1.0 - t_r) * np.array([250, 210, 95]) + t_r * np.array([205, 65, 55])
    arr[:128, 192:w] = np.clip(np.rint(sphere), 0, 255).astype(np.uint8)

    # Flat illustration fills in bottom half
    arr[128:, :128] = (242, 238, 230)
    arr[128:, 128:256] = (180, 225, 200)
    arr[128:, 256:] = (240, 190, 200)

    img = Image.fromarray(arr, mode="RGB")
    draw = ImageDraw.Draw(img)

    # Crisp ink outlines & geometric illustration shapes
    draw.rectangle([1, 1, w - 2, h - 2], outline=(16, 16, 20), width=2)
    draw.line([(0, 128), (w, 128)], fill=(16, 16, 20), width=2)
    draw.line([(192, 0), (192, 128)], fill=(16, 16, 20), width=2)

    # Sun & diagonal rays in top-left
    draw.ellipse([24, 20, 74, 70], fill=(245, 210, 45), outline=(16, 16, 20), width=2)
    for dx, dy in ((84, 45), (78, 72), (56, 86), (92, 22)):
        draw.line([(50, 45), (dx, dy)], fill=(20, 20, 24), width=1)

    # Concentric crisp circles & diagonal lines in top-right
    for r, width in ((46, 2), (30, 1), (14, 1)):
        draw.ellipse([288 - r, 64 - r, 288 + r, 64 + r], outline=(16, 16, 20), width=width)

    # Flat cel-shaded emblem + thin crosshatching in bottom-left
    draw.polygon([(24, 230), (64, 150), (104, 230)], fill=(225, 50, 50), outline=(16, 16, 20))
    for offset in range(-20, 24, 6):
        draw.line([(36 + offset, 235), (68 + offset, 195)], fill=(20, 20, 24), width=1)

    # Fine screentone-like dot grid in bottom-middle
    for gy in range(148, 240, 6):
        for gx in range(144, 240, 6):
            draw.point((gx, gy), fill=(24, 30, 40))
    draw.rounded_rectangle([152, 156, 232, 228], radius=12, outline=(16, 16, 20), width=2)

    # Bold + 1px diagonal hatching and coloured badge in bottom-right
    draw.ellipse([280, 152, 360, 232], fill=(45, 95, 225), outline=(16, 16, 20), width=3)
    draw.ellipse([300, 172, 340, 212], fill=(250, 250, 250), outline=(16, 16, 20), width=1)

    return img


def generate_fixtures(output_dir: Path, jpeg_quality: int = 75) -> dict[str, int]:
    """Generate PNG + JPEG(q=75) illustration test fixtures and manifest."""
    output_dir.mkdir(parents=True, exist_ok=True)
    qr_image, qr_manifest = build_qr_sheet()

    images: dict[str, Image.Image] = {
        "colour_patches": build_colour_patches(),
        "qr_modules": qr_image,
        "small_text": build_small_text(),
        "line_art": build_line_art(),
    }

    sizes: dict[str, int] = {}
    for stem, img in images.items():
        png_path = output_dir / f"{stem}.png"
        jpg_path = output_dir / f"{stem}_q{jpeg_quality}.jpg"
        img.save(png_path, format="PNG", optimize=True)
        img.save(jpg_path, format="JPEG", quality=jpeg_quality)
        sizes[png_path.name] = png_path.stat().st_size
        sizes[jpg_path.name] = jpg_path.stat().st_size

    manifest = {
        "colour_patches": [{"name": name, "rgb": list(rgb)} for name, rgb in COLOUR_PATCHES],
        "qr": qr_manifest,
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    sizes[manifest_path.name] = manifest_path.stat().st_size
    return sizes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    sizes = generate_fixtures(args.output)
    total = sum(sizes.values())
    for name, size in sorted(sizes.items()):
        print(f"{name}: {size:,} bytes")
    print(f"Total: {total:,} bytes ({total / 1024:.1f} KiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
