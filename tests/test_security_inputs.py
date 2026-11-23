import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import av
import pytest
from PIL import Image, UnidentifiedImageError

from localsr.core import external_ffmpeg, image_io, video_io
from localsr.worker.result_preview import open_result_image


def test_pillow_rejects_undocumented_ppm_in_input_and_result(tmp_path):
    path = tmp_path / "picture.ppm"
    Image.new("RGB", (2, 2)).save(path)
    for opener in (image_io.open_image, open_result_image):
        with pytest.raises(UnidentifiedImageError):
            opener(path)


def test_raw_pixel_budget_checked_before_postprocess(monkeypatch):
    class Raw:
        sizes = SimpleNamespace(width=image_io.MAX_INPUT_PIXELS, height=2)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def postprocess(self, **kwargs):
            pytest.fail("oversized RAW was developed")

    monkeypatch.setitem(sys.modules, "rawpy", SimpleNamespace(imread=lambda _: Raw()))
    with pytest.raises(ValueError, match="megapixels"):
        image_io._develop_raw("large.raw")


def test_oversized_video_header_rejected_before_decode(monkeypatch):
    stream = SimpleNamespace(
        type="video",
        width=image_io.MAX_INPUT_PIXELS,
        height=2,
        codec_context=SimpleNamespace(),
    )

    class Container:
        streams = [stream]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(video_io.av, "open", lambda *args, **kwargs: Container())
    with pytest.raises(ValueError, match="megapixels"):
        with video_io._video_decoder("large.webm"):
            pytest.fail("oversized video was decoded")


def test_oversized_video_frame_rejected_before_conversion():
    frame = SimpleNamespace(width=image_io.MAX_INPUT_PIXELS, height=2, side_data={})
    stream = SimpleNamespace(sample_aspect_ratio=None)
    with pytest.raises(ValueError, match="megapixels"):
        video_io._frame_rgb(stream, frame, "tone_map")


def test_hls_http_segment_is_refused(tmp_path, monkeypatch):
    playlist = tmp_path / "remote.m3u8"
    playlist.write_text(
        "#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXTINF:1,\nhttp://127.0.0.1:9/segment.ts\n#EXT-X-ENDLIST\n"
    )
    original = av.open

    def guarded_open(path, **kwargs):
        assert kwargs["options"]["protocol_whitelist"] == "file"
        return original(path, **kwargs)

    monkeypatch.setattr(video_io.av, "open", guarded_open)
    with pytest.raises(av.FFmpegError):
        with video_io._video_decoder(str(playlist)):
            pass


def test_sips_gets_absolute_input_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = Path("-input.heic")
    source.write_bytes(b"input")
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        Image.new("RGB", (1, 1)).save(argv[-1], format="TIFF")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(image_io.subprocess, "run", run)
    with image_io._decode_with_macos(str(source)):
        pass
    assert calls[0][4] == str(source.resolve())


def test_windows_path_search_excludes_current_and_relative_entries(monkeypatch):
    import ntpath

    files = {r"c:\current\ffmpeg.exe", r"c:\safe\ffmpeg.exe"}
    monkeypatch.setattr(ntpath, "isfile", lambda path: ntpath.normcase(path) in files)
    assert (
        external_ffmpeg._windows_path_ffmpeg(r";.;C:relative;\rooted;C:\Current", r"C:\Current")
        is None
    )
    assert (
        external_ffmpeg._windows_path_ffmpeg(r"C:\Current;C:\safe", r"C:\Current")
        == r"C:\safe\ffmpeg.exe"
    )


def test_sips_output_must_be_tiff(tmp_path, monkeypatch):
    source = tmp_path / "input.heic"
    source.touch()

    def run(argv, **kwargs):
        Image.new("RGB", (1, 1)).save(argv[-1], format="PNG")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(image_io.subprocess, "run", run)
    with pytest.raises(OSError):
        image_io._decode_with_macos(str(source))


def test_raw_sensor_pixel_budget_checked_before_postprocess(monkeypatch):
    class Raw:
        sizes = SimpleNamespace(
            width=10, height=10, raw_width=image_io.MAX_INPUT_PIXELS, raw_height=2
        )

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def postprocess(self, **kwargs):
            pytest.fail("oversized RAW sensor was developed")

    monkeypatch.setitem(
        sys.modules,
        "rawpy",
        SimpleNamespace(imread=lambda _: Raw(), ColorSpace=SimpleNamespace(sRGB=object())),
    )
    with pytest.raises(ValueError, match="megapixels"):
        image_io._develop_raw("large.raw")


def test_video_probe_passes_native_pixel_limit(monkeypatch):
    def open_checked(path, **kwargs):
        assert kwargs["options"]["max_pixels"] == str(image_io.MAX_INPUT_PIXELS)
        raise av.FFmpegError(1, "stop")

    monkeypatch.setattr(video_io.av, "open", open_checked)
    with pytest.raises(av.FFmpegError):
        with video_io._video_decoder("missing"):
            pass
    with pytest.raises(av.FFmpegError):
        video_io._source_decodable("missing")


def test_system_video_dimensions_checked_before_helper_reader(tmp_path, monkeypatch):
    from localsr.core.system_codecs import SystemCodecs

    helper = SystemCodecs("helper", 1)
    monkeypatch.setattr(
        SystemCodecs,
        "probe",
        lambda self, path: {"video": {"width": image_io.MAX_INPUT_PIXELS, "height": 2}},
    )
    monkeypatch.setattr(
        SystemCodecs, "_process", lambda *args, **kwargs: pytest.fail("helper reader started")
    )
    with pytest.raises(ValueError, match="megapixels"):
        helper.convert_source("large.mov", tmp_path / "output.mkv")


def test_native_decoder_refuses_frames_over_its_pixel_limit(tmp_path, monkeypatch):
    import numpy as np

    path = tmp_path / "video.mkv"
    with av.open(str(path), "w") as output:
        stream = output.add_stream("libvpx-vp9", rate=1)
        stream.width, stream.height, stream.pix_fmt = 64, 48, "yuv420p"
        frame = av.VideoFrame.from_ndarray(np.zeros((48, 64, 3), dtype=np.uint8), format="rgb24")
        for packet in stream.encode(frame):
            output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)
    monkeypatch.setattr(video_io, "MAX_INPUT_PIXELS", 100)
    with pytest.raises(av.FFmpegError):
        with video_io._video_decoder(str(path)) as (container, stream):
            next(container.decode(stream))


def test_windows_path_search_accepts_quoted_expanded_absolute_directories(monkeypatch):
    import ntpath

    monkeypatch.setenv("LOCALSR_TEST_FFMPEG_DIR", r"C:\Tools")
    # The folder does not exist; on Windows the real realpath may change its case.
    monkeypatch.setattr(ntpath, "realpath", lambda path: path)
    monkeypatch.setattr(
        ntpath, "isfile", lambda path: ntpath.normcase(path) == r"c:\tools\ffmpeg.exe"
    )
    assert (
        external_ffmpeg._windows_path_ffmpeg(
            r'"%LOCALSR_TEST_FFMPEG_DIR%";C:relative;\rooted;C:\Current', r"C:\Current"
        )
        == r"C:\Tools\ffmpeg.exe"
    )


def test_windows_path_search_uses_canonical_paths(monkeypatch):
    import ntpath

    def realpath(path):
        return {
            r"C:\alias": r"C:\CURRENT",
            r"C:\tools": r"C:\actual",
            r"C:\actual\ffmpeg.exe": r"C:\actual\real.exe",
        }.get(path, path)

    monkeypatch.setattr(ntpath, "realpath", realpath)
    monkeypatch.setattr(ntpath, "isfile", lambda path: True)
    assert external_ffmpeg._windows_path_ffmpeg(r"C:\alias;C:\tools", r"C:\current") == (
        r"C:\actual\real.exe"
    )


def test_windows_path_search_rejects_incomplete_share_and_device_paths(monkeypatch):
    import ntpath

    monkeypatch.setattr(ntpath, "isfile", lambda path: True)
    for directory in (r"\\server", r"\\.\pipe", r"\\?\C:relative"):
        assert external_ffmpeg._windows_path_ffmpeg(directory, r"C:\current") is None


def test_windows_path_search_accepts_extended_unc_root(monkeypatch):
    import ntpath

    monkeypatch.setattr(ntpath, "isfile", lambda path: True)
    assert (
        external_ffmpeg._windows_path_ffmpeg(r"\\?\UnC\server\share\bin", r"C:\current")
        == r"\\?\UnC\server\share\bin\ffmpeg.exe"
    )
