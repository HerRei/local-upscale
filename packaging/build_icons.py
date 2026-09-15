import platform
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "packaging" / "icons"


# Brand colours shared with the website (herrei.github.io/localsr): paper, ink
# and the terracotta of the "LocalSR." dot.
PAPER_TOP = (247, 245, 238, 255)
PAPER_BOTTOM = (233, 230, 219, 255)
INK = (36, 39, 32, 255)
ACCENT = (179, 61, 38, 255)
SUPERSAMPLE = 4


def render_icon(size: int, compact: bool = False) -> Image.Image:
    """Crop marks around one terracotta pixel: "a little more in every pixel".

    ``compact`` fills more of the canvas with heavier marks for favicons and the
    16-32 px sizes, where the standard macOS icon margin would make it unreadable.
    """
    canvas = size * SUPERSAMPLE
    unit = canvas / 1024

    def u(value: float) -> int:
        return round(value * unit)

    inset, radius = (u(40), u(210)) if compact else (u(100), u(185))
    frame_start, frame_end = (u(236), u(788)) if compact else (u(300), u(724))
    arm, stroke = (u(170), u(84)) if compact else (u(118), u(46))
    pixel_size, pixel_center = (u(190), u(560)) if compact else (u(112), u(566))

    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    body = (inset, inset, canvas - inset, canvas - inset)
    if not compact:
        shadow = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
        ImageDraw.Draw(shadow).rounded_rectangle(
            (body[0], body[1] + u(14), body[2], body[3] + u(14)), radius=radius, fill=(0, 0, 0, 70)
        )
        image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(u(22))))

    paper = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    paper_draw = ImageDraw.Draw(paper)
    for y in range(inset, canvas - inset):
        t = (y - inset) / max(1, canvas - inset * 2)
        paper_draw.line(
            (inset, y, canvas - inset, y),
            fill=tuple(round(a + (b - a) * t) for a, b in zip(PAPER_TOP, PAPER_BOTTOM, strict=True)),
        )
    mask = Image.new("L", (canvas, canvas), 0)
    ImageDraw.Draw(mask).rounded_rectangle(body, radius=radius, fill=255)
    paper.putalpha(mask)
    image.alpha_composite(paper)

    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(body, radius=radius, outline=(36, 39, 32, 26), width=max(1, u(4)))
    half = stroke / 2
    for x, dx in ((frame_start, 1), (frame_end, -1)):
        for y, dy in ((frame_start, 1), (frame_end, -1)):
            # One L-shaped crop mark per corner, drawn as two square-capped bars.
            horizontal = sorted((x - dx * half, x + dx * arm))
            vertical = sorted((y - dy * half, y + dy * arm))
            draw.rectangle((horizontal[0], y - half, horizontal[1], y + half), fill=INK)
            draw.rectangle((x - half, vertical[0], x + half, vertical[1]), fill=INK)
    pixel_half = pixel_size / 2
    draw.rounded_rectangle(
        (
            pixel_center - pixel_half,
            pixel_center - pixel_half,
            pixel_center + pixel_half,
            pixel_center + pixel_half,
        ),
        radius=round(pixel_size * 0.16),
        fill=ACCENT,
    )
    return image.resize((size, size), Image.Resampling.LANCZOS)


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    master = render_icon(1024)
    master.save(OUTPUT / "LocalSR.png", optimize=True)
    compact = render_icon(256, compact=True)
    compact.save(
        OUTPUT / "LocalSR.ico",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    for favicon in (32, 64, 180):
        render_icon(favicon, compact=True).save(OUTPUT / f"favicon-{favicon}.png", optimize=True)

    if platform.system() == "Darwin":
        iconset = OUTPUT / "LocalSR.iconset"
        iconset.mkdir(exist_ok=True)
        for logical in (16, 32, 128, 256, 512):
            for scale, suffix in ((1, ""), (2, "@2x")):
                pixels = logical * scale
                variant = render_icon(pixels, compact=pixels <= 64)
                variant.save(iconset / f"icon_{logical}x{logical}{suffix}.png")
        subprocess.run(
            [
                "iconutil",
                "--convert",
                "icns",
                str(iconset),
                "--output",
                str(OUTPUT / "LocalSR.icns"),
            ],
            check=True,
        )


if __name__ == "__main__":
    main()
