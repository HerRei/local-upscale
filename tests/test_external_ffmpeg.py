"""The licensing boundary: bundled royalty-free codecs and an optional user-installed FFmpeg."""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from pathlib import Path

import av
import numpy as np
import pytest
from test_hdr import make_hdr
from test_video_timing import make_vfr, run_standard, timestamps

from localsr.core import media_codecs
from localsr.core.external_ffmpeg import (
    ExternalFFmpegError,
    decodable_source,
    load_external_ffmpeg,
    plan_source,
)
from localsr.core.video_io import (
    VideoStageError,
    decode_timed_frames,
    encode_video,
    probe_video,
    probe_video_preview,
)
from localsr.core.video_playback import prepare_playback


@pytest.fixture
def user_ffmpeg():
    binary = shutil.which("ffmpeg")
    if not binary:
        pytest.skip("needs a user-installed ffmpeg")
    return load_external_ffmpeg(binary)


@pytest.fixture
def clean_runtime():
    """Refusal tests need the licensing-clean runtime, not PyPI's PyAV."""
    if "h264" in av.codecs_available:
        pytest.skip(
            "this environment uses PyPI's PyAV; run packaging/ffmpeg/install_media_runtime.py"
        )


def ffprobe_streams(path: Path) -> list[dict]:
    binary = shutil.which("ffprobe")
    if not binary:
        pytest.skip("needs a user-installed ffprobe to inspect H.264/HEVC output")
    result = subprocess.run(
        [binary, "-v", "error", "-show_streams", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)["streams"]


def make_h264_aac(path: Path, user_ffmpeg, frames: int = 12) -> Path:
    subprocess.run(
        [
            user_ffmpeg.path,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size=64x48:rate=12,trim=end_frame={frames}",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=1",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
    )
    return path


def test_bundled_runtime_contains_only_policy_codecs():
    policy = json.loads(
        (Path(__file__).resolve().parents[1] / "packaging/ffmpeg/codec-policy.json").read_text()
    )
    if "libx264" in av.codecs_available:
        pytest.skip("this environment uses a development PyAV with GPL codecs")
    names = {
        policy["runtime_codec_names"].get(n, n) for n in policy["decoders"] + policy["encoders"]
    }
    assert set(av.codecs_available) <= names
    for forbidden in ("h264", "hevc", "aac", "libx264", "libx265", "mpeg4", "wmv2"):
        assert forbidden not in av.codecs_available


@pytest.mark.parametrize(
    "codec,container,hdr,message",
    [
        ("ffv1", "mp4", False, "MKV"),
        ("h264", "mp4", True, "cannot preserve HDR"),
        ("x264", "mp4", False, "Unsupported video codec"),
        ("av1", "avi", False, "Unsupported video container"),
        ("vp9", "avi", False, "Unsupported video container"),
    ],
)
def test_output_validation_rejects_unsupported_combinations(codec, container, hdr, message):
    with pytest.raises(ValueError, match=message):
        media_codecs.validate_output(codec, container, hdr=hdr)


@pytest.mark.parametrize("codec,container", [("av1", "mp4"), ("vp9", "mkv"), ("ffv1", "mkv")])
def test_bundled_exports_round_trip_with_timing(tmp_path, codec, container):
    source = make_vfr(tmp_path / "source.mp4")
    output = tmp_path / f"out.{container}"
    run_standard(source, output, video_codec=codec, container=container)
    assert timestamps(output) == pytest.approx(timestamps(source), abs=0.002)
    expected = {"av1": "libdav1d", "vp9": "vp9", "ffv1": "ffv1"}[codec]
    with av.open(str(output)) as result:
        assert result.streams.video[0].codec_context.name == expected


def test_probe_failure_message_carries_the_ffmpeg_reason_only_when_set():
    from localsr.protocol.messages import MediaProbeFailed

    plain = json.loads(MediaProbeFailed("a.mp4", "Truncated file").to_json())
    assert plain["data"] == {"media_path": "a.mp4", "error_message": "Truncated file"}
    needs = json.loads(
        MediaProbeFailed(
            "b.mp4",
            media_codecs.UNDECODABLE_VIDEO_MESSAGE,
            reason=media_codecs.EXTERNAL_FFMPEG_REQUIRED,
        ).to_json()
    )
    assert needs["data"]["reason"] == "external_ffmpeg_required"


def test_h264_source_needs_user_ffmpeg_then_converts_losslessly(
    tmp_path, user_ffmpeg, clean_runtime
):
    source = make_h264_aac(tmp_path / "phone.mp4", user_ffmpeg)
    assert plan_source(str(source)) == plan_source(str(source), "mp4")
    assert not plan_source(str(source)).video_decodable
    with pytest.raises(
        media_codecs.UndecodableVideoError, match="patent-licensed format"
    ) as refused:
        probe_video_preview(str(source))
    assert refused.value.reason == media_codecs.EXTERNAL_FFMPEG_REQUIRED
    with pytest.raises(ValueError, match="patent-licensed format"):
        run_standard(source, tmp_path / "refused.mp4")
    assert not (tmp_path / "refused.mp4").exists()

    probe, jpeg = probe_video_preview(str(source), external_ffmpeg=user_ffmpeg)
    assert (probe.width, probe.height, probe.codec) == (64, 48, "external")
    assert probe.frame_count == 12 and jpeg.startswith(b"\xff\xd8")

    output = tmp_path / "enhanced.mp4"
    run_video = run_standard(
        source,
        output,
        external_ffmpeg=user_ffmpeg,
        temporary_directory=str(tmp_path),
    )
    assert run_video.frames_processed == 12
    assert not list(tmp_path.glob("localsr-external-*"))
    with av.open(str(output)) as result:
        assert result.streams.video[0].codec_context.name == "libdav1d"
        assert result.streams.audio[0].codec_context.name == "flac"
        assert len(list(result.decode(audio=0))) > 0
    assert len(timestamps(output)) == 12


@pytest.mark.parametrize("codec,container", [("h264", "mp4"), ("hevc", "mkv")])
def test_h264_and_hevc_exports_are_written_by_user_ffmpeg(tmp_path, user_ffmpeg, codec, container):
    source = make_vfr(tmp_path / "source.mp4")
    frames = list(decode_timed_frames(str(source)))
    output = tmp_path / f"export.{container}"
    with pytest.raises(VideoStageError, match="FFmpeg installed on this computer"):
        encode_video(
            iter(frames),
            str(output),
            fps=25,
            width=48,
            height=32,
            video_codec=codec,
            container_format=container,
        )
    stages = []
    encode_video(
        iter(frames),
        str(output),
        fps=25,
        width=48,
        height=32,
        video_codec=codec,
        container_format=container,
        external_ffmpeg=user_ffmpeg,
        stage_callback=stages.append,
    )
    assert stages == ["external_encode"]
    assert not list(tmp_path.glob(".export.*.tmp"))
    video = next(stream for stream in ffprobe_streams(output) if stream["codec_type"] == "video")
    assert video["codec_name"] == codec
    assert int(video["nb_frames"] if "nb_frames" in video else len(frames)) == len(frames)


def test_hevc_hdr_source_and_export_through_user_ffmpeg(tmp_path, user_ffmpeg, clean_runtime):
    source = make_hdr(tmp_path / "camera.mp4", 18, codec="libx265", audio="aac")
    with pytest.raises(ValueError, match="patent-licensed format"):
        probe_video(str(source))
    assert probe_video(str(source), user_ffmpeg, str(tmp_path)).hdr_format == "HLG"
    with decodable_source(str(source), ffmpeg=user_ffmpeg, temporary_directory=str(tmp_path)) as (
        frames_path,
        _audio,
    ):
        frames = list(decode_timed_frames(frames_path, 0, 2, hdr_mode="preserve"))
    output = tmp_path / "hdr.mp4"
    encode_video(
        iter(frames),
        str(output),
        fps=25,
        width=48,
        height=32,
        video_codec="hevc",
        hdr_format="HLG",
        external_ffmpeg=user_ffmpeg,
    )
    video = next(stream for stream in ffprobe_streams(output) if stream["codec_type"] == "video")
    assert (video["codec_name"], video["pix_fmt"], video["color_transfer"]) == (
        "hevc",
        "yuv420p10le",
        "arib-std-b67",
    )


def test_playback_copy_of_h264_source_is_royalty_free_webm(tmp_path, user_ffmpeg, clean_runtime):
    source = make_h264_aac(tmp_path / "phone.mp4", user_ffmpeg)
    output = tmp_path / "preview.webm"
    with pytest.raises(ValueError, match="patent-licensed format"):
        prepare_playback(source, output)
    events = []
    prepare_playback(source, output, progress=events.append, external_ffmpeg=user_ffmpeg)
    assert events[0]["stage"] == "Converting the source video"
    with av.open(str(output)) as result:
        assert result.format.name.startswith("matroska")
        assert result.streams.video[0].codec_context.name == "vp9"
        assert result.streams.audio[0].codec_context.name == "opus"
    assert len(list(decode_timed_frames(str(output)))) == 12


def test_user_ffmpeg_selection_is_validated(tmp_path, monkeypatch):
    monkeypatch.delenv("LOCALSR_EXTERNAL_FFMPEG", raising=False)
    assert load_external_ffmpeg("") is None
    with pytest.raises(ExternalFFmpegError, match="not an executable"):
        load_external_ffmpeg(str(tmp_path / "missing"))
    impostor = tmp_path / "ffmpeg"
    impostor.write_text("#!/bin/sh\necho hello\n")
    impostor.chmod(0o755)
    with pytest.raises(ExternalFFmpegError, match="does not identify as FFmpeg"):
        load_external_ffmpeg(str(impostor))


def test_cancelled_conversion_leaves_no_intermediate(tmp_path, user_ffmpeg, clean_runtime):
    source = make_h264_aac(tmp_path / "phone.mp4", user_ffmpeg, frames=240)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(InterruptedError):
        with decodable_source(
            str(source),
            ffmpeg=user_ffmpeg,
            temporary_directory=str(tmp_path),
            cancel_event=cancel,
        ):
            pass
    assert not list(tmp_path.glob("localsr-external-*"))


def test_audio_that_cannot_be_copied_becomes_opus(tmp_path):
    source = tmp_path / "pcm.mkv"
    with av.open(str(source), "w") as container:
        video = container.add_stream("ffv1", rate=10)
        video.width, video.height, video.pix_fmt = 32, 24, "yuv420p"
        audio = container.add_stream("pcm_s16le", rate=48000)
        audio.layout = "stereo"
        for index in range(10):
            frame = av.VideoFrame.from_ndarray(np.full((24, 32, 3), index, np.uint8), "rgb24")
            for packet in video.encode(frame):
                container.mux(packet)
            samples = np.zeros((1, 9600), np.int16)
            sound = av.AudioFrame.from_ndarray(samples, format="s16", layout="stereo")
            sound.sample_rate = 48000
            for packet in audio.encode(sound):
                container.mux(packet)
        for stream in (video, audio):
            for packet in stream.encode():
                container.mux(packet)
    output = tmp_path / "out.mp4"
    messages = []
    encode_video(
        decode_timed_frames(str(source)),
        str(output),
        fps=10,
        width=32,
        height=24,
        audio_source=str(source),
        warning_callback=messages.append,
    )
    assert any("converted to Opus" in message for message in messages)
    with av.open(str(output)) as result:
        assert result.streams.audio[0].codec_context.name == "opus"
