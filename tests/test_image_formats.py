from localsr.core import image_formats


def test_raw_and_video_inputs_are_recognized_case_insensitively():
    assert image_formats.is_raw_input("portrait.DNG")
    assert not image_formats.is_raw_input("portrait.tif")
    assert image_formats.is_video_input("clip.MKV")
    assert not image_formats.is_video_input("portrait.dng")
