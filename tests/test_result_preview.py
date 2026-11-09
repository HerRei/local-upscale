import base64
import io
import json
import os
import queue
import sys
import threading

import numpy as np
import pytest
from PIL import Image

from localsr.protocol.messages import PreviewFailed, PreviewReady
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


@pytest.mark.parametrize("extension", ["png", "jpg", "tiff", "webp"])
def test_large_export_previews_in_a_child_without_changing_input_limits(
    tmp_path, monkeypatch, extension
):
    path = tmp_path / f"finished.{extension}"
    Image.new("RGB", (320, 160), (180, 40, 20)).save(path)
    original = path.read_bytes()
    # Reproduce the same rejection as a 24 MP photo enlarged 4×, without a
    # several-hundred-megapixel allocation in the routine test suite.
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 10000)
    with pytest.raises(Image.DecompressionBombError):
        Image.open(path)
    result = render_result_preview({"image_path": str(path), "max_dimension": 96})
    assert isinstance(result, PreviewReady)
    assert (result.width, result.height) == (320, 160)
    assert Image.MAX_IMAGE_PIXELS == 10000
    with pytest.raises(Image.DecompressionBombError):
        Image.open(path)
    with Image.open(io.BytesIO(base64.b64decode(result.jpeg_base64))) as preview:
        assert preview.size == (96, 48)
    assert path.read_bytes() == original


def test_large_preview_decoder_failure_is_nonfatal(tmp_path, monkeypatch):
    import subprocess

    path = tmp_path / "finished.png"
    Image.new("RGB", (320, 160)).save(path)
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 10000)

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 180)

    monkeypatch.setattr(subprocess, "run", timeout)
    result = render_result_preview({"image_path": str(path)})
    assert isinstance(result, PreviewFailed)
    assert result.image_path == str(path)
    assert Image.MAX_IMAGE_PIXELS == 10000


def test_revisiting_result_reuses_preview_until_file_changes(tmp_path, monkeypatch):
    import localsr.worker.result_preview as module

    path = tmp_path / "finished.png"
    Image.new("RGB", (320, 160), "red").save(path)
    emitted = queue.Queue()
    calls = []
    real_render = module.render_result_preview

    def render(data):
        calls.append(data)
        return real_render(data)

    monkeypatch.setattr(module, "render_result_preview", render)
    service = ResultPreviewService(emitted.put)
    request = {"image_path": str(path), "max_dimension": 96}
    try:
        service.submit(request)
        first = emitted.get(timeout=5)
        service.submit({**request, "comparison": True})
        assert emitted.get(timeout=5) == first
        assert len(calls) == 1
        Image.new("RGB", (320, 160), "blue").save(path)
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
        service.submit(request)
        assert emitted.get(timeout=5).jpeg_base64 != first.jpeg_base64
        assert len(calls) == 2
    finally:
        service.close()


def test_pair_slots_coalesce_by_generation_without_displacing_either_side(monkeypatch):
    import localsr.worker.result_preview as module

    entered, release = threading.Event(), threading.Event()
    emitted = queue.Queue()

    def render(data):
        if data["image_path"] == "busy":
            entered.set()
            assert release.wait(5)
        return PreviewFailed(image_path=data["image_path"], error_message="missing")

    monkeypatch.setattr(module, "render_result_preview", render)
    service = ResultPreviewService(emitted.put)
    try:
        service.submit({"image_path": "busy"})
        assert entered.wait(5)
        for generation in range(1, 101):
            for slot in ("showing", "against"):
                service.submit(
                    {
                        "image_path": f"{slot}-{generation}",
                        "comparison": True,
                        "slot": slot,
                        "generation": generation,
                        "target_id": slot,
                    }
                )
        service.submit({"image_path": "automatic"})
        assert len(service.pending) == 3
        release.set()
        results = [emitted.get(timeout=5) for _ in range(4)]
        pair = {result.slot: result for result in results if result.slot}
        assert set(pair) == {"showing", "against"}
        for slot, result in pair.items():
            assert result.image_path == f"{slot}-100"
            assert result.generation == 100
            assert result.target_id == slot
    finally:
        release.set()
        service.close()


def test_two_cached_previews_echo_current_identity_and_decoded_dimensions(tmp_path, monkeypatch):
    import localsr.worker.result_preview as module

    emitted, calls = queue.Queue(), []
    real_render = module.render_result_preview

    def render(data):
        calls.append(data)
        return real_render(data)

    monkeypatch.setattr(module, "render_result_preview", render)
    service = ResultPreviewService(emitted.put)
    try:
        for generation in (1, 2):
            for index, slot in enumerate(("showing", "against")):
                path = tmp_path / f"{index}.png"
                if generation == 1:
                    Image.new("RGB", (320, 160), "red").save(path)
                service.submit(
                    {
                        "image_path": str(path),
                        "max_dimension": 96,
                        "comparison": True,
                        "slot": slot,
                        "generation": generation,
                        "target_id": f"job-{index}",
                    }
                )
                result = emitted.get(timeout=5)
                assert result.slot == slot
                assert result.generation == generation
                assert result.target_id == f"job-{index}"
                assert (result.preview_width, result.preview_height) == (96, 48)
        assert len(calls) == 2
        assert len(service.cache) <= 3
    finally:
        service.close()


def test_a_newer_pair_drops_older_pending_sides_and_late_older_requests(monkeypatch):
    import localsr.worker.result_preview as module

    entered, release = threading.Event(), threading.Event()
    calls, emitted = [], queue.Queue()

    def render(data):
        calls.append(data["image_path"])
        if data["image_path"] == "busy":
            entered.set()
            assert release.wait(5)
        return PreviewFailed(image_path=data["image_path"], error_message="missing")

    monkeypatch.setattr(module, "render_result_preview", render)
    service = ResultPreviewService(emitted.put)
    pair = {"comparison": True, "target_id": "job"}
    try:
        service.submit({"image_path": "busy"})
        assert entered.wait(5)
        service.submit({**pair, "image_path": "old showing", "slot": "showing", "generation": 1})
        service.submit({**pair, "image_path": "old against", "slot": "against", "generation": 1})
        # Generation 2 needs only its showing side decoded.
        service.submit({**pair, "image_path": "new showing", "slot": "showing", "generation": 2})
        service.submit({**pair, "image_path": "late", "slot": "against", "generation": 1})
        assert set(service.pending) == {"showing"}
        release.set()
        emitted.get(timeout=5)
        answer = emitted.get(timeout=5)
        assert (answer.image_path, answer.slot, answer.generation) == ("new showing", "showing", 2)
        assert calls == ["busy", "new showing"]
    finally:
        release.set()
        service.close()


def test_a_legacy_selection_keeps_its_own_slot_and_answers_without_identity(monkeypatch):
    import localsr.worker.result_preview as module

    emitted = queue.Queue()
    monkeypatch.setattr(
        module,
        "render_result_preview",
        lambda data: PreviewFailed(image_path=data["image_path"], error_message="missing"),
    )
    service = ResultPreviewService(emitted.put)
    try:
        service.submit({"image_path": "chosen", "comparison": True})
        answer = emitted.get(timeout=5)
        assert (answer.slot, answer.generation, answer.target_id) == ("", 0, "")
    finally:
        service.close()


def test_large_result_child_and_its_timeout_answer_with_the_request_identity(tmp_path, monkeypatch):
    import subprocess

    path = tmp_path / "finished.png"
    Image.new("RGB", (320, 160), (180, 40, 20)).save(path)
    # Larger than the decoder's input limit, as a 4x export of a camera photo is.
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 10000)
    emitted = queue.Queue()
    service = ResultPreviewService(emitted.put)
    request = {
        "image_path": str(path),
        "max_dimension": 96,
        "comparison": True,
        "slot": "against",
        "generation": 7,
        "target_id": "job-large",
    }
    try:
        service.submit(request)
        ready = emitted.get(timeout=60)
        assert isinstance(ready, PreviewReady)
        assert (ready.slot, ready.generation, ready.target_id) == ("against", 7, "job-large")
        assert (ready.width, ready.height, ready.preview_width, ready.preview_height) == (
            320,
            160,
            96,
            48,
        )
        Image.new("RGB", (320, 160), "blue").save(path)
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

        def timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired(args[0], 180)

        monkeypatch.setattr(subprocess, "run", timeout)
        service.submit({**request, "generation": 8, "slot": "showing"})
        failed = emitted.get(timeout=5)
        assert isinstance(failed, PreviewFailed)
        assert (failed.slot, failed.generation, failed.target_id) == ("showing", 8, "job-large")
    finally:
        service.close()


def test_four_compare_slots_coalesce_and_keep_request_identity(monkeypatch):
    import localsr.worker.result_preview as module

    entered, release = threading.Event(), threading.Event()
    emitted = queue.Queue()

    def render(data):
        if data["image_path"] == "busy":
            entered.set()
            assert release.wait(5)
        return PreviewFailed(image_path=data["image_path"], error_message="missing")

    monkeypatch.setattr(module, "render_result_preview", render)
    service = ResultPreviewService(emitted.put)
    try:
        service.submit({"image_path": "busy"})
        assert entered.wait(5)
        for slot in ("slot1", "slot2", "slot3", "slot4"):
            service.submit(
                {
                    "image_path": slot,
                    "comparison": True,
                    "slot": slot,
                    "generation": 3,
                    "target_id": slot,
                }
            )
        assert len(service.pending) == 4
        release.set()
        emitted.get(timeout=5)
        answers = [emitted.get(timeout=5) for _ in range(4)]
        assert {answer.slot for answer in answers} == {"slot1", "slot2", "slot3", "slot4"}
        assert all(answer.generation == 3 and answer.target_id == answer.slot for answer in answers)
    finally:
        release.set()
        service.close()


def test_preview_cache_is_bounded_by_bytes(tmp_path, monkeypatch):
    import localsr.worker.result_preview as module

    monkeypatch.setattr(module, "_CACHE_BYTES", 100)
    emitted = queue.Queue()
    service = ResultPreviewService(emitted.put)
    try:
        path = tmp_path / "image.png"
        Image.new("RGB", (200, 100), "red").save(path)
        service.submit({"image_path": str(path), "max_dimension": 96})
        assert isinstance(emitted.get(timeout=5), PreviewReady)
        assert sum(len(item.jpeg_base64) for item in service.cache.values()) <= 100
    finally:
        service.close()


@pytest.mark.skipif(sys.platform != "darwin", reason="HEIC is decoded by macOS")
def test_a_heic_export_previews(tmp_path):
    from localsr.core.image_io import ImageManager

    path = tmp_path / "finished.heic"
    ImageManager().save_from_writer(
        np.full((3, 80, 120), 128, dtype=np.uint8), str(path), "heic", 90, True, None, {}, 1
    )
    result = render_result_preview({"image_path": str(path), "max_dimension": 64})
    assert isinstance(result, PreviewReady)
    assert (result.width, result.height) == (120, 80)
