"""Encoder failures must not discard Automatic jobs or their rendered frames."""

import errno
import json
import re
import threading
from contextlib import contextmanager
from fractions import Fraction
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from localsr.core.frame_interpolation import InterpolationTarget, interpolate_frames
from localsr.core.media_bridge import MediaBridge
from localsr.core.system_codecs import FRAME_HEADER, SystemCodecs, SystemCodecsError
from localsr.core.video_io import TimedVideoFrame, encode_video


def pictures(count=4):
    for index in range(count):
        yield TimedVideoFrame(
            index,
            np.full((32, 48, 3), index * 30, dtype=np.uint8),
            Fraction(index, 120),
            Fraction(1, 120),
            Fraction(1, 1500000),
        )


@pytest.mark.parametrize("normalized", [False, True])
def test_owner_fps120_stream_header_and_exact_timestamps(monkeypatch, tmp_path, normalized):
    """Exercise the bytes sent to the helper, rather than just nominal FPS math."""

    class Pipe(BytesIO):
        def close(self):
            pass

    pipe = Pipe()
    process = SimpleNamespace(stdin=pipe, returncode=0)

    @contextmanager
    def fake_process(self, *args, **kwargs):
        yield process

    monkeypatch.setattr(SystemCodecs, "_process", fake_process)
    monkeypatch.setattr(SystemCodecs, "_wait", lambda *args: None)
    target = InterpolationTarget.parse("fps120")
    source_rate = Fraction(500000, 33333)
    time_base = Fraction(1, 1000 if normalized else 500000)
    stamps = [round(Fraction(i, 1) / source_rate / time_base) * time_base for i in range(4)]
    source = [
        TimedVideoFrame(
            i,
            np.zeros((32, 48, 3), dtype=np.uint8),
            stamps[i],
            stamps[i + 1] - stamps[i],
            time_base,
        )
        for i in range(3)
    ]
    frames = list(interpolate_frames(source, target, None, None))
    SystemCodecs("/fake", 1).encode_frames(
        iter(frames),
        tmp_path / "unused.mp4",
        video_codec="hevc",
        container="mp4",
        crf=18,
        fps=target.output_fps(float(source_rate)),
    )
    pipe.seek(0)
    header = json.loads(pipe.readline())
    assert header["fps"] == 120.0
    timescale = 3000 if normalized else 1500000
    assert header["timescale"] == timescale
    records = []
    while raw := pipe.read(FRAME_HEADER.size):
        pts, duration, size = FRAME_HEADER.unpack(raw)
        records.append((pts, duration))
        pipe.read(size)
    assert [pts for pts, _ in records] == [i * (timescale // 120) for i in range(len(frames))]
    assert all(duration > 0 for _, duration in records)
    assert sum(duration for _, duration in records) == stamps[-1] * timescale


class RefusingSystem(SystemCodecs):
    def __init__(self, error, consume):
        super().__init__("/fake", 1)
        self.error, self.consume, self.calls = error, consume, []

    def encode_frames(self, frames, destination, **options):
        self.calls.append(options["video_codec"])
        destination.write_bytes(b"partial")
        destination.with_name(destination.name + ".sb-test").write_bytes(b"sidecar")
        iterator = iter(frames)
        for _ in range(self.consume):
            next(iterator)
        raise self.error

    def transcode_output(self, source, destination, **options):
        self.encode_frames(pictures(), destination, **options)


@pytest.mark.parametrize("consume", [0, 2, 4])
@pytest.mark.parametrize("stream", [True, False])
def test_automatic_retries_av1_keeps_rendered_frames_and_cleans_partials(
    monkeypatch, tmp_path, consume, stream
):
    import localsr.core.video_io as video_io

    original_encode = video_io._encode_video
    replayed = []

    def encode_without_native_av1(frames, destination, *, video_codec, **options):
        if video_codec != "av1":
            return original_encode(frames, destination, video_codec=video_codec, **options)
        replayed.extend(frames)
        Path(destination).write_bytes(b"av1")
        return destination

    monkeypatch.setattr(video_io, "_encode_video", encode_without_native_av1)
    system = RefusingSystem(SystemCodecsError("HEVC encode failed: Cannot Encode Media"), consume)
    notes, rendered = [], []

    def render():
        for frame in pictures():
            rendered.append(frame.index)
            yield frame

    output = tmp_path / "out.mp4"
    encode_video(
        render(),
        str(output),
        fps=120,
        width=48,
        height=32,
        video_codec="auto",
        external_ffmpeg=MediaBridge(system=system),
        stream_to_system=stream,
        temporary_directory=str(tmp_path),
        note_callback=notes.append,
    )
    assert rendered == [0, 1, 2, 3]
    assert system.calls == ["hevc"]  # Existing Automatic order is HEVC, then AV1.
    assert notes == ["HEVC export failed on this Mac; the video was written as AV1 instead."]
    assert [frame.index for frame in replayed] == [0, 1, 2, 3]
    for actual, expected in zip(replayed, pictures(), strict=True):
        np.testing.assert_array_equal(actual.rgb, expected.rgb)
        assert (actual.timestamp, actual.duration, actual.time_base) == (
            expected.timestamp,
            expected.duration,
            expected.time_base,
        )
    assert output.read_bytes() == b"av1"
    assert list(tmp_path.iterdir()) == [output]


@pytest.mark.parametrize("codec", ["h264", "hevc"])
def test_explicit_system_failure_names_a_way_out(tmp_path, codec):
    system = RefusingSystem(SystemCodecsError("Cannot Encode Media"), 2)
    with pytest.raises(Exception, match="Choose Automatic or AV1 under Advanced > Video format"):
        encode_video(
            pictures(),
            str(tmp_path / "out.mp4"),
            fps=120,
            width=48,
            height=32,
            video_codec=codec,
            external_ffmpeg=MediaBridge(system=system),
            stream_to_system=True,
        )
    assert system.calls == [codec]
    assert not list(tmp_path.iterdir())


def test_worker_retains_automatic_intent_after_selecting_hevc(monkeypatch, tmp_path):
    from test_video_interpolation import patched_worker, worker_request

    import localsr.core.media_bridge as media_bridge

    server, _, _, captured = patched_worker(monkeypatch, tmp_path)
    monkeypatch.setattr(
        media_bridge, "load_media_bridge", lambda _: MediaBridge(system=SystemCodecs("/fake", 1))
    )
    server._run_video_job("auto", worker_request(tmp_path, video_codec="auto"))
    assert captured["config"].video_codec == "hevc"
    assert captured["config"].automatic_codec is True


def test_interpolated_pipeline_reports_note_without_running_model_twice(tmp_path):
    from test_video_interpolation import CountingEngine, interpolated_job, make_cfr

    class NoteEngine(CountingEngine):
        def __init__(self):
            super().__init__()
            self.notes = []

        def add_note(self, note):
            self.notes.append(note)

    source = make_cfr(tmp_path / "source.mp4", frames=3, fps=15)
    system = RefusingSystem(SystemCodecsError("Cannot Encode Media"), 2)
    engine = NoteEngine()
    interpolated_job(
        source,
        tmp_path / "output.mp4",
        engine=engine,
        video_codec="auto",
        external_ffmpeg=MediaBridge(system=system),
        interpolation="fps120",
    )
    assert engine.frames == 3
    assert engine.notes == ["HEVC export failed on this Mac; the video was written as AV1 instead."]
    assert {p.name for p in tmp_path.iterdir()} == {"source.mp4", "output.mp4"}


@pytest.mark.parametrize(
    "error",
    [
        InterruptedError("video job cancelled"),
        OSError(errno.ENOSPC, "No space left on device"),
        SystemCodecsError("The disk is full (AVFoundationErrorDomain -11807)"),
        SystemCodecsError("NSCocoaErrorDomain (640): volume full"),
    ],
)
def test_cancellation_and_full_disk_never_retry(tmp_path, error):
    system = RefusingSystem(error, 1)
    notes, rendered = [], []

    def render():
        for frame in pictures():
            rendered.append(frame.index)
            yield frame

    with pytest.raises(type(error), match=re.escape(str(error))):
        encode_video(
            render(),
            str(tmp_path / "out.mp4"),
            fps=120,
            width=48,
            height=32,
            video_codec="auto",
            external_ffmpeg=MediaBridge(system=system),
            stream_to_system=True,
            note_callback=notes.append,
        )
    assert rendered == [0]
    assert system.calls == ["hevc"]
    assert notes == []
    assert not list(tmp_path.iterdir())


def test_automatic_recovers_when_the_helper_cannot_start(tmp_path):
    notes = []
    output = tmp_path / "out.mp4"
    encode_video(
        pictures(),
        str(output),
        fps=120,
        width=48,
        height=32,
        video_codec="auto",
        external_ffmpeg=MediaBridge(system=SystemCodecs(str(tmp_path / "missing-helper"), 1)),
        stream_to_system=True,
        note_callback=notes.append,
    )
    assert output.exists()
    assert len(notes) == 1
    assert list(tmp_path.iterdir()) == [output]


def test_cancel_flag_on_helper_failure_never_retries(tmp_path):
    cancel = threading.Event()

    class CancelSystem(RefusingSystem):
        def encode_frames(self, *args, **kwargs):
            cancel.set()
            return super().encode_frames(*args, **kwargs)

    system = CancelSystem(SystemCodecsError("Cannot Encode Media"), 0)
    with pytest.raises(InterruptedError):
        encode_video(
            pictures(),
            str(tmp_path / "out.mp4"),
            fps=120,
            width=48,
            height=32,
            video_codec="auto",
            external_ffmpeg=MediaBridge(system=system),
            stream_to_system=True,
            cancel_event=cancel,
            temporary_directory=str(tmp_path),
        )
    assert system.calls == ["hevc"]
    assert not list(tmp_path.iterdir())


def test_cache_limit_stops_retry_and_cleans_partials(monkeypatch, tmp_path):
    import localsr.core.video_codec_recovery as recovery

    monkeypatch.setattr(recovery, "REPLAY_LIMIT_BYTES", 1)
    system = RefusingSystem(SystemCodecsError("Cannot Encode Media"), 2)
    with pytest.raises(
        Exception,
        match="The video encoder stopped part-way; run the job again with AV1 under Advanced > Video format",
    ):
        encode_video(
            pictures(),
            str(tmp_path / "out.mp4"),
            fps=120,
            width=48,
            height=32,
            video_codec="auto",
            external_ffmpeg=MediaBridge(system=system),
            stream_to_system=True,
            temporary_directory=str(tmp_path),
        )
    assert system.calls == ["hevc"]
    assert not list(tmp_path.iterdir())


def test_cache_write_error_does_not_stop_export(monkeypatch, tmp_path):
    import localsr.core.video_codec_recovery as recovery

    def fail(*args, **kwargs):
        raise OSError(errno.ENOSPC, "No space left")

    monkeypatch.setattr(recovery.pickle, "dump", fail)
    output = tmp_path / "out.mp4"
    result = recovery.automatic_export(
        pictures(),
        output,
        export=lambda frames, codec: list(frames),
        cancel_event=None,
        temporary_directory=str(tmp_path),
        note_callback=None,
        warning_callback=None,
    )
    assert len(result) == 4
    assert not list(tmp_path.iterdir())


def test_cache_creation_failure_does_not_stop_export(monkeypatch, tmp_path):
    import localsr.core.video_codec_recovery as recovery

    def fail(**kwargs):
        assert kwargs["dir"] == str(tmp_path)
        raise OSError(errno.ENOSPC, "No space left")

    monkeypatch.setattr(recovery.tempfile, "TemporaryFile", fail)
    result = recovery.automatic_export(
        pictures(),
        tmp_path / "out.mp4",
        export=lambda frames, codec: list(frames),
        cancel_event=None,
        temporary_directory=str(tmp_path),
        note_callback=None,
        warning_callback=None,
    )
    assert len(result) == 4
    assert not list(tmp_path.iterdir())


def test_replay_snapshots_pixels_before_the_first_export_mutates_them(tmp_path):
    from localsr.core.video_codec_recovery import automatic_export

    source = next(pictures())
    expected = source.rgb.copy()
    calls = []

    def export(frames, codec):
        calls.append(codec)
        if codec == "hevc":
            next(iter(frames)).rgb[:] = 255
            raise SystemCodecsError("Cannot Encode Media")
        return list(frames)

    replayed = automatic_export(
        iter([source]),
        tmp_path / "out.mp4",
        export=export,
        cancel_event=None,
        temporary_directory=str(tmp_path),
        note_callback=None,
        warning_callback=None,
    )
    assert calls == ["hevc", "av1"]
    np.testing.assert_array_equal(replayed[0].rgb, expected)
    assert replayed[0].timestamp == source.timestamp
    assert replayed[0].rgb.flags.owndata
