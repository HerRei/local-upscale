"""A cancel is honoured until the output is published; after that the job is complete."""

from __future__ import annotations

import threading
from types import SimpleNamespace

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


def _run_temporal(tmp_path, monkeypatch, wrap_encode):
    source = make_vfr(tmp_path / "source.mp4")
    output = tmp_path / "output.mp4"
    messages = []
    monkeypatch.setattr(server_module, "send_message", messages.append)
    real_encode = video_io.encode_video
    cancel = threading.Event()
    monkeypatch.setattr(
        video_io, "encode_video", lambda *a, **k: wrap_encode(real_encode, cancel, *a, **k)
    )
    worker = SimpleNamespace(cancel_event=cancel, _emit_live_preview=lambda _: None)
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
