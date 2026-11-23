"""A cancel is honoured until the output is published; after that the job is complete."""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

import av
import numpy as np
import pytest
from test_video_interpolation import patched_worker, worker_request
from test_video_timing import IdentityEngine, make_vfr

import localsr.core.video_io as video_io
import localsr.worker.server as server_module
from localsr.core.video_io import encode_video


def _frames(count=3):
    for index in range(count):
        yield np.full((64, 64, 3), 40 * index, dtype=np.uint8)


def _leftovers(directory):
    return [path.name for path in directory.iterdir()]


def test_cancel_during_the_encoder_flush_publishes_nothing(tmp_path):
    cancel = threading.Event()

    def frames_then_cancel():
        yield from _frames()
        cancel.set()

    destination = tmp_path / "out.mp4"
    with pytest.raises(InterruptedError):
        encode_video(
            frames_then_cancel(),
            str(destination),
            fps=5.0,
            width=64,
            height=64,
            cancel_event=cancel,
        )
    assert _leftovers(tmp_path) == []


def test_cancel_during_the_container_close_publishes_nothing(tmp_path, monkeypatch):
    cancel = threading.Event()
    real_open = video_io.av.open

    class CancelsOnClose:
        def __init__(self, container):
            self._container = container

        def __getattr__(self, name):
            return getattr(self._container, name)

        def close(self):
            cancel.set()
            self._container.close()

    def open_container(path, mode="r", **options):
        container = real_open(path, mode=mode, **options)
        return CancelsOnClose(container) if mode == "w" else container

    monkeypatch.setattr(video_io.av, "open", open_container)
    with pytest.raises(InterruptedError):
        encode_video(
            _frames(),
            str(tmp_path / "out.mp4"),
            fps=5.0,
            width=64,
            height=64,
            cancel_event=cancel,
        )
    assert _leftovers(tmp_path) == []


def _run_temporal(tmp_path, monkeypatch, wrap_encode, keep=False):
    source = make_vfr(tmp_path / "source.mp4")
    output = tmp_path / "output.mp4"
    messages = []
    monkeypatch.setattr(server_module, "send_message", messages.append)
    real_encode = video_io.encode_video
    cancel = threading.Event()
    monkeypatch.setattr(
        video_io, "encode_video", lambda *a, **k: wrap_encode(real_encode, cancel, *a, **k)
    )
    worker = SimpleNamespace(
        cancel_event=cancel,
        _emit_live_preview=lambda _: None,
        _keep_partial_for_job=lambda _job: keep,
    )
    server_module.WorkerServer._run_temporal_video_job(
        worker,
        "job",
        {
            "video_path": str(source),
            "output_video_path": str(output),
            "preview_enabled": False,
        },
        lambda *_: IdentityEngine(),
    )
    return output, [type(message).__name__ for message in messages], messages


def test_temporal_job_cancelled_during_the_flush_is_cancelled_and_leaves_no_file(
    tmp_path, monkeypatch
):
    def encode(real, cancel, frames, *args, **kwargs):
        def then_cancel():
            yield from frames
            cancel.set()

        return real(then_cancel(), *args, **kwargs)

    output, kinds, _ = _run_temporal(tmp_path, monkeypatch, encode)
    assert "JobCancelled" in kinds and "VideoJobCompleted" not in kinds
    assert not output.exists()


def test_temporal_job_cancelled_after_publication_reports_the_saved_output(tmp_path, monkeypatch):
    def encode(real, cancel, *args, **kwargs):
        result = real(*args, **kwargs)
        cancel.set()  # during the container close, the model release or other cleanup
        return result

    output, kinds, messages = _run_temporal(tmp_path, monkeypatch, encode)
    assert output.is_file()
    assert "VideoJobCompleted" in kinds and "JobCancelled" not in kinds
    completed = next(m for m in messages if type(m).__name__ == "VideoJobCompleted")
    assert completed.output_path == str(output)


def test_temporal_job_stopped_with_keep_reports_the_finished_part(tmp_path, monkeypatch):
    def encode(real, cancel, frames, *args, **kwargs):
        def cancel_after_two():
            for index, frame in enumerate(frames):
                if index == 2:
                    cancel.set()
                yield frame

        return real(cancel_after_two(), *args, **kwargs)

    output, kinds, messages = _run_temporal(tmp_path, monkeypatch, encode, keep=True)
    assert "VideoJobCompleted" in kinds and "JobCancelled" not in kinds
    completed = next(m for m in messages if type(m).__name__ == "VideoJobCompleted")
    assert completed.partial
    assert completed.output_path != str(output)
    assert Path(completed.output_path).is_file()
    assert "_partial_" in Path(completed.output_path).name
    assert not output.exists()


def _frame_by_frame_server(monkeypatch, tmp_path, fake_run):
    server, _bundle, _loaded, _captured = patched_worker(monkeypatch, tmp_path)
    messages = []
    monkeypatch.setattr(server_module, "send_message", messages.append)
    monkeypatch.setattr(server_module, "run_video_job", fake_run)
    server._run_video_job("job", worker_request(tmp_path))
    return server, [type(message).__name__ for message in messages], messages


def test_frame_by_frame_job_cancelled_after_publication_reports_the_saved_output(
    monkeypatch, tmp_path
):
    holder = {}

    def run_then_cancel(**kwargs):
        holder["cancel"] = kwargs["cancel_event"]
        kwargs["cancel_event"].set()
        return SimpleNamespace(
            output_path=kwargs["config"].output_video_path,
            frames_processed=3,
            elapsed_seconds=1.0,
            inference_seconds=1.0,
            partial=False,
            kept_seconds=0.0,
            kept_frames=0,
        )

    _server, kinds, messages = _frame_by_frame_server(monkeypatch, tmp_path, run_then_cancel)
    assert "VideoJobCompleted" in kinds and "JobCancelled" not in kinds
    completed = next(m for m in messages if type(m).__name__ == "VideoJobCompleted")
    assert completed.output_path == str(tmp_path / "output.mp4")


def test_frame_by_frame_job_cancelled_before_publication_is_cancelled(monkeypatch, tmp_path):
    def interrupted(**kwargs):
        kwargs["cancel_event"].set()
        raise InterruptedError("video job cancelled")

    _server, kinds, _ = _frame_by_frame_server(monkeypatch, tmp_path, interrupted)
    assert "JobCancelled" in kinds and "VideoJobCompleted" not in kinds


def test_streamed_system_stop_closes_input_and_publishes_partial(tmp_path, monkeypatch):
    from fractions import Fraction

    from localsr.core.video_io import TimedVideoFrame, _system_stream_export

    calls = []
    source = tmp_path / "source.mp4"
    source.touch()

    class FakeSystem:
        def encode_frames(self, frames, final, **options):
            assert options["cancel_event"] is None
            assert options["audio"] is None
            calls.append(len(list(frames)))
            final.write_bytes(b"video")

    def remux(video, audio, output, **options):
        assert audio == source
        assert options["force_audio_transcode"]
        assert options["cut_packet_durations"]
        output.write_bytes(video.read_bytes() + b"audio")
        return True

    monkeypatch.setattr(video_io, "_remux_source_streams", remux)
    rgb = np.zeros((4, 4, 3), dtype=np.uint8)

    def frames():
        yield TimedVideoFrame(0, rgb, Fraction(0), Fraction(1, 2), Fraction(1, 2))
        yield TimedVideoFrame(1, rgb, Fraction(1, 2), Fraction(1, 2), Fraction(1, 2))
        raise InterruptedError("stopped")

    result = _system_stream_export(
        frames(),
        tmp_path / "out.mp4",
        system=FakeSystem(),
        video_codec="hevc",
        container_format="mp4",
        crf=18,
        fps=2,
        audio_source=str(source),
        cancel_event=threading.Event(),
        temporary_directory=None,
        keep_partial=True,
        stage_callback=calls.append,
    )
    assert calls == ["partial_finalizing", 2]
    assert result.endswith("out_partial_0m01s.mp4")
    assert Path(result).read_bytes() == b"videoaudio"


@pytest.mark.parametrize("keep", [True, False])
def test_temporal_stop_right_after_the_first_window_keeps_that_window(tmp_path, monkeypatch, keep):
    """Bug hunt 2026-11-23: a stop after SeedVR's first window threw its frames away."""

    def encode(real, cancel, frames, *args, **kwargs):
        cancel.set()  # the window is done; the encoder has not read a frame yet
        return real(frames, *args, **kwargs)

    output, kinds, messages = _run_temporal(tmp_path, monkeypatch, encode, keep=keep)
    if not keep:
        assert "JobCancelled" in kinds and "VideoJobCompleted" not in kinds
        assert not any(path.name.startswith("output") for path in tmp_path.iterdir())
        return
    completed = next(m for m in messages if type(m).__name__ == "VideoJobCompleted")
    assert completed.partial
    with av.open(completed.output_path) as container:
        written = sum(1 for _ in container.decode(video=0))
    assert written > 0
    assert written == completed.frames_processed


def test_a_stop_that_keeps_after_the_last_frame_saves_the_whole_video(tmp_path):
    """Argon 11-23: every frame was written, then Keep; the finished video must not be lost."""
    cancel = threading.Event()

    def frames_then_stop():
        yield from _frames()
        cancel.set()

    destination = tmp_path / "out.mp4"
    written = encode_video(
        frames_then_stop(),
        str(destination),
        fps=5.0,
        width=64,
        height=64,
        cancel_event=cancel,
        keep_partial=True,
    )
    assert written == str(destination)
    with av.open(written) as container:
        assert sum(1 for _ in container.decode(video=0)) == 3


def test_a_kept_part_with_the_name_of_an_earlier_one_gets_a_number(tmp_path):
    """Hermes, 11-23: a second kept part of the same length found the name taken and the
    whole kept part was lost ("already exists ... this result was not saved")."""
    names = []
    for _ in range(2):
        cancel = threading.Event()

        def stopped_after_two():
            frames = _frames()
            yield next(frames)
            yield next(frames)
            cancel.set()
            raise InterruptedError("stopped")

        names.append(
            encode_video(
                stopped_after_two(),
                str(tmp_path / "clip_video_4x.mkv"),
                fps=5.0,
                width=64,
                height=64,
                container_format="mkv",
                cancel_event=cancel,
                keep_partial=True,
            )
        )
    assert [Path(name).name for name in names] == [
        "clip_video_4x_partial_0m00s.mkv",
        "clip_video_4x_partial_0m00s-2.mkv",
    ]
    assert all(Path(name).is_file() for name in names)
