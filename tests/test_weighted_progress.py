from types import SimpleNamespace
from unittest.mock import Mock

from localsr.core.pipeline import ImagePipeline, PipelineStage
from localsr.worker.job_progress import ImageJobProgress


def test_weighted_progress_and_remaining_across_stages(monkeypatch):
    monkeypatch.setattr("localsr.worker.job_progress.get_memory_snapshot", lambda _: {})
    stages = tuple(PipelineStage("restore", f"unknown-{i}") for i in range(3)) + (
        PipelineStage("upscale", "unknown-up"),
    )
    pipeline = ImagePipeline(stages, 3)
    now = [0.0]
    messages = []
    progress = ImageJobProgress(
        "job", pipeline, "cpu", messages.append, Mock(), clock=lambda: now[0]
    )
    progress.configure_work(100, 100, 4)
    samples = []
    for index, stage in enumerate(stages):
        progress.start(index, stage)
        side = 400 if index == 3 else 100
        tile = SimpleNamespace(out_x=0, out_y=0, out_w=side // 10, out_h=side)
        for n in range(1, 11):
            progress.tile("started", tile, None, n - 1, 10, side, side, side // 10)
            now[0] += 1
            progress.tile("completed", tile, None, n, 10, side, side, side // 10)
            progress.update(n, 10, side // 10)
            update = messages[-1]
            samples.append((update.percentage, update.estimated_remaining_seconds))
    assert samples[0][1] == 0
    assert samples[4][1] > 0
    assert samples[29][0] < 50
    assert all(a[0] <= b[0] for a, b in zip(samples, samples[1:], strict=False))
    assert samples[-2][1] > 0
    assert samples[-1] == (100, 0)


def test_save_status_precedes_write_and_replaces_old_message():
    from localsr.worker.image_job import emit_save_status

    messages = [SimpleNamespace(message="old engine message")]
    writer = SimpleNamespace(shape=(3, 10080, 15104))
    emit_save_status(
        messages.append,
        "job",
        writer,
        SimpleNamespace(scale=4),
        {"output_format": "png", "output_scale": 4},
    )
    assert len(messages) == 2
    assert messages[-1].message == "Writing a 15,104 \N{MULTIPLICATION SIGN} 10,080 PNG (152 MP)."

    # A small result keeps a meaningful size instead of "0 MP".
    emit_save_status(
        messages.append,
        "job",
        SimpleNamespace(shape=(3, 400, 600)),
        SimpleNamespace(scale=1),
        {"output_format": "jpg", "output_scale": 1},
    )
    assert messages[-1].message == "Writing a 600 \N{MULTIPLICATION SIGN} 400 JPG (0.2 MP)."


def test_background_save_reports_once_before_writing(monkeypatch):
    import threading

    from localsr.worker.image_job import SavePipeline, _SaveTask

    events = []
    writer = SimpleNamespace(shape=(3, 10080, 15104), cleanup=Mock())
    manager = SimpleNamespace(notes=[], save=lambda *args, **kwargs: events.append("write"))
    data = {
        "output_path": "out.png",
        "output_format": "png",
        "output_scale": 4,
        "jpeg_quality": 90,
        "preserve_metadata": False,
    }
    task = _SaveTask(
        job_id="job",
        manager=manager,
        writer=writer,
        image={},
        info=SimpleNamespace(scale=4),
        data=data,
        notes=[],
        started_at=0,
        inference_started_at=0,
        discard_signal=threading.Event(),
        emit=events.append,
        log=Mock(),
    )
    SavePipeline()._execute(task)
    statuses = [event for event in events if type(event).__name__ == "EngineStatus"]
    assert len(statuses) == 1
    assert events.index(statuses[0]) < events.index("write")
    assert statuses[0].message == "Writing a 15,104 \N{MULTIPLICATION SIGN} 10,080 PNG (152 MP)."
