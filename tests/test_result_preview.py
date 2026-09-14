import base64
import io
import json
import queue
import threading

from PIL import Image

from localsr.protocol.messages import PreviewReady
from localsr.worker.result_preview import ResultPreviewService, render_result_preview


def test_preview_preserves_oriented_dimensions_with_bounded_rgb_pixels(tmp_path):
    image = Image.new("RGBA", (257, 129), (180, 40, 20, 100))
    exif = image.getexif()
    exif[274] = 6
    path = tmp_path / "oriented.png"
    image.save(path, exif=exif)
    result = render_result_preview({"image_path": str(path), "max_dimension": 96})
    assert isinstance(result, PreviewReady)
    assert (result.width, result.height) == (129, 257)
    with Image.open(io.BytesIO(base64.b64decode(result.jpeg_base64))) as preview:
        assert preview.size == (48, 96)
        assert preview.mode == "RGB"


def test_latest_selection_has_priority_over_bounded_automatic_previews(monkeypatch):
    import localsr.worker.result_preview as module

    entered, release = threading.Event(), threading.Event()
    emitted = queue.Queue()

    def render(data):
        if data["image_path"] == "busy":
            entered.set()
            assert release.wait(5)
        return data["image_path"]

    monkeypatch.setattr(module, "render_result_preview", render)
    service = ResultPreviewService(emitted.put)
    try:
        service.submit({"image_path": "busy"})
        assert entered.wait(5)
        service.submit({"image_path": "old selection", "comparison": True})
        service.submit({"image_path": "current selection", "comparison": True})
        for index in range(100):
            service.submit({"image_path": f"automatic {index}"})
        assert len(service.pending) == 2
        release.set()
        assert [emitted.get(timeout=5) for _ in range(3)] == [
            "busy",
            "current selection",
            "automatic 99",
        ]
    finally:
        release.set()
        service.close()
    assert not service.thread.is_alive()


def test_result_preview_and_cancellation_arrive_while_inference_is_blocked(tmp_path, monkeypatch):
    import localsr.worker.server as module

    requests, emitted = queue.Queue(), queue.Queue()
    entered, release = threading.Event(), threading.Event()

    def incoming():
        while (message := requests.get()) is not None:
            yield json.dumps(message) + "\n"

    monkeypatch.setattr(module.sys, "stdin", incoming())
    monkeypatch.setattr(module, "send_message", lambda msg: emitted.put(json.loads(msg.to_json())))
    server = module.WorkerServer()

    def blocked_job(*_):
        entered.set()
        assert release.wait(10)

    monkeypatch.setattr(server, "_run_job", blocked_job)
    worker = threading.Thread(target=server.run)
    path = tmp_path / "completed.png"
    Image.new("RGB", (200, 100), "red").save(path)
    worker.start()
    try:
        requests.put({"type": "job_request", "data": {"job_id": "slow-model"}})
        assert entered.wait(5)
        requests.put(
            {
                "type": "preview_request",
                "data": {
                    "image_path": str(path),
                    "comparison": True,
                    "max_dimension": 96,
                },
            }
        )
        while (message := emitted.get(timeout=5))["type"] != "preview_ready":
            pass
        assert message["data"]["image_path"] == str(path)
        assert server.active_job_id == "slow-model"
        assert not release.is_set()
        requests.put({"type": "cancel_request", "data": {"job_id": "slow-model"}})
        assert server.cancel_event.wait(5)
    finally:
        release.set()
        requests.put({"type": "shutdown_request", "data": {}})
        requests.put(None)
        worker.join(timeout=5)
        server.result_previews.close()
    assert not worker.is_alive()


def test_preview_failure_keeps_the_original_path(tmp_path):
    result = render_result_preview({"image_path": str(tmp_path / "missing.png")})
    assert result.image_path.endswith("missing.png")
    assert result.error_message
