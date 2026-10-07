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
