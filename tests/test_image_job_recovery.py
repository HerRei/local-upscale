"""Exercise job failures through the worker loop, including the next queued job."""

import errno
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
from PIL import Image

from localsr.core.model_adapter import NormalizedModelInfo
from localsr.core.output_writer import OutputWriter
from localsr.core.pipeline import resolve_image_pipeline
from localsr.worker.job_progress import ImageJobProgress
from localsr.worker.server import WorkerServer


class TestPreview:
    __test__ = False

    def __init__(self, *_args, **_kwargs):
        self.closed = False

    def close(self):
        self.closed = True


@pytest.mark.parametrize("failure", ["corrupt_input", "disk_full", "cancel_before_decode"])
def test_failed_or_cancelled_job_cleans_up_and_next_job_completes(tmp_path, monkeypatch, failure):
    import localsr.worker.server as server_module

    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    Image.new("RGB", (8, 8), (32, 64, 96)).save(second)
    if failure == "corrupt_input":
        first.write_bytes(b"not an image")
    else:
        Image.new("RGB", (8, 8)).save(first)
    destination = tmp_path / "first-output.png"
    destination.write_bytes(b"existing output must survive")
    scratch = tmp_path / "scratch"
    previews = []
    writers = []
    decoded = []
    messages = []
    server = WorkerServer()
    server.model_adapter = SimpleNamespace(
        inspect=lambda path: NormalizedModelInfo("Test", 2, 3, 3, True, False, 1, 1, path, []),
        release=lambda: None,
    )

    def preview_factory(*args, **kwargs):
        preview = TestPreview(*args, **kwargs)
        previews.append(preview)
        return preview

    def process_image(**kwargs):
        writer = OutputWriter((3, 16, 16), temporary_directory=scratch)
        writer.mmap[:] = 96
        writers.append(writer)
        return writer

    server.engine = SimpleNamespace(process_image=process_image, release_model=lambda *args: None)
    monkeypatch.setattr(server_module, "LatestPreviewEncoder", preview_factory)
    # Feed the actual worker loop without a stdin thread cancelling on EOF.
    monkeypatch.setattr(server, "reader_thread_func", lambda: None)
    from localsr.core.image_io import ImageManager

    real_load = ImageManager.load

    def record_load(manager, path):
        decoded.append(path)
        return real_load(manager, path)

    monkeypatch.setattr(ImageManager, "load", record_load)
    real_replace = __import__("os").replace

    def replace(source, target):
        if failure == "disk_full" and Path(target) == destination:
            raise OSError(errno.ENOSPC, "No space left on device")
        return real_replace(source, target)

    monkeypatch.setattr("os.replace", replace)

    def emit(message):
        envelope = json.loads(message.to_json())
        if envelope["type"] in {"job_completed", "job_failed", "job_cancelled"}:
            assert all(preview.closed for preview in previews)
            assert not list(scratch.glob("localsr-output-*.dat"))
        messages.append(envelope)

    monkeypatch.setattr(server_module, "send_message", emit)
    for index, path in enumerate((first, second)):
        job_id = f"job-{index}"
        server.message_queue.put(
            {
                "type": "job_request",
                "data": {
                    "job_id": job_id,
                    "image_path": str(path),
                    "model_path": str(tmp_path / "model.pth"),
                    "output_path": str(
                        destination if index == 0 else tmp_path / "second-output.png"
                    ),
                    "scratch_directory": str(scratch),
                    "output_format": "png",
                    "device": "cpu",
                    "precision": "fp32",
                    "tile_size": 64,
                    "halo": 8,
                    "jpeg_quality": 98,
                    "preserve_metadata": True,
                    "safe_memory": True,
                    "preview_enabled": False,
                },
            }
        )
    if failure == "cancel_before_decode":
        server._request_cancel("job-0")
    server.message_queue.put({"type": "shutdown_request"})
    server.run()

    terminal = [
        (item["type"], item["data"]["job_id"])
        for item in messages
        if item["type"] in {"job_failed", "job_cancelled", "job_completed"}
    ]
    assert terminal == [
        ("job_cancelled" if failure == "cancel_before_decode" else "job_failed", "job-0"),
        ("job_completed", "job-1"),
    ]
    assert destination.read_bytes() == b"existing output must survive"
    with Image.open(tmp_path / "second-output.png") as result:
        assert result.size == (16, 16)
        np.testing.assert_array_equal(np.asarray(result), np.full((16, 16, 3), 96, dtype=np.uint8))
    assert all(writer.mmap is None for writer in writers)
    assert not list(tmp_path.glob(".*.tmp*"))
    if failure == "cancel_before_decode":
        assert decoded == [str(second)]


def test_progress_accounts_for_fused_stages_and_excludes_initial_model_loading(monkeypatch):
    monkeypatch.setattr("localsr.worker.job_progress.get_memory_snapshot", lambda _: {})
    pipeline = resolve_image_pipeline(
        raw_stages=[
            {"kind": "restore", "model_id": "clean", "model_path": "clean.pth"},
            {"kind": "upscale", "model_id": "up", "model_path": "up.pth"},
            {
                "kind": "face_restore",
                "model_id": "face",
                "model_path": "face.pth",
                "execution": "fused-with-upscale",
            },
        ],
        model_path="up.pth",
        face_model_path="face.pth",
    )
    now = [100.0]
    emitted = []
    progress = ImageJobProgress(
        "job", pipeline, "cpu", emitted.append, Mock(), clock=lambda: now[0]
    )
    progress.start(0, pipeline.preprocess)
    progress.update(0, 2, 64)
    now[0] = 200.0  # Expensive model loading has not started inference.
    progress.tile("started", None, None, 0, 2, 16, 16, 64)
    now[0] = 202.0
    progress.update(2, 2, 64)
    progress.start(1, pipeline.primary, span=2)
    now[0] = 204.0
    progress.update(1, 2, 64)
    now[0] = 206.0
    progress.update(2, 2, 64)

    envelopes = [json.loads(message.to_json()) for message in emitted]
    updates = [message["data"] for message in envelopes if message["type"] == "progress"]
    assert [item["percentage"] for item in updates] == pytest.approx([0, 100 / 3, 200 / 3, 100])
    assert [item["elapsed_seconds"] for item in updates] == [0, 2, 4, 6]
    assert [item["estimated_remaining_seconds"] for item in updates] == [0, 4, 2, 0]
    stages = [message["data"] for message in envelopes if message["type"] == "stage_progress"]
    assert [(item["stage_kind"], item["percentage"]) for item in stages[-2:]] == [
        ("upscale", 100),
        ("face_restore", 100),
    ]
