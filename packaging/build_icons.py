import platform
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "packaging" / "icons"


def _font(size: int):
    for name in ("DejaVuSans-Bold.ttf", "Arial Bold.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def render_icon(size: int) -> Image.Image:
    scale = size / 1024
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    inset = round(48 * scale)
    radius = round(230 * scale)
    gradient = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gradient_draw = ImageDraw.Draw(gradient)
    for y in range(inset, size - inset):
        t = (y - inset) / max(1, size - inset * 2)
        color = (
            round(117 - 45 * t),
            round(91 - 38 * t),
            round(246 - 32 * t),
            255,
        )
        gradient_draw.line((inset, y, size - inset, y), fill=color)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (inset, inset, size - inset, size - inset), radius=radius, fill=255
    )
    gradient.putalpha(mask)
    image.alpha_composite(gradient)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (inset, inset, size - inset, size - inset),
        radius=radius,
        outline=(196, 186, 255, 180),
        width=max(1, round(14 * scale)),
    )
    font = _font(round(300 * scale))
    text = "SR"
    box = draw.textbbox((0, 0), text, font=font)
    x = (size - (box[2] - box[0])) / 2
    y = (size - (box[3] - box[1])) / 2 - box[1] - round(8 * scale)
    draw.text((x, y), text, font=font, fill=(247, 249, 255, 255))
    return image


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    master = render_icon(1024)
    master.save(OUTPUT / "LocalSR.png", optimize=True)
    master.save(
        OUTPUT / "LocalSR.ico",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )

    if platform.system() == "Darwin":
        iconset = OUTPUT / "LocalSR.iconset"
        iconset.mkdir(exist_ok=True)
        for logical in (16, 32, 128, 256, 512):
            master.resize((logical, logical), Image.Resampling.LANCZOS).save(
                iconset / f"icon_{logical}x{logical}.png"
            )
            doubled = logical * 2
            master.resize((doubled, doubled), Image.Resampling.LANCZOS).save(
                iconset / f"icon_{logical}x{logical}@2x.png"
            )
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
