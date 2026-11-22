import os
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
        assert kwargs.get("options") == {"protocol_whitelist": "file"}
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


def test_windows_path_search_excludes_current_and_relative_entries(tmp_path, monkeypatch):
    cwd = tmp_path / "current"
    safe = tmp_path / "safe"
    cwd.mkdir()
    safe.mkdir()
    monkeypatch.chdir(cwd)
    (cwd / "ffmpeg.exe").touch()
    assert (
        external_ffmpeg._windows_path_ffmpeg(os.pathsep.join(["", ".", str(cwd)]), str(cwd)) is None
    )
    (safe / "ffmpeg.exe").touch()
    assert external_ffmpeg._windows_path_ffmpeg(
        os.pathsep.join([str(cwd), str(safe)]), str(cwd)
    ) == str(safe / "ffmpeg.exe")


def test_sips_output_must_be_tiff(tmp_path, monkeypatch):
    source = tmp_path / "input.heic"
    source.touch()

    def run(argv, **kwargs):
        Image.new("RGB", (1, 1)).save(argv[-1], format="PNG")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(image_io.subprocess, "run", run)
    with pytest.raises(OSError):
        image_io._decode_with_macos(str(source))
