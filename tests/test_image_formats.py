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
