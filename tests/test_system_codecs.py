"""The system-codec route: macOS decodes and encodes H.264/HEVC/AAC through localsr-media."""

from __future__ import annotations

import shutil
import struct
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
import pytest
from test_hdr import make_hdr
from test_video_timing import make_vfr, run_standard, timestamps

from localsr.core import media_bridge, system_codecs
from localsr.core.media_bridge import MediaBridge, decodable_source, load_media_bridge
from localsr.core.system_codecs import (
    FRAME_HEADER,
    STREAM_TAG,
    SystemCodecs,
    SystemCodecsError,
    frame_from_payload,
    payload_from_frame,
    rotate_planar,
)
from localsr.core.video_io import (
    decode_timed_frames,
    encode_video,
    probe_video,
    probe_video_preview,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def helper() -> SystemCodecs:
    """The real helper, built on demand; the tests need macOS and its Swift compiler."""
    if sys.platform != "darwin":
        pytest.skip("the system-codec helper exists on macOS only")
    if shutil.which("swiftc") is None:
        pytest.skip("needs swiftc to build localsr-media")
    sys.path.insert(0, str(ROOT / "scripts"))
    from build_media_helper import build

    build()
    found = system_codecs.detect_system_codecs(refresh=True)
    assert found is not None, "localsr-media was built but did not identify itself"
    return found


@pytest.fixture
def bridge(helper) -> MediaBridge:
    return MediaBridge(system=helper)


def encoder_or_skip(error: SystemCodecsError) -> None:
    if "cannot encode" in str(error):
        pytest.skip(f"this Mac has no usable encoder: {error}")
    raise error


def source_frames(path: Path):
    return [frame for frame in decode_timed_frames(str(path))]


def test_payloads_pack_and_unpack_rows_including_odd_sizes():
    rgb = np.random.default_rng(1).integers(0, 255, (33, 35, 3), dtype=np.uint8)
    frame = av.VideoFrame.from_ndarray(rgb, format="rgb24")
    payload = payload_from_frame(frame, "nv12")
    assert len(payload) == 35 * 33 + 36 * 17
    rebuilt = frame_from_payload(payload, 35, 33, "nv12")
    assert rebuilt.format.name == "nv12" and (rebuilt.width, rebuilt.height) == (35, 33)
    assert payload_from_frame(rebuilt, "nv12") == payload
    ten_bit = frame.reformat(format="yuv420p10le")
    payload = payload_from_frame(ten_bit, "p010le")
    assert len(payload) == (35 * 33 + 36 * 17) * 2
    back = frame_from_payload(payload, 35, 33, "p010le")
    assert payload_from_frame(back, "p010le") == payload
    with pytest.raises(SystemCodecsError, match="expected"):
        frame_from_payload(payload[:-1], 35, 33, "p010le")


def test_rotation_matches_the_display_matrix_rule():
    rgb = np.zeros((8, 12, 3), dtype=np.uint8)
    rgb[:4, :6] = 200
    frame = av.VideoFrame.from_ndarray(rgb, format="rgb24").reformat(format="yuv420p")
    rotated = rotate_planar(frame, 1, False)
    assert (rotated.width, rotated.height) == (8, 12)
    luma = rotated.to_ndarray()[:12]
    assert luma[8:, :4].mean() > 150 and luma[:8, 4:].mean() < 40
    mirrored = rotate_planar(frame, 0, True)
    assert mirrored.to_ndarray()[:8][:4, 6:].mean() > 150
    assert rotate_planar(frame, 0, False) is frame


def test_quality_scale_and_encoder_names():
    assert system_codecs._quality(0) == 1.0
    assert 0.6 < system_codecs._quality(18) < 0.75
    assert system_codecs._quality(51) == pytest.approx(0.1)
    helper = SystemCodecs("/nonexistent", 1)
    assert helper.encoder_for("h264") == "h264_videotoolbox"
    assert helper.encoder_for("hevc", hdr=True) == "hevc_videotoolbox"
    with pytest.raises(SystemCodecsError, match="MP4"):
        helper.encoder_for("h264", container="mkv")
    with pytest.raises(SystemCodecsError, match="HEVC"):
        helper.encoder_for("h264", hdr=True)


def test_bridge_prefers_the_system_and_falls_back_to_the_user(monkeypatch, tmp_path):
    calls = []

    class FakeSystem(SystemCodecs):
        def readable(self, path, *, audio_only=False):
            calls.append(("readable", Path(path).name, audio_only))
            return Path(path).name.startswith("phone")

    class FakeFFmpeg:
        path = "/usr/bin/ffmpeg"
        version = "7"

        def encoder_for(self, codec, *, hdr=False):
            return "libx264"

    system = FakeSystem("/fake", 1)
    both = MediaBridge(system=system, user=FakeFFmpeg())  # type: ignore[arg-type]
    assert both.source_provider(str(tmp_path / "phone.mp4")) is system
    assert isinstance(both.source_provider(str(tmp_path / "old.wmv")), FakeFFmpeg)
    assert both.export_provider("h264", container="mp4") is system
    assert isinstance(both.export_provider("h264", container="mkv"), FakeFFmpeg)
    assert both.converter_label(str(tmp_path / "phone.mp4")) == "macOS"
    assert both.converter_label(str(tmp_path / "old.wmv")) == "your FFmpeg"
    alone = MediaBridge(system=system)
    with pytest.raises(media_bridge.ExternalFFmpegError, match="MP4"):
        alone.export_provider("hevc", container="mkv")
    assert alone.source_provider(str(tmp_path / "old.wmv")) is None
    assert "macOS" in media_bridge.undecodable_message(alone)
    with pytest.raises(ValueError, match="MP4"):
        media_bridge.validate_export(alone, "hevc", "mkv")
    media_bridge.validate_export(alone, "av1", "mkv")
    with pytest.raises(ValueError, match="FFmpeg installed on this computer"):
        media_bridge.validate_export(None, "h264", "mp4")


def test_capabilities_and_bridge_loading(monkeypatch):
    fake = SystemCodecs("/fake", 1)
    monkeypatch.setattr(media_bridge, "detect_system_codecs", lambda: fake)
    monkeypatch.setattr(media_bridge, "implicit_user_ffmpeg", lambda: None)
    report = media_bridge.media_capabilities()
    assert report["system_codecs"]["label"] == "macOS"
    assert "h264" in report["system_codecs"]["encode"]
    assert report["external_ffmpeg"]["detected"] == []
    loaded = load_media_bridge(None)
    assert loaded is not None and loaded.system is fake and loaded.user is None
    monkeypatch.setattr(media_bridge, "detect_system_codecs", lambda: None)
    assert load_media_bridge(None) is None


def test_helper_identifies_itself(helper):
    result = subprocess.run([helper.path, "version"], capture_output=True, text=True, check=True)
    assert f'"stream":"{STREAM_TAG}"'.replace("/", "\\/") in result.stdout.replace(" ", "")
    assert helper.name == "macOS system codecs"


def test_h264_round_trip_keeps_timing_pixels_and_audio(tmp_path, bridge):
    source = make_vfr(tmp_path / "source.mp4")
    frames = source_frames(source)
    exported = tmp_path / "phone.mp4"
    stages = []
    try:
        encode_video(
            iter(frames),
            str(exported),
            fps=25,
            width=48,
            height=32,
            video_codec="h264",
            container_format="mp4",
            crf=4,
            external_ffmpeg=bridge,
            stage_callback=stages.append,
        )
    except Exception as error:  # noqa: BLE001
        if isinstance(error.__cause__, SystemCodecsError):
            encoder_or_skip(error.__cause__)
        raise
    assert stages == ["external_encode"]
    info = bridge.system.probe(str(exported))
    assert info["video"]["codec"] == "avc1" and info["video"]["decodable"]
    assert bridge.system.readable(str(exported))

    # The bundled runtime cannot open it; the system codecs can.
    if "h264" not in av.codecs_available:
        with pytest.raises(media_bridge.UndecodableVideoError, match="patent-licensed"):
            probe_video_preview(str(exported))
    probe, jpeg = probe_video_preview(str(exported), external_ffmpeg=bridge)
    assert (probe.width, probe.height, probe.codec) == (48, 32, "external")
    assert probe.frame_count == len(frames) and jpeg.startswith(b"\xff\xd8")

    with decodable_source(str(exported), ffmpeg=bridge, temporary_directory=str(tmp_path)) as (
        frame_path,
        audio_path,
    ):
        assert frame_path == audio_path and frame_path.endswith("source.mkv")
        with av.open(frame_path) as intermediate:
            assert intermediate.streams.video[0].codec_context.name == "ffv1"
            assert not intermediate.streams.audio
        back = source_frames(Path(frame_path))
        assert [float(f.timestamp) for f in back] == pytest.approx(timestamps(source), abs=0.002)
        for original, decoded in zip(frames, back, strict=True):
            assert np.abs(original.rgb.astype(int) - decoded.rgb.astype(int)).mean() < 12
    assert not list(tmp_path.glob("localsr-media-*"))


def test_aac_audio_travels_through_the_system_codecs(tmp_path, bridge):
    source = make_vfr(tmp_path / "source.mp4")
    frames = source_frames(source)
    make_tone(tmp_path / "tone.mkv")
    exported = tmp_path / "with-audio.mp4"
    try:
        encode_video(
            iter(frames),
            str(exported),
            fps=25,
            width=48,
            height=32,
            video_codec="h264",
            container_format="mp4",
            audio_source=str(tmp_path / "tone.mkv"),
            external_ffmpeg=bridge,
        )
    except Exception as error:  # noqa: BLE001
        if isinstance(error.__cause__, SystemCodecsError):
            encoder_or_skip(error.__cause__)
        raise
    info = bridge.system.probe(str(exported))
    assert info["audio"]["codec"] == "aac" and info["audio"]["channels"] == 1
    with decodable_source(str(exported), ffmpeg=bridge, temporary_directory=str(tmp_path)) as (
        frame_path,
        audio_path,
    ):
        with av.open(audio_path) as intermediate:
            audio = intermediate.streams.audio[0]
            assert audio.codec_context.name == "flac" and audio.rate == 48000
            samples = sum(frame.samples for frame in intermediate.decode(audio))
        # Two seconds of tone under 2.28 seconds of video.
        assert 1.8 * 48000 < samples <= 2.4 * 48000
    output = tmp_path / "enhanced.mp4"
    run_video = run_standard(
        exported, output, external_ffmpeg=bridge, temporary_directory=str(tmp_path)
    )
    assert run_video.frames_processed == len(frames)
    with av.open(str(output)) as result:
        assert result.streams.video[0].codec_context.name == "libdav1d"
        assert result.streams.audio[0].codec_context.name == "flac"


def make_tone(path: Path) -> Path:
    """A two-second 440 Hz mono track in a lossless container the runtime can read."""
    with av.open(str(path), "w", format="matroska") as container:
        stream = container.add_stream("flac", rate=48000)
        stream.codec_context.layout = "mono"
        samples = (np.sin(np.arange(96000) * 2 * np.pi * 440 / 48000) * 12000).astype(np.int16)
        for start in range(0, len(samples), 4096):
            frame = av.AudioFrame.from_ndarray(
                samples[None, start : start + 4096], format="s16", layout="mono"
            )
            frame.sample_rate = 48000
            frame.pts = start
            frame.time_base = Fraction(1, 48000)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return path


def test_rotated_recordings_come_out_in_display_orientation(tmp_path, bridge):
    source = make_vfr(tmp_path / "source.mp4")
    frames = source_frames(source)
    rotated = tmp_path / "portrait.mp4"
    stream = [payload_from_frame(frame_for(frame), "nv12") for frame in frames]
    header = {
        "stream": STREAM_TAG,
        "width": 48,
        "height": 32,
        "format": "nv12",
        "timescale": 1000,
        "full_range": False,
        "color": {"primaries": "bt709", "transfer": "bt709", "matrix": "bt709"},
        "fps": 25.0,
    }
    import json

    payload = json.dumps(header).encode() + b"\n"
    for frame, packed in zip(frames, stream, strict=True):
        payload += FRAME_HEADER.pack(int(frame.timestamp * 1000), 0, len(packed)) + packed
    result = subprocess.run(
        [
            bridge.system.path,
            "encode",
            "--output",
            str(rotated),
            "--codec",
            "h264",
            "--rotation",
            "90",
        ],
        input=payload,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0 and b"cannot encode" in result.stderr:
        pytest.skip(result.stderr.decode())
    assert result.returncode == 0, result.stderr.decode()
    info = bridge.system.probe(str(rotated))
    assert info["video"]["rotation"] == 90 and info["video"]["transform"] == [0, 1, -1, 0]

    probe = probe_video(str(rotated), bridge, str(tmp_path))
    assert (probe.width, probe.height) == (32, 48)
    with decodable_source(str(rotated), ffmpeg=bridge, temporary_directory=str(tmp_path)) as (
        frame_path,
        _,
    ):
        decoded = source_frames(Path(frame_path))
    assert decoded[0].rgb.shape == (48, 32, 3)
    # FFmpeg's own reading of the same file agrees on the orientation.
    user = shutil.which("ffmpeg")
    if user:
        with decodable_source(
            str(rotated),
            ffmpeg=media_bridge.user_ffmpeg.load_external_ffmpeg(user),
            temporary_directory=str(tmp_path),
        ) as (reference_path, _):
            expected = source_frames(Path(reference_path))
        assert expected[0].rgb.shape == decoded[0].rgb.shape
        assert np.abs(expected[0].rgb.astype(int) - decoded[0].rgb.astype(int)).mean() < 6


def frame_for(timed) -> av.VideoFrame:
    return av.VideoFrame.from_ndarray(timed.rgb, format="rgb24")


def test_hlg_hevc_round_trip_keeps_ten_bit_tags(tmp_path, bridge):
    if shutil.which("ffmpeg") is None:
        pytest.skip("needs ffmpeg to build the HLG source")
    source = make_hdr(tmp_path / "hlg.mp4", 18)
    frames = list(decode_timed_frames(str(source), hdr_mode="preserve"))
    exported = tmp_path / "hlg-hevc.mp4"
    try:
        encode_video(
            iter(frames),
            str(exported),
            fps=25,
            width=48,
            height=32,
            video_codec="hevc",
            container_format="mp4",
            hdr_format="HLG",
            external_ffmpeg=bridge,
        )
    except Exception as error:  # noqa: BLE001
        if isinstance(error.__cause__, SystemCodecsError):
            encoder_or_skip(error.__cause__)
        raise
    info = bridge.system.probe(str(exported))
    assert info["video"]["codec"] == "hvc1"
    assert info["video"]["hdr"] == "HLG" and info["video"]["bit_depth"] == 10
    assert probe_video(str(exported), bridge, str(tmp_path)).hdr_format == "HLG"
    with decodable_source(str(exported), ffmpeg=bridge, temporary_directory=str(tmp_path)) as (
        frame_path,
        _,
    ):
        with av.open(frame_path) as intermediate:
            context = intermediate.streams.video[0].codec_context
            assert context.pix_fmt == "yuv420p10le"
            assert (
                int(context.color_primaries),
                int(context.color_trc),
                int(context.colorspace),
            ) == (9, 18, 9)
        back = list(decode_timed_frames(frame_path, hdr_mode="preserve"))
    assert len(back) == len(frames)


def test_undecodable_container_reports_what_was_tried(tmp_path, bridge):
    bogus = tmp_path / "old.wmv"
    bogus.write_bytes(b"\x30\x26\xb2\x75" + bytes(2048))
    assert not bridge.system.readable(str(bogus))
    with pytest.raises(media_bridge.UndecodableVideoError, match="neither LocalSR nor macOS"):
        probe_video_preview(str(bogus), external_ffmpeg=bridge)


def test_cancellation_stops_a_conversion(tmp_path, bridge):
    import threading

    source = make_vfr(tmp_path / "source.mp4")
    exported = tmp_path / "phone.mp4"
    try:
        encode_video(
            iter(source_frames(source)),
            str(exported),
            fps=25,
            width=48,
            height=32,
            video_codec="h264",
            container_format="mp4",
            external_ffmpeg=bridge,
        )
    except Exception as error:  # noqa: BLE001
        if isinstance(error.__cause__, SystemCodecsError):
            encoder_or_skip(error.__cause__)
        raise
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(InterruptedError):
        with decodable_source(
            str(exported), ffmpeg=bridge, temporary_directory=str(tmp_path), cancel_event=cancelled
        ):
            pass
    assert not list(tmp_path.glob("localsr-media-*"))


def test_frame_header_layout_is_stable():
    assert FRAME_HEADER.size == 16
    assert FRAME_HEADER.pack(-5, 7, 9) == struct.pack("<qiI", -5, 7, 9)
