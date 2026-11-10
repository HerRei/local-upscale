import json
import threading
from types import SimpleNamespace

import pytest

from localsr.protocol.messages import JobRequest
from localsr.worker.server import WorkerServer

# The size of the decoded image is all the job reads before the engine runs.
TINY = SimpleNamespace(shape=(3, 8, 8))


class DummyModelAdapter:
    status_callback = None
    cancel_event = None

    def inspect(self, path):
        from localsr.core.model_adapter import NormalizedModelInfo

        return NormalizedModelInfo("Dummy", 2, 3, 3, True, False, 1, 1, "dummy.pth", [])

    def load(self, path, dev, prec):
        return None, None

    def release(self):
        pass


class DummyWriter:
    def __init__(self):
        self.cleaned_up = False

    def cleanup(self):
        self.cleaned_up = True


class DummyEngine:
    def release_models_except(self, keep):
        pass

    def process_image(
        self,
        img_data,
        model_info,
        model_path,
        device_str,
        precision_str,
        tile_size,
        halo,
        cancel_event,
        progress_callback,
        safe_memory,
        tile_callback=None,
        temporary_directory=None,
    ):
        progress_callback(0, 4, tile_size)
        assert cancel_event.wait(timeout=2)
        raise InterruptedError()


def test_worker_cancellation(monkeypatch, capsys):
    # We will feed stdin manually
    server = WorkerServer()
    server.model_adapter = DummyModelAdapter()
    server.engine = DummyEngine()

    # Fake image IO
    from localsr.core.image_io import ImageManager

    def fake_load(*args):
        return {"tensor": TINY}

    def fake_save(*args, **kwargs):
        pass

    monkeypatch.setattr(ImageManager, "load", fake_load)
    monkeypatch.setattr(ImageManager, "save", fake_save)

    # Push job
    req = JobRequest(
        job_id="job_c1",
        image_path="test.png",
        model_path="test.pth",
        output_path="out.png",
        output_format="png",
        device="cpu",
        tile_size=256,
        halo=32,
        precision="fp32",
        jpeg_quality=98,
        preserve_metadata=True,
        safe_memory=True,
    )

    started = threading.Event()
    original_process = server.engine.process_image

    def process_image(*args, **kwargs):
        started.set()
        return original_process(*args, **kwargs)

    server.engine.process_image = process_image

    def delayed_cancel():
        assert started.wait(timeout=2)
        server.cancel_event.set()
        server.message_queue.put({"type": "shutdown_request"})

    t = threading.Thread(target=delayed_cancel)
    t.start()

    server.message_queue.put(json.loads(req.to_json()))

    import sys
    from io import StringIO

    old_stdout = sys.stdout
    sys.stdout = StringIO()

    try:
        # We manually run the loop body once for the job, and then shutdown will hit
        while server.running:
            msg = server.message_queue.get(timeout=1.0)
            if msg["type"] == "shutdown_request":
                break
            if msg["type"] == "job_request":
                server.cancel_event.clear()
                server._run_job("job_c1", msg["data"])
    finally:
        output = sys.stdout.getvalue()
        sys.stdout = old_stdout
        t.join(timeout=2)
        assert not t.is_alive()

    assert "job_cancelled" in output


def test_cancel_requested_before_job_activation_is_not_lost():
    server = WorkerServer()

    server._request_cancel("queued-job")
    assert not server.cancel_event.is_set()

    server._activate_job("queued-job")
    assert server.active_job_id == "queued-job"
    assert server.cancel_event.is_set()

    server._deactivate_job("queued-job")
    assert not server.cancel_event.is_set()
    assert "queued-job" not in server.pending_cancel_job_ids


@pytest.mark.parametrize("how", ["eof", "shutdown_request"])
def test_a_job_queued_before_shutdown_stays_cancelled_when_it_activates(how):
    server = WorkerServer()
    if how == "eof":
        import io
        import sys

        stdin, sys.stdin = sys.stdin, io.StringIO("")
        try:
            server.reader_thread_func()
        finally:
            sys.stdin = stdin
    else:
        server._read_line(json.dumps({"type": "shutdown_request"}))
    assert server.cancel_event.is_set()

    server._activate_job("queued-job")
    assert server.cancel_event.is_set()
    server._deactivate_job("queued-job")
    assert server.cancel_event.is_set()

    server._activate_batch(["a", "b"])
    assert server.cancel_event.is_set()
    server._deactivate_batch(["a", "b"])
    assert server.cancel_event.is_set()


def test_an_image_job_that_would_start_after_shutdown_does_not_run(monkeypatch, capsys):
    server = WorkerServer()
    server.model_adapter = DummyModelAdapter()
    server.engine = DummyEngine()
    server.engine.process_image = lambda *a, **k: pytest.fail("inference must not start")
    from localsr.core.image_io import ImageManager

    monkeypatch.setattr(ImageManager, "load", lambda *a: {"tensor": TINY})
    server._read_line(json.dumps({"type": "shutdown_request"}))
    request = JobRequest(
        job_id="late",
        image_path="test.png",
        model_path="test.pth",
        output_path="out.png",
        output_format="png",
        device="cpu",
        tile_size=256,
        halo=32,
        precision="fp32",
        jpeg_quality=98,
        preserve_metadata=True,
        safe_memory=True,
    )
    server._run_image_job(json.loads(request.to_json())["data"])
    assert "job_cancelled" in capsys.readouterr().out


def test_queued_cancel_does_not_affect_a_different_job():
    server = WorkerServer()

    server._request_cancel("other-job")
    server._activate_job("active-job")

    assert server.active_job_id == "active-job"
    assert not server.cancel_event.is_set()


def test_temporal_bundle_is_verified_in_worker_before_use(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import localsr.worker.server as server_module

    bundle = tmp_path / "video-bundle"
    bundle.mkdir()
    model = SimpleNamespace(model_id="video-test", name="Video Test")
    monkeypatch.setitem(server_module.VIDEO_CATALOG_BY_ID, "video-test", model)

    class DummyStore:
        def __init__(self):
            self.checked = False

        def bundle_dir_for(self, candidate):
            assert candidate is model
            return bundle

        def is_bundle_installed(self, candidate):
            assert candidate is model
            self.checked = True
            return True

    server = WorkerServer()
    server.model_store = DummyStore()
    server._verify_temporal_bundle({"video_model_id": "video-test", "bundle_dir": str(bundle)})

    assert server.model_store.checked is True


def test_temporal_bundle_rejects_non_catalog_path(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import localsr.worker.server as server_module

    expected = tmp_path / "expected"
    supplied = tmp_path / "supplied"
    expected.mkdir()
    supplied.mkdir()
    model = SimpleNamespace(model_id="video-test", name="Video Test")
    monkeypatch.setitem(server_module.VIDEO_CATALOG_BY_ID, "video-test", model)

    class DummyStore:
        def bundle_dir_for(self, _candidate):
            return expected

        def is_bundle_installed(self, _candidate):
            raise AssertionError("untrusted path must be rejected before hashing")

    server = WorkerServer()
    server.model_store = DummyStore()
    with pytest.raises(ValueError, match="does not match"):
        server._verify_temporal_bundle(
            {"video_model_id": "video-test", "bundle_dir": str(supplied)}
        )


def test_worker_job_completion_and_cleanup(monkeypatch):
    server = WorkerServer()
    server.model_adapter = DummyModelAdapter()

    writer_instance = DummyWriter()

    class InstantEngine:
        def release_models_except(self, keep):
            pass

        def process_image(self, **kwargs):
            return writer_instance

    server.engine = InstantEngine()

    from localsr.core.image_io import ImageManager

    load_called = False
    save_kwargs_captured = {}

    def fake_load(self, path):
        nonlocal load_called
        load_called = True
        return {
            "tensor": TINY,
            "icc_profile": b"dummy_icc",
            "safe_exif": {315: "Artist"},
        }

    def fake_save(
        self,
        output_writer,
        destination_path,
        format,
        quality,
        preserve_metadata,
        icc_profile=None,
        safe_exif=None,
        scale=1,
        output_scale=None,
        temporary_directory=None,
        cancel_event=None,
    ):
        save_kwargs_captured["output_writer"] = output_writer
        save_kwargs_captured["icc_profile"] = icc_profile
        save_kwargs_captured["safe_exif"] = safe_exif
        save_kwargs_captured["scale"] = scale
        save_kwargs_captured["output_scale"] = output_scale

    monkeypatch.setattr(ImageManager, "load", fake_load)
    monkeypatch.setattr(ImageManager, "save", fake_save)

    job_data = {
        "job_id": "job_100",
        "image_path": "in.png",
        "model_path": "model.pth",
        "output_path": "out.png",
        "output_format": "png",
        "device": "cpu",
        "precision": "fp32",
        "tile_size": 256,
        "halo": 32,
        "jpeg_quality": 98,
        "preserve_metadata": True,
        "safe_memory": True,
        "output_scale": 2,
    }

    server._run_job("job_100", job_data)

    # Check F1.3 parameter passing
    assert save_kwargs_captured["output_writer"] == writer_instance
    assert save_kwargs_captured["icc_profile"] == b"dummy_icc"
    assert save_kwargs_captured["safe_exif"] == {315: "Artist"}
    assert save_kwargs_captured["scale"] == 2  # scale from DummyModelAdapter.inspect()
    assert save_kwargs_captured["output_scale"] == 2

    # Check F1.4 cleanup in finally
    assert writer_instance.cleaned_up is True


def test_worker_hands_progressive_tiles_to_nonblocking_preview_encoder(monkeypatch):
    from types import SimpleNamespace

    import localsr.worker.server as server_module
    from localsr.core.image_io import ImageManager

    server = WorkerServer()
    server.model_adapter = DummyModelAdapter()
    writer = DummyWriter()

    class FastTiledEngine:
        def release_models_except(self, keep):
            pass

        def process_image(self, **kwargs):
            callback = kwargs["tile_callback"]
            tile = SimpleNamespace(out_x=0, out_y=0, out_w=16, out_h=16)
            for completed in range(1, 11):
                callback("completed", tile, object(), completed, 10, 160, 16, 64)
            return writer

    submitted = []
    server.engine = FastTiledEngine()
    monkeypatch.setattr(ImageManager, "load", lambda *_: {"tensor": TINY})
    monkeypatch.setattr(ImageManager, "save", lambda *_, **__: None)

    class FakePreviewEncoder:
        def __init__(self, *_args, **kwargs):
            assert kwargs["max_fps"] == 2.0

        def submit(self, **kwargs):
            submitted.append(kwargs)

        def close(self):
            pass

    monkeypatch.setattr(server_module, "LatestPreviewEncoder", FakePreviewEncoder)

    server._run_job(
        "job-sampled-preview",
        {
            "job_id": "job-sampled-preview",
            "image_path": "in.png",
            "model_path": "model.pth",
            "output_path": "out.png",
            "output_format": "png",
            "device": "cpu",
            "precision": "fp32",
            "tile_size": 64,
            "halo": 8,
            "jpeg_quality": 98,
            "preserve_metadata": True,
            "safe_memory": True,
            "output_scale": 2,
            "preview_enabled": True,
            "preview_max_fps": 2.0,
        },
    )

    assert len(submitted) == 10
    assert submitted[-1]["force"] is True
    assert all(item["preview_kind"] == "tile" for item in submitted)


def test_a_video_reports_its_preparation_and_carries_the_route_it_gave_up_in_its_notes(
    monkeypatch, capsys
):
    from types import SimpleNamespace

    import localsr.worker.server as server_module

    server = WorkerServer()
    adapter = DummyModelAdapter()
    seen = {}
    plain_inspect = adapter.inspect

    def inspect(path):
        # The checkpoint read is bounded: it must already say so and be cancellable.
        seen["cancel"], seen["status"] = adapter.cancel_event, adapter.status_callback
        return plain_inspect(path)

    adapter.inspect = inspect
    server.model_adapter = adapter
    server.engine.add_note("left over from an earlier job")

    def fake_video_job(**kwargs):
        server.engine.add_note(
            "Running on the standard path: the faster one was not ready in time."
        )
        return SimpleNamespace(
            output_path="output.mp4", frames_processed=2, elapsed_seconds=1.0, inference_seconds=1.0
        )

    monkeypatch.setattr(server_module, "run_video_job", fake_video_job)
    server._run_video_job(
        "video-notes",
        {
            "video_path": "input.mp4",
            "model_path": "model.pth",
            "output_video_path": "output.mp4",
            "device": "cpu",
            "precision": "fp32",
            "tile_size": 64,
            "halo": 8,
        },
    )
    assert seen["cancel"] is server.cancel_event
    seen["status"]("Reading model.pth.")
    messages = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert {"job_id": "video-notes", "message": "Reading model.pth."} in [
        item["data"] for item in messages if item["type"] == "engine_status"
    ]
    done = next(item["data"] for item in messages if item["type"] == "video_job_completed")
    assert done["notes"] == "Running on the standard path: the faster one was not ready in time."
    assert server.engine.notes == []


def test_video_worker_emits_one_authoritative_progress_event_with_eta(monkeypatch, capsys):
    from types import SimpleNamespace

    import localsr.worker.server as server_module

    server = WorkerServer()
    server.model_adapter = DummyModelAdapter()
    submitted = []

    class FakePreviewEncoder:
        def __init__(self, *_args, **kwargs):
            self.enabled = kwargs.get("enabled", True)

        def submit(self, **kwargs):
            submitted.append(kwargs)
            return True

        def clear(self):
            pass

        def close(self):
            pass

    def fake_video_job(**kwargs):
        kwargs["frame_started_cb"](2, 10)
        kwargs["source_frame_cb"](2, 10, __import__("numpy").zeros((8, 8, 3), dtype="uint8"))
        kwargs["enhanced_frame_cb"](2, 10, __import__("numpy").zeros((8, 8, 3), dtype="uint8"))
        kwargs["progress_cb"](3, 10, 6.0, 14.0)
        return SimpleNamespace(
            output_path="output.mp4",
            frames_processed=10,
            elapsed_seconds=20.0,
            inference_seconds=19.0,
        )

    monkeypatch.setattr(server_module, "LatestPreviewEncoder", FakePreviewEncoder)
    monkeypatch.setattr(server_module, "run_video_job", fake_video_job)
    server._run_video_job(
        "video-eta",
        {
            "video_path": "input.mp4",
            "model_path": "model.pth",
            "output_video_path": "output.mp4",
            "device": "cpu",
            "precision": "fp32",
            "tile_size": 64,
            "halo": 8,
        },
    )

    messages = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    progress = [item for item in messages if item["type"] == "video_frame_completed"]
    assert len(progress) == 1
    expected = {
        "job_id": "video-eta",
        "frame_index": 2,
        "total_frames": 10,
        "frames_processed": 3,
        "elapsed_seconds": 6.0,
        "estimated_remaining_seconds": 14.0,
        "jpeg_base64": "",
    }
    assert {key: progress[0]["data"][key] for key in expected} == expected
    assert [packet["preview_kind"] for packet in submitted] == ["source_video", "video"]
    assert all(packet["frame_index"] == 2 for packet in submitted)


def test_worker_memmap_cleanup_on_error(monkeypatch):
    server = WorkerServer()
    server.model_adapter = DummyModelAdapter()

    writer_instance = DummyWriter()

    class InstantEngine:
        def release_models_except(self, keep):
            pass

        def process_image(self, **kwargs):
            return writer_instance

    server.engine = InstantEngine()

    from localsr.core.image_io import ImageManager

    def fake_load(self, path):
        return {"tensor": TINY}

    def bad_save(*args, **kwargs):
        raise RuntimeError("Save failed")

    monkeypatch.setattr(ImageManager, "load", fake_load)
    monkeypatch.setattr(ImageManager, "save", bad_save)

    job_data = {
        "job_id": "job_101",
        "image_path": "in.png",
        "model_path": "model.pth",
        "output_path": "out.png",
        "output_format": "png",
        "device": "cpu",
        "precision": "fp32",
        "tile_size": 256,
        "halo": 32,
        "jpeg_quality": 98,
        "preserve_metadata": True,
        "safe_memory": True,
    }

    with pytest.raises(RuntimeError, match="Save failed"):
        server._run_job("job_101", job_data)

    # Check F1.4 cleanup executed in finally despite exception
    assert writer_instance.cleaned_up is True


def _sent(capsys):
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]


def _batch_jobs(count):
    return [
        {
            "job_id": f"photo-{index}",
            "image_path": f"{index}.png",
            "output_path": f"{index}-out.png",
            "output_format": "png",
            "jpeg_quality": 90,
            "preserve_metadata": True,
            "model_path": "model.pth",
            "device": "cpu",
            "tile_size": 64,
            "halo": 8,
            "precision": "fp32",
            "safe_memory": True,
        }
        for index in range(count)
    ]


def test_cancelling_any_batch_member_cancels_the_whole_batch():
    server = WorkerServer()
    server._request_cancel("photo-1")
    server._activate_batch(["photo-0", "photo-1", "photo-2"])
    assert server.cancel_event.is_set()
    # A photo the lanes hand back runs as its own job and is cancelled too.
    server._activate_job("photo-2")
    assert server.cancel_event.is_set()
    server._deactivate_job("photo-2")
    server._deactivate_batch(["photo-0", "photo-1", "photo-2"])
    assert not server.cancel_event.is_set() and not server.pending_cancel_job_ids
    server._activate_job("next")
    assert not server.cancel_event.is_set()

    server._activate_batch(["a", "b"])
    server._request_cancel("unrelated")
    assert not server.cancel_event.is_set()
    server._request_cancel("b")
    assert server.cancel_event.is_set()
    assert {"a", "b"} <= server.pending_cancel_job_ids


def test_photos_the_lanes_hand_back_run_as_normal_jobs_in_order(monkeypatch, capsys):
    import localsr.worker.image_batch as batch_module
    from localsr.protocol.messages import JobCompleted

    server = WorkerServer()
    jobs = _batch_jobs(3)
    ran = []

    def lanes(runner, items):
        runner.emit(JobCompleted(job_id=items[0]["job_id"], elapsed_seconds=1.0))
        return items[1:]

    def run_job(job_id, data):
        assert server.active_job_id == job_id and server.active_batch_job_ids
        ran.append(job_id)
        server_module.send_message(JobCompleted(job_id=job_id, elapsed_seconds=1.0))

    import localsr.worker.server as server_module

    monkeypatch.setattr(batch_module.ImageBatchRunner, "run", lanes)
    monkeypatch.setattr(server, "_run_job", run_job)
    server._run_image_batch(jobs)
    assert ran == ["photo-1", "photo-2"]
    sent = [(m["type"], m["data"]["job_id"]) for m in _sent(capsys)]
    assert sent == [
        ("job_completed", "photo-0"),
        ("job_started", "photo-1"),
        ("job_completed", "photo-1"),
        ("job_started", "photo-2"),
        ("job_completed", "photo-2"),
    ]
    assert server.active_job_id is None and not server.active_batch_job_ids
    assert not server.cancel_event.is_set()


def test_a_photo_that_fell_back_after_it_was_announced_is_not_started_twice(monkeypatch, capsys):
    import localsr.worker.image_batch as batch_module
    import localsr.worker.server as server_module
    from localsr.protocol.messages import JobCompleted

    server = WorkerServer()

    def lanes(runner, items):
        runner.announce(items[0])  # the photo started on the lanes, then fell back
        return items

    def run_job(job_id, data):
        server_module.send_message(JobCompleted(job_id=job_id, elapsed_seconds=1.0))

    monkeypatch.setattr(batch_module.ImageBatchRunner, "run", lanes)
    monkeypatch.setattr(server, "_run_job", run_job)
    server._run_image_batch(_batch_jobs(2))
    started = [m["data"]["job_id"] for m in _sent(capsys) if m["type"] == "job_started"]
    assert started == ["photo-0", "photo-1"]


def test_cancelling_a_batch_also_cancels_the_photos_left_for_the_normal_path(monkeypatch, capsys):
    import localsr.worker.image_batch as batch_module
    import localsr.worker.server as server_module
    from localsr.protocol.messages import JobCancelled, JobCompleted

    server = WorkerServer()
    monkeypatch.setattr(batch_module.ImageBatchRunner, "run", lambda runner, items: items)

    def run_job(job_id, data):
        if job_id == "photo-0":
            server._request_cancel("photo-0")  # The reader thread, mid-photo.
        message = JobCancelled if server.cancel_event.is_set() else JobCompleted
        server_module.send_message(
            message(job_id=job_id)
            if message is JobCancelled
            else message(job_id=job_id, elapsed_seconds=1.0)
        )

    monkeypatch.setattr(server, "_run_job", run_job)
    server._run_image_batch(_batch_jobs(3))
    terminal = [
        (m["type"], m["data"]["job_id"]) for m in _sent(capsys) if m["type"] != "job_started"
    ]
    assert terminal == [("job_cancelled", f"photo-{index}") for index in range(3)]
    assert not server.cancel_event.is_set() and not server.pending_cancel_job_ids


def test_an_unexpected_batch_error_fails_the_started_photos_and_runs_the_others_alone(
    monkeypatch, capsys
):
    import localsr.worker.image_batch as batch_module
    import localsr.worker.server as server_module
    from localsr.protocol.messages import JobCompleted, JobStarted

    server = WorkerServer()

    def broken(runner, items):
        runner.emit(JobStarted(job_id=items[0]["job_id"]))
        runner.emit(JobCompleted(job_id=items[0]["job_id"], elapsed_seconds=1.0))
        runner.emit(JobStarted(job_id=items[1]["job_id"]))
        raise RuntimeError("scheduler bug")

    def run_job(job_id, data):
        server_module.send_message(JobCompleted(job_id=job_id, elapsed_seconds=1.0))

    monkeypatch.setattr(batch_module.ImageBatchRunner, "run", broken)
    monkeypatch.setattr(server, "_run_job", run_job)
    server._run_image_batch(_batch_jobs(3))
    sent = [(m["type"], m["data"]["job_id"]) for m in _sent(capsys) if "job_id" in m["data"]]
    assert sent == [
        ("job_started", "photo-0"),
        ("job_completed", "photo-0"),
        ("job_started", "photo-1"),
        ("job_failed", "photo-1"),
        # A photo that never started runs as an ordinary job, as in the queue before.
        ("job_started", "photo-2"),
        ("job_completed", "photo-2"),
    ]
    assert server.active_job_id is None and not server.active_batch_job_ids


def test_a_lost_device_in_a_batch_leaves_the_unstarted_photos_queued(monkeypatch, capsys):
    import localsr.worker.image_batch as batch_module
    import localsr.worker.server as server_module
    from localsr.protocol.messages import JobStarted

    server = WorkerServer()
    exits = []

    def lost(runner, items):
        runner.emit(JobStarted(job_id=items[0]["job_id"]))
        raise RuntimeError("device lost")

    monkeypatch.setattr(batch_module.ImageBatchRunner, "run", lost)
    monkeypatch.setattr(server_module, "_device_lost", lambda error: True)
    monkeypatch.setattr(server_module.os, "_exit", lambda code: exits.append(code))
    monkeypatch.setattr(server, "_run_job", lambda *_: pytest.fail("ran on a lost device"))
    server._run_image_batch(_batch_jobs(3))
    sent = [(m["type"], m["data"].get("job_id")) for m in _sent(capsys) if m["type"] != "log"]
    # Only the started photo fails; the engine restarts and the desktop sends the rest again.
    assert sent == [("job_started", "photo-0"), ("job_failed", "photo-0")]
    assert exits


@pytest.mark.parametrize(
    "jobs",
    [
        [{"job_id": "only"}],
        [{"job_id": "same"}, {"job_id": "same"}],
        [{"job_id": "named"}, {"image_path": "unnamed.png"}],
        [{"job_id": f"many-{index}"} for index in range(65)],
    ],
    ids=["one", "duplicate", "unnamed", "too-many"],
)
def test_a_malformed_batch_fails_each_photo_it_names(capsys, jobs):
    server = WorkerServer()
    server._run_image_batch(jobs)
    named = {job["job_id"] for job in jobs if "job_id" in job}
    failed = {m["data"]["job_id"] for m in _sent(capsys) if m["type"] == "job_failed"}
    assert failed == named
    assert server.active_job_id is None and not server.active_batch_job_ids


def test_the_engine_offers_photo_batches():
    from localsr.worker.server import _engine_features

    assert "image_batch_v1" in _engine_features()
