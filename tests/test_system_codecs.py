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
from localsr.core.external_ffmpeg import ExternalFFmpegError
from localsr.core.media_bridge import MediaBridge, decodable_source, load_media_bridge
from localsr.core.media_codecs import audio_copy_allowed, muxer_format, validate_output
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


def clean_runtime_only(codec: str) -> None:
    """The conversion route exists for the licensing-clean runtime, not PyPI's PyAV."""
    if codec in av.codecs_available:
        pytest.skip(
            f"PyPI's PyAV decodes {codec} itself; run packaging/ffmpeg/install_media_runtime.py"
        )


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
    clean_runtime_only("h264")
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


def test_whole_clips_stream_straight_into_the_system_encoder(tmp_path, bridge):
    source = make_vfr(tmp_path / "source.mp4")
    frames = source_frames(source)
    make_tone(tmp_path / "tone.mkv")
    exported = tmp_path / "streamed.mp4"
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
            audio_source=str(tmp_path / "tone.mkv"),
            external_ffmpeg=bridge,
            stage_callback=stages.append,
            stream_to_system=True,
        )
    except SystemCodecsError as error:
        encoder_or_skip(error)
    # No lossless master and no separate encoding stage: frames went straight in.
    assert stages == []
    info = bridge.system.probe(str(exported))
    assert info["video"]["codec"] == "avc1" and info["audio"]["codec"] == "aac"
    with decodable_source(str(exported), ffmpeg=bridge, temporary_directory=str(tmp_path)) as (
        frame_path,
        _audio_path,
    ):
        back = source_frames(Path(frame_path))
        assert [float(f.timestamp) for f in back] == pytest.approx(timestamps(source), abs=0.002)
        for original, decoded in zip(frames, back, strict=True):
            assert np.abs(original.rgb.astype(int) - decoded.rgb.astype(int)).mean() < 12
    assert not list(tmp_path.glob("localsr-media-*"))
    assert not list(tmp_path.glob("*.external*"))


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
    clean_runtime_only("aac")
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
            width=64,
            height=64,
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

    clean_runtime_only("h264")
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


def test_encode_frames_sends_scheduled_durations(monkeypatch, tmp_path):
    import io
    from contextlib import contextmanager
    from types import SimpleNamespace

    from localsr.core.video_io import TimedVideoFrame

    class Capture(io.BytesIO):
        def close(self):
            pass

    output = Capture()
    commands = []
    process = SimpleNamespace(stdin=output, returncode=0)

    @contextmanager
    def fake_process(self, arguments, *, stdin=False):
        commands.append(arguments)
        yield process

    monkeypatch.setattr(SystemCodecs, "_process", fake_process)
    monkeypatch.setattr(SystemCodecs, "_wait", lambda *args: None)
    rgb = np.zeros((4, 4, 3), dtype=np.uint8)
    frames = [
        TimedVideoFrame(0, rgb, Fraction(0), Fraction(1, 96), Fraction(1, 96)),
        TimedVideoFrame(1, rgb, Fraction(1, 96), Fraction(1, 24), Fraction(1, 96)),
    ]
    SystemCodecs("helper", 1).encode_frames(
        frames, tmp_path / "out.mp4", fps=96, video_codec="hevc", container="mp4", crf=18
    )
    assert "--no-reordering" in commands[0]
    data = output.getvalue().split(b"\n", 1)[1]
    pts, duration, length = FRAME_HEADER.unpack_from(data)
    assert (pts, duration) == (0, 1)
    pts, duration, _ = FRAME_HEADER.unpack_from(data, FRAME_HEADER.size + length)
    assert (pts, duration) == (1, 4)


def test_mov_codec_validation():
    from localsr.core.media_codecs import validate_output

    for codec in ("hevc", "prores"):
        validate_output(codec, "mov")
    for codec, container in (("h264", "mov"), ("av1", "mov"), ("prores", "mp4")):
        with pytest.raises(ValueError):
            validate_output(codec, container)
    # A source whose audio only the system decodes is planned against the output container.
    assert muxer_format("mov") == "mov" and audio_copy_allowed("aac", "mov")
    helper = SystemCodecs("/nonexistent", 1)
    assert helper.encoder_for("prores", container="mov")
    with pytest.raises(SystemCodecsError):
        helper.encoder_for("prores", container="mp4")


def prores_or_skip(
    helper: SystemCodecs, width: int = 48, height: int = 32, fps: float = 25
) -> None:
    if not helper.can_encode("prores", width, height, fps=fps):
        pytest.skip(
            f"this Mac (a virtual machine, perhaps) cannot encode Apple ProRes at {width}x{height}"
        )


@pytest.mark.parametrize("codec,fourcc", [("hevc", "hvc1"), ("prores", "apcn")])
def test_mov_system_export_with_audio(tmp_path, bridge, codec, fourcc):
    if codec == "prores":
        prores_or_skip(bridge.system)
    source = make_vfr(tmp_path / "source.mp4")
    frames = source_frames(source)
    tone = tmp_path / "tone.mkv"
    make_tone(tone)
    output = tmp_path / f"{codec}.mov"
    encode_video(
        iter(frames),
        str(output),
        fps=25,
        width=48,
        height=32,
        video_codec=codec,
        container_format="mov",
        audio_source=str(tone),
        external_ffmpeg=bridge,
        stream_to_system=True,
    )
    info = bridge.system.probe(str(output))
    assert info["video"]["codec"] == fourcc
    assert info["audio"]["codec"] == "aac"
    expected_duration = float(frames[-1].timestamp + frames[-1].duration - frames[0].timestamp)
    assert info["video"]["duration"] == pytest.approx(expected_duration, abs=0.05)
    with decodable_source(str(output), ffmpeg=bridge, temporary_directory=str(tmp_path)) as (
        frame_path,
        _,
    ):
        assert len(source_frames(Path(frame_path))) == len(frames)


def test_mov_and_prores_exports_need_the_system_codecs(monkeypatch):
    class FakeFFmpeg:
        path = "/usr/bin/ffmpeg"
        version = "7"

        def encoder_for(self, codec, *, hdr=False):
            return "libx265"

    user_only = MediaBridge(user=FakeFFmpeg())
    for codec in ("hevc", "prores"):
        with pytest.raises(ValueError, match="codecs of macOS"):
            media_bridge.validate_export(user_only, codec, "mov")
        with pytest.raises(ValueError, match="codecs of macOS"):
            media_bridge.validate_export(None, codec, "mov")
    with pytest.raises(ValueError, match="codecs of macOS"):
        media_bridge.validate_export(user_only, "prores", "mp4")
    with pytest.raises(ExternalFFmpegError, match="codecs of macOS"):
        user_only.export_provider("hevc", container="mov")
    both = MediaBridge(system=SystemCodecs("/nonexistent", 1), user=FakeFFmpeg())
    media_bridge.validate_export(both, "prores", "mov")
    media_bridge.validate_export(both, "hevc", "mov")
    assert isinstance(both.export_provider("hevc", container="mov"), SystemCodecs)
    # An FFmpeg the user installed still writes HEVC into MKV, never ProRes or MOV.
    assert both.export_provider("hevc", container="mkv") is both.user
    with pytest.raises(ValueError, match="SDR"):
        validate_output("prores", "mov", hdr=True)
    fake = SystemCodecs("/fake", 1)
    monkeypatch.setattr(media_bridge, "detect_system_codecs", lambda: fake)
    monkeypatch.setattr(media_bridge, "implicit_user_ffmpeg", lambda: None)
    report = media_bridge.media_capabilities()["system_codecs"]
    assert "prores" in report["encode"] and "mov" in report["containers"]


@pytest.mark.parametrize("available", [True, False])
def test_prores_is_offered_only_when_the_system_can_encode_it(monkeypatch, available):
    fake = SystemCodecs("/fake-prores-probe", 1)
    monkeypatch.setattr(system_codecs, "_encode_probe", {})
    monkeypatch.setattr(fake.__class__, "_run", lambda self, args, timeout: {"prores": available})
    assert fake.can_encode("prores", 48, 32) is available
    assert fake.can_encode("prores") is available
    monkeypatch.setattr(media_bridge, "detect_system_codecs", lambda: fake)
    monkeypatch.setattr(media_bridge, "implicit_user_ffmpeg", lambda: None)
    encode = media_bridge.media_capabilities()["system_codecs"]["encode"]
    assert ("prores" in encode) is available
    assert {"h264", "hevc", "aac"} <= set(encode)


def test_mov_export_through_the_lossless_master(tmp_path, bridge):
    """Odd sizes cannot stream to the encoder: the FFV1 master route writes MOV as well."""
    prores_or_skip(bridge.system)
    source = make_vfr(tmp_path / "source.mp4")
    frames = source_frames(source)
    output = tmp_path / "master.mov"
    encode_video(
        iter(frames),
        str(output),
        fps=25,
        width=48,
        height=32,
        video_codec="prores",
        container_format="mov",
        external_ffmpeg=bridge,
        stream_to_system=False,
    )
    info = bridge.system.probe(str(output))
    assert info["video"]["codec"] == "apcn"
    assert output.read_bytes()[4:12] == b"ftypqt  "


class _SidecarSystem:
    """A stand-in helper that, like macOS, leaves a `.sb-` sibling of its output."""

    def __init__(self, fail=False):
        self.fail = fail

    def source_audio(self, *_args):
        return None

    def encode_frames(self, frames, final, **_options):
        final.write_bytes(b"video")
        final.with_name(final.name + ".sb-df5fe11b-AbCdEf").write_bytes(b"x" * 64)
        if self.fail:
            raise InterruptedError("video job cancelled")


@pytest.mark.parametrize("fail", [False, True])
def test_system_export_leaves_nothing_but_the_output(tmp_path, fail):
    from localsr.core.video_io import _system_stream_export

    keep = tmp_path / ".unrelated.sb-keep"
    keep.write_bytes(b"mine")
    destination = tmp_path / "out.mp4"
    call = {
        "system": _SidecarSystem(fail),
        "video_codec": "hevc",
        "container_format": "mp4",
        "crf": 18,
        "fps": 24.0,
        "audio_source": None,
        "cancel_event": None,
        "temporary_directory": None,
    }
    if fail:
        with pytest.raises(InterruptedError):
            _system_stream_export(iter(()), destination, **call)
    else:
        _system_stream_export(iter(()), destination, **call)
    expected = {keep.name} | (set() if fail else {destination.name})
    assert {path.name for path in tmp_path.iterdir()} == expected


@pytest.mark.parametrize(
    ("codec", "container"),
    # .mov cannot carry Opus: a kept part of a QuickTime export gets PCM (bug hunt 11-23).
    [("hevc", "mp4"), ("hevc", "mov"), ("prores", "mov")],
)
def test_stopped_streamed_system_export_keeps_playable_video_and_trimmed_audio(
    tmp_path, bridge, codec, container
):
    import threading

    from test_video_timing import ffmpeg

    video = make_vfr(tmp_path / "video.mp4")
    source = tmp_path / "source.mp4"
    ffmpeg(
        "-i",
        video,
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=800:duration=3",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-shortest",
        source,
    )
    frames = source_frames(source)

    def stopped_frames():
        yield from frames[:3]
        raise InterruptedError("stopped")

    output = encode_video(
        stopped_frames(),
        str(tmp_path / f"output.{container}"),
        fps=25,
        width=frames[0].rgb.shape[1],
        height=frames[0].rgb.shape[0],
        video_codec=codec,
        container_format=container,
        external_ffmpeg=bridge,
        stream_to_system=True,
        audio_source=str(source),
        cancel_event=threading.Event(),
        keep_partial=True,
    )
    assert "_partial_" in output
    with av.open(output) as container:
        assert len(container.streams.audio) == 1
        video_stream = container.streams.video[0]
        audio_stream = container.streams.audio[0]
        assert (
            abs(
                float(audio_stream.duration * audio_stream.time_base)
                - float(video_stream.duration * video_stream.time_base)
            )
            < 0.06
        )
        assert len(list(container.decode(video=0))) == 3


def force_system_route(monkeypatch) -> None:
    """Route H.264/HEVC through the system codecs as the shipped runtime does, also where
    PyPI's PyAV could decode it itself."""
    from localsr.core.external_ffmpeg import SourcePlan

    monkeypatch.setattr(
        media_bridge, "plan_source", lambda *args, **kwargs: SourcePlan(False, True)
    )


def timed(frames) -> list:
    return [
        (
            f.index,
            f.timestamp,
            f.duration,
            f.time_base,
            f.rgb.dtype.str,
            f.rgb.shape,
            f.rgb.tobytes(),
        )
        for f in frames
    ]


def audio_packets(path: str) -> list:
    with av.open(path) as container:
        if not container.streams.audio:
            return []
        stream = container.streams.audio[0]
        return [(packet.pts, bytes(packet)) for packet in container.demux(stream) if packet.size]


def system_export(bridge, destination: Path, frames, **options) -> Path:
    try:
        encode_video(iter(frames), str(destination), external_ffmpeg=bridge, **options)
    except Exception as error:  # noqa: BLE001
        cause = error if isinstance(error, SystemCodecsError) else error.__cause__
        if isinstance(cause, SystemCodecsError):
            encoder_or_skip(cause)
        raise
    return destination


def streamed_sources(tmp_path, bridge, kind: str) -> tuple[Path, str]:
    """A source only the system decodes, and the HDR mode to read it with."""
    if kind == "hlg":
        if shutil.which("ffmpeg") is None:
            pytest.skip("needs ffmpeg to build the HLG source")
        frames = list(
            decode_timed_frames(str(make_hdr(tmp_path / "hlg.mp4", 18)), hdr_mode="preserve")
        )
        options = {"width": 64, "height": 64, "video_codec": "hevc", "hdr_format": "HLG"}
        return system_export(
            bridge, tmp_path / "hlg-hevc.mp4", frames, fps=25, **options
        ), "preserve"
    frames = source_frames(make_vfr(tmp_path / "source.mp4"))
    tone = make_tone(tmp_path / "tone.mkv")
    if kind == "vfr":
        exported = system_export(
            bridge,
            tmp_path / "phone.mp4",
            frames,
            fps=25,
            width=48,
            height=32,
            video_codec="h264",
            container_format="mp4",
            audio_source=str(tone),
        )
        return exported, "reject"
    import json

    header = {
        "stream": STREAM_TAG,
        "width": 48,
        "height": 32,
        "format": "nv12",
        # 90 kHz ticks whose milliseconds round up and down, as phone clips have.
        "timescale": 90000,
        "full_range": False,
        "color": {"primaries": "bt709", "transfer": "bt709", "matrix": "bt709"},
        "fps": 25.0,
    }
    payload = json.dumps(header).encode() + b"\n"
    for index, frame in enumerate(frames):
        packed = payload_from_frame(frame_for(frame), "nv12")
        payload += FRAME_HEADER.pack(
            int(frame.timestamp * 90000) + 45 * (index % 2), 0, len(packed)
        )
        payload += packed
    rotated = tmp_path / "portrait.mp4"
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
    return rotated, "reject"


@pytest.mark.parametrize("kind", ["vfr", "rotated", "hlg"])
def test_streamed_source_matches_the_lossless_copy_frame_for_frame(
    tmp_path, bridge, monkeypatch, kind
):
    from localsr.core.video_io import StreamedVideo, count_video_frames

    source, hdr_mode = streamed_sources(tmp_path, bridge, kind)
    force_system_route(monkeypatch)
    trims = [(None, None), (2, 5), (0, 0)]
    work = tmp_path / "work"
    work.mkdir()
    with decodable_source(str(source), ffmpeg=bridge, temporary_directory=str(work)) as (
        copy,
        copy_audio,
    ):
        assert isinstance(copy, str)
        expected_probe = probe_video(copy)
        expected = {
            trim: timed(decode_timed_frames(copy, *trim, hdr_mode=hdr_mode)) for trim in trims
        }
        expected_audio = audio_packets(copy_audio)
    with decodable_source(
        str(source), ffmpeg=bridge, temporary_directory=str(work), stream_video=True
    ) as (streamed, audio):
        assert isinstance(streamed, StreamedVideo)
        # Only the first frame is copied; the rest comes from the helper as it decodes.
        assert count_video_frames(audio) == 1
        assert [path.name for path in Path(audio).parent.iterdir()] == ["source.mkv"]
        probe = probe_video(streamed)
        # Estimates for progress: the copy ends one nominal frame after its last frame.
        assert probe.frame_count == pytest.approx(expected_probe.frame_count, abs=1)
        assert probe.duration_seconds == pytest.approx(
            expected_probe.duration_seconds, abs=1 / expected_probe.fps
        )
        same = ("frame_count", "duration_seconds")
        assert {k: v for k, v in vars(probe).items() if k not in same} == {
            k: v for k, v in vars(expected_probe).items() if k not in same
        }
        for trim in trims:
            assert timed(decode_timed_frames(streamed, *trim, hdr_mode=hdr_mode)) == expected[trim]
        assert audio_packets(audio) == expected_audio
    assert expected[(None, None)]
    assert bool(expected_audio) == (kind == "vfr")
    assert not list(work.iterdir())


def _large_phone_clip(tmp_path, bridge) -> Path:
    """Frames far larger than a pipe's buffer, so the helper cannot run ahead and finish."""
    frames = source_frames(
        make_vfr(tmp_path / "large.mp4", times=tuple(range(0, 480, 40)), width=640, height=360)
    )
    return system_export(
        bridge,
        tmp_path / "large-phone.mp4",
        frames,
        fps=25,
        width=640,
        height=360,
        video_codec="h264",
        container_format="mp4",
    )


def _started_processes(monkeypatch) -> list:
    started = []
    original = system_codecs.start_child

    def record(*args, **kwargs):
        process = original(*args, **kwargs)
        started.append(process)
        return process

    monkeypatch.setattr(system_codecs, "start_child", record)
    return started


def test_streamed_frames_arrive_while_the_helper_still_decodes(tmp_path, bridge, monkeypatch):
    source = _large_phone_clip(tmp_path, bridge)
    force_system_route(monkeypatch)
    started = _started_processes(monkeypatch)
    with decodable_source(
        str(source), ffmpeg=bridge, temporary_directory=str(tmp_path), stream_video=True
    ) as (streamed, _audio):
        started.clear()
        frames = decode_timed_frames(streamed)
        first = next(frames)
        (decoder,) = started
        assert first.index == 0 and decoder.poll() is None
        assert len(list(frames)) == 11
        assert decoder.returncode == 0
    assert not list(tmp_path.glob("localsr-media-*"))


def test_cancelling_a_streamed_source_stops_its_helper(tmp_path, bridge, monkeypatch):
    import threading
    import time

    source = _large_phone_clip(tmp_path, bridge)
    force_system_route(monkeypatch)
    started = _started_processes(monkeypatch)
    cancel = threading.Event()
    with pytest.raises(InterruptedError):
        with decodable_source(
            str(source),
            ffmpeg=bridge,
            temporary_directory=str(tmp_path),
            cancel_event=cancel,
            stream_video=True,
        ) as (streamed, _audio):
            started.clear()
            frames = decode_timed_frames(streamed, cancel_event=cancel)
            next(frames)
            (decoder,) = started
            assert decoder.poll() is None
            # The helper stops on the cancel itself, while the job is not reading.
            cancel.set()
            deadline = time.monotonic() + 5
            while decoder.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            assert decoder.poll() is not None
            next(frames)
    assert not list(tmp_path.glob("localsr-media-*"))


def test_video_jobs_read_a_system_decoded_source_as_it_decodes(tmp_path, bridge, monkeypatch):
    """A whole job gives the same video and audio whether it streams or reads a copy."""
    import threading
    from types import SimpleNamespace

    from test_video_timing import IdentityEngine

    from localsr.core.video_pipeline import VideoJobConfig, run_video_job

    source, _ = streamed_sources(tmp_path, bridge, "vfr")
    force_system_route(monkeypatch)
    reads = []
    streaming = media_bridge.decodable_source

    def copy_only(*args, **kwargs):
        kwargs["stream_video"] = False
        return streaming(*args, **kwargs)

    original = SystemCodecs.decoded_frames

    def counted(self, *args, **kwargs):
        reads.append(mode)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(SystemCodecs, "decoded_frames", counted)
    outputs = {}
    for mode in ("copy", "stream"):
        monkeypatch.setattr(
            media_bridge, "decodable_source", copy_only if mode == "copy" else streaming
        )
        stages = []
        output = tmp_path / f"{mode}.mkv"
        result = run_video_job(
            VideoJobConfig(
                str(source),
                "test",
                str(output),
                SimpleNamespace(scale=1),
                "cpu",
                "fp32",
                64,
                4,
                False,
                container="mkv",
                video_codec="ffv1",
                external_ffmpeg=bridge,
                temporary_directory=str(tmp_path),
            ),
            IdentityEngine(),
            threading.Event(),
            stage_cb=lambda stage, *_, seen=stages: seen.append(stage),
        )
        assert result.output_path == str(output) and "external_decode" in stages
        with av.open(str(output)) as container:
            video = [
                (frame.pts, frame.to_ndarray().tobytes())
                for frame in container.decode(container.streams.video[0])
            ]
        outputs[mode] = (video, audio_packets(str(output)))
    assert reads == ["stream"]
    assert outputs["stream"] == outputs["copy"]
    assert len(outputs["stream"][0]) == 10 and outputs["stream"][1]
    assert not list(tmp_path.glob("localsr-media-*"))


def test_timestamps_are_rounded_as_the_muxer_rounds_them(tmp_path):
    from localsr.core.system_codecs import _rescale

    for timescale, stamps in (
        (90000, [0, 45, 135, 3003, 6006, 9054, 12010, 90045]),
        (600, [0, 1, 20, 41, 61, 599]),
        (30000, [0, 1001, 2002, 3003, 4004, 30030]),
    ):
        path = tmp_path / f"{timescale}.mkv"
        with av.open(str(path), "w", format="matroska") as container:
            stream = container.add_stream("ffv1", rate=25)
            stream.width, stream.height, stream.pix_fmt = 16, 16, "yuv420p"
            stream.codec_context.time_base = Fraction(1, timescale)
            for stamp in stamps:
                frame = av.VideoFrame(16, 16, "yuv420p")
                frame.pts, frame.time_base = stamp, Fraction(1, timescale)
                for packet in stream.encode(frame):
                    container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)
        with av.open(str(path)) as container:
            stored = [frame.pts for frame in container.decode(video=0)]
            base = container.streams.video[0].time_base
        assert stored == [_rescale(stamp, Fraction(1, timescale), base) for stamp in stamps]
    assert _rescale(-45, Fraction(1, 90000), Fraction(1, 1000)) == -1


def _fake_decoder(monkeypatch, stdout: bytes, returncode: int, errors: str = ""):
    import io
    from contextlib import contextmanager
    from types import SimpleNamespace

    process = SimpleNamespace(
        stdout=io.BytesIO(stdout),
        returncode=returncode,
        error_log=io.StringIO(errors),
        poll=lambda: returncode,
        terminate=lambda: None,
    )

    @contextmanager
    def fake_process(self, arguments, *, stdin=False, cancel_event=None):
        yield process

    monkeypatch.setattr(SystemCodecs, "_process", fake_process)
    monkeypatch.setattr(SystemCodecs, "_wait", staticmethod(lambda *args: None))


def _stream_bytes(frames: int, *, cut: bool = False) -> bytes:
    import json

    header = {"stream": STREAM_TAG, "video": True, "width": 4, "height": 2, "format": "nv12"}
    data = json.dumps({**header, "timescale": 90000, "transform": [1, 0, 0, 1]}).encode() + b"\n"
    payload = bytes(4 * 2 + 4 * 1)
    for index in range(frames):
        data += FRAME_HEADER.pack(index * 3003, 0, len(payload)) + payload
    return data + (FRAME_HEADER.pack(9, 0, len(payload)) + payload[:3] if cut else b"")


def _template() -> av.VideoFrame:
    template = av.VideoFrame(4, 2, "yuv420p")
    template.pts, template.time_base, template.duration = 0, Fraction(1, 1000), 33
    template.color_range, template.colorspace = 1, 1
    return template


def test_streamed_frames_take_the_intermediate_timing(monkeypatch):
    _fake_decoder(monkeypatch, _stream_bytes(3), 0)
    frames = list(SystemCodecs("helper", 1).decoded_frames("clip.mov", _template()))
    assert [frame.pts for frame in frames] == [0, 33, 67]
    assert {(frame.time_base, frame.duration, frame.format.name) for frame in frames} == {
        (Fraction(1, 1000), 33, "yuv420p")
    }
    assert {(int(frame.color_range), int(frame.colorspace)) for frame in frames} == {(1, 1)}


@pytest.mark.parametrize("cut", [False, True])
def test_a_source_that_stops_decoding_part_way_is_not_an_encoder_failure(monkeypatch, cut):
    from localsr.core.video_codec_recovery import automatic_export, system_export_failure
    from localsr.core.video_io import SourceDecodeError

    _fake_decoder(monkeypatch, _stream_bytes(2, cut=cut), 0 if cut else 1, "video decode failed")
    decoded = SystemCodecs("helper", 1).decoded_frames("clip.mov", _template())
    frames = (frame.to_ndarray(format="rgb24") for frame in decoded)
    exports = []

    def export(replay, codec):
        exports.append(codec)
        for _frame in replay:
            pass
        return "published"

    with pytest.raises(SourceDecodeError, match="decode") as raised:
        automatic_export(
            frames,
            "out.mp4",
            export=export,
            cancel_event=None,
            temporary_directory=None,
            note_callback=None,
            warning_callback=None,
        )
    # No second encoder publishes the frames that arrived before the failure.
    assert exports == ["hevc"]
    assert not isinstance(raised.value, SystemCodecsError)
    assert raised.value.__context__ is None
    wrapped = SourceDecodeError("decode", "stopped")
    wrapped.__context__ = SystemCodecsError("helper failed")
    assert not system_export_failure(wrapped)
    assert system_export_failure(SystemCodecsError("encoder failed"))


def test_temporal_jobs_read_a_system_decoded_source_as_it_decodes(tmp_path, bridge, monkeypatch):
    import threading
    from types import SimpleNamespace

    from test_video_timing import IdentityEngine

    from localsr.worker.server import WorkerServer

    source, _ = streamed_sources(tmp_path, bridge, "vfr")
    force_system_route(monkeypatch)
    monkeypatch.setattr("localsr.worker.server.send_message", lambda _: None)
    streaming = media_bridge.decodable_source
    reads = []
    original = SystemCodecs.decoded_frames

    def counted(self, *args, **kwargs):
        reads.append(mode)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(SystemCodecs, "decoded_frames", counted)
    outputs = {}
    for mode in ("copy", "stream"):

        def copy_only(*args, **kwargs):
            kwargs["stream_video"] = False
            return streaming(*args, **kwargs)

        monkeypatch.setattr(
            media_bridge, "decodable_source", copy_only if mode == "copy" else streaming
        )
        engine = IdentityEngine()
        engine.max_temporal_window = 5
        output = tmp_path / f"temporal-{mode}.mkv"
        WorkerServer._run_temporal_video_job(
            SimpleNamespace(cancel_event=threading.Event(), _emit_live_preview=lambda _: None),
            f"temporal-{mode}",
            {
                "video_path": str(source),
                "output_video_path": str(output),
                "container": "mkv",
                "video_codec": "ffv1",
                "temporary_directory": str(tmp_path),
            },
            lambda *_, ready=engine: ready,
        )
        with av.open(str(output)) as container:
            video = [
                (frame.pts, frame.to_ndarray().tobytes())
                for frame in container.decode(container.streams.video[0])
            ]
        outputs[mode] = (video, audio_packets(str(output)))
    assert reads == ["stream"]
    assert outputs["stream"] == outputs["copy"]
    assert len(outputs["stream"][0]) == 10 and outputs["stream"][1]
    assert not list(tmp_path.glob("localsr-media-*"))
