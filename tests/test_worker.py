import json
import threading

import pytest

from localsr.protocol.messages import JobRequest
from localsr.worker.server import WorkerServer


class DummyModelAdapter:
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
    ):
        progress_callback(0, 4, tile_size)
        import time

        for i in range(4):
            time.sleep(0.05)
            if cancel_event.is_set():
                raise InterruptedError()
            progress_callback(i + 1, 4, tile_size)
        return DummyWriter()


def test_worker_cancellation(monkeypatch, capsys):
    # We will feed stdin manually
    server = WorkerServer()
    server.model_adapter = DummyModelAdapter()
    server.engine = DummyEngine()

    # Fake image IO
    from localsr.core.image_io import ImageManager

    def fake_load(*args):
        return {"tensor": None}

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

    import time

    def delayed_cancel():
        time.sleep(0.08)
        server.cancel_event.set()
        time.sleep(0.1)
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
        t.join()

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
            "tensor": None,
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


def test_worker_samples_progressive_tile_jpegs_for_desktop_requests(monkeypatch):
    from types import SimpleNamespace

    import localsr.worker.server as server_module
    from localsr.core.image_io import ImageManager

    server = WorkerServer()
    server.model_adapter = DummyModelAdapter()
    writer = DummyWriter()

    class FastTiledEngine:
        def process_image(self, **kwargs):
            callback = kwargs["tile_callback"]
            tile = SimpleNamespace(out_x=0, out_y=0, out_w=16, out_h=16)
            for completed in range(1, 11):
                callback("completed", tile, object(), completed, 10, 160, 16, 64)
            return writer

    encoded = []
    server.engine = FastTiledEngine()
    monkeypatch.setattr(ImageManager, "load", lambda *_: {"tensor": None})
    monkeypatch.setattr(ImageManager, "save", lambda *_, **__: None)
    monkeypatch.setattr(
        server_module,
        "_encode_chw_jpeg",
        lambda *_args, **_kwargs: encoded.append(True) or "preview",
    )

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
            "preview_interval_ms": 1000,
        },
    )

    # The first tile makes the preview visible and the final tile is forced;
    # rapid intermediate tiles do not monopolize the UI transport.
    assert len(encoded) == 2


def test_worker_memmap_cleanup_on_error(monkeypatch):
    server = WorkerServer()
    server.model_adapter = DummyModelAdapter()

    writer_instance = DummyWriter()

    class InstantEngine:
        def process_image(self, **kwargs):
            return writer_instance

    server.engine = InstantEngine()

    from localsr.core.image_io import ImageManager

    def fake_load(self, path):
        return {"tensor": None}

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
