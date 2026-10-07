from __future__ import annotations

import importlib.util
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "packaging" / "macos" / "installer"


def load_script(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


dmg = load_script(ROOT / "scripts" / "make_macos_dmg.py")
background = load_script(INSTALLER / "build_background.py")


def accent_box(image: Image.Image, scale: int) -> tuple[float, float, float, float]:
    """Bounding box, in points, of the clearly terracotta pixels."""
    rgb = image.convert("RGB")
    xs, ys = [], []
    for y in range(rgb.height):
        for x in range(rgb.width):
            r, g, b = rgb.getpixel((x, y))
            if r - g > 60 and r - b > 60:
                xs.append(x)
                ys.append(y)
    assert xs, "no arrow in the background"
    return min(xs) / scale, min(ys) / scale, (max(xs) + 1) / scale, (max(ys) + 1) / scale


def test_committed_backgrounds_have_the_window_size() -> None:
    width, height = dmg.BACKGROUND_SIZE
    for name, scale in (("background.png", 1), ("background@2x.png", 2)):
        with Image.open(INSTALLER / name) as image:
            assert image.size == (width * scale, height * scale)


def test_background_covers_the_window() -> None:
    assert dmg.BACKGROUND_SIZE[0] >= dmg.WINDOW_SIZE[0]
    assert dmg.BACKGROUND_SIZE[1] >= dmg.WINDOW_SIZE[1]


def test_arrow_sits_between_the_icons_on_their_row() -> None:
    half = dmg.ICON_SIZE / 2
    app_right = dmg.APP_ICON_CENTER[0] + half
    applications_left = dmg.APPLICATIONS_ICON_CENTER[0] - half
    row = dmg.APP_ICON_CENTER[1]
    assert dmg.APPLICATIONS_ICON_CENTER[1] == row
    for name, scale in (("background.png", 1), ("background@2x.png", 2)):
        with Image.open(INSTALLER / name) as image:
            left, top, right, bottom = accent_box(image, scale)
        assert app_right + 10 < left < right < applications_left - 10, name
        assert abs((top + bottom) / 2 - row) <= 1, name
        assert abs((left + right) / 2 - (app_right + applications_left) / 2) <= 6, name


def test_fresh_render_matches_the_layout() -> None:
    left, top, right, bottom = accent_box(background.render(1), 1)
    assert abs((top + bottom) / 2 - dmg.APP_ICON_CENTER[1]) <= 1
    assert dmg.APP_ICON_CENTER[0] < left < right < dmg.APPLICATIONS_ICON_CENTER[0]


def test_settings_place_both_icons(tmp_path: Path) -> None:
    app = tmp_path / "LocalSR.app"
    settings = dmg.settings(app)
    assert settings["files"] == [str(app)]
    assert settings["symlinks"] == {"Applications": "/Applications"}
    assert settings["icon_locations"] == {
        "LocalSR.app": dmg.APP_ICON_CENTER,
        "Applications": dmg.APPLICATIONS_ICON_CENTER,
    }
    assert Path(settings["background"]).is_file()
    assert Path(settings["icon"]).is_file()
    assert settings["window_rect"][1] == dmg.WINDOW_SIZE
