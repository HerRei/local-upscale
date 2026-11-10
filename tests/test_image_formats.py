import json
import re
import sys
from pathlib import Path

from localsr.core import image_formats


def test_raw_and_video_inputs_are_recognized_case_insensitively():
    assert image_formats.is_raw_input("portrait.DNG")
    assert not image_formats.is_raw_input("portrait.tif")
    assert image_formats.is_video_input("clip.MKV")
    assert not image_formats.is_video_input("portrait.dng")


def test_camera_raw_formats_go_to_libraw():
    for name in ("DSCF0001.RAF", "IMG_0001.CR3", "DSC_0001.NEF", "DSC00001.ARW", "P1000001.RW2"):
        assert image_formats.is_raw_input(name), name
        assert name.lower()[-4:] in image_formats.SUPPORTED_INPUT_EXTENSIONS


def test_hevc_based_formats_are_only_offered_where_the_os_decodes_them():
    import sys

    on_mac = sys.platform == "darwin"
    for name in ("IMG_0001.HEIC", "photo.heif", "DSCF0001.HIF", "scan.jxl"):
        assert image_formats.is_system_decoded(name) == on_mac, name
        assert (
            name.lower()[name.rindex(".") :] in image_formats.SUPPORTED_INPUT_EXTENSIONS
        ) == on_mac
    # Royalty-free formats Pillow already decodes are offered everywhere.
    for extension in (".avif", ".jp2", ".gif", ".bmp"):
        assert extension in image_formats.RASTER_INPUT_EXTENSIONS


def test_heic_output_is_announced_only_where_macos_encodes_it(monkeypatch):
    import sys

    from localsr.core import media_bridge

    monkeypatch.setattr(media_bridge, "detect_system_codecs", lambda: None)
    monkeypatch.setattr(media_bridge, "implicit_user_ffmpeg", lambda: None)
    assert image_formats.system_image_output_formats() == (
        ["heic"] if sys.platform == "darwin" else []
    )
    monkeypatch.setattr(image_formats.sys, "platform", "linux")
    assert image_formats.system_image_output_formats() == []
    assert media_bridge.media_capabilities()["image_output"] == []
    monkeypatch.setattr(image_formats.sys, "platform", "darwin")
    monkeypatch.setattr(image_formats.os, "access", lambda *_: False)
    assert image_formats.system_image_output_formats() == []
    monkeypatch.setattr(image_formats.os, "access", lambda *_: True)
    assert media_bridge.media_capabilities()["image_output"] == ["heic"]


# RAW suffixes the app opens but does not register as file associations: the
# installers claim only the formats common enough to be worth an "Open with" entry.
RAW_WITHOUT_FILE_ASSOCIATION = frozenset(
    {".srf", ".sr2", ".3fr", ".iiq", ".erf", ".rwl", ".mrw", ".mef", ".mos", ".dcr", ".kdc"}
)
# Decoded by macOS only, so the host and the picker list them but the Python
# constant is empty elsewhere.
MACOS_DECODED_IMAGE_EXTENSIONS = frozenset({".heic", ".heif", ".hif", ".jxl"})


def _desktop_file(relative: str) -> str:
    return (Path(__file__).resolve().parents[1] / relative).read_text()


def _rust_extensions(source: str, name: str) -> set[str]:
    # The first declaration is the macOS one for SYSTEM_IMAGE_EXTENSIONS.
    pattern = rf"(?:#\[cfg\(target_os = \"macos\"\)\]\s*)?const {name}:.*?=\s*&\[(.*?)\];"
    body = re.findall(pattern, source, re.S)[0]
    return {"." + value for value in re.findall(r'"([a-z0-9]+)"', body)}


def test_image_extensions_agree_across_python_host_picker_and_associations():
    python_images = set(image_formats.RASTER_INPUT_EXTENSIONS | image_formats.RAW_INPUT_EXTENSIONS)
    rust = _desktop_file("desktop/src-tauri/src/commands.rs")
    assert _rust_extensions(rust, "IMAGE_EXTENSIONS") == python_images
    assert _rust_extensions(rust, "SYSTEM_IMAGE_EXTENSIONS") == MACOS_DECODED_IMAGE_EXTENSIONS
    if sys.platform == "darwin":
        assert image_formats.SYSTEM_IMAGE_EXTENSIONS == MACOS_DECODED_IMAGE_EXTENSIONS

    dialog = _desktop_file("desktop/src/lib/api.ts").split(
        "export async function chooseMediaFolder"
    )[0]
    picker = {
        "." + value
        for value in re.findall(r"'([a-z0-9]+)'", dialog.split("extensions: [")[1].split("]")[0])
    }
    assert (
        picker - image_formats.VIDEO_INPUT_EXTENSIONS
        == python_images | MACOS_DECODED_IMAGE_EXTENSIONS
    )

    config = json.loads(_desktop_file("desktop/src-tauri/tauri.conf.json"))
    registered = {
        "." + ext
        for entry in config["bundle"]["fileAssociations"]
        if entry.get("ext", [""])[0] == "png"
        for ext in entry["ext"]
    }
    assert registered == python_images - RAW_WITHOUT_FILE_ASSOCIATION
    assert RAW_WITHOUT_FILE_ASSOCIATION <= image_formats.RAW_INPUT_EXTENSIONS
