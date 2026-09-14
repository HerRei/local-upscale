import threading
import time

import numpy as np
from PIL import Image

import localsr.core.live_preview as preview_module
from localsr.core.live_preview import LatestPreviewEncoder, encode_preview_jpeg


def test_preview_encoding_is_bounded_and_decodable():
    pixels = np.zeros((3, 900, 1200), dtype=np.uint8)
    pixels[0] = 220
    encoded = encode_preview_jpeg(pixels, max_dimension=320, quality=68)
    import base64
    import io

    with Image.open(io.BytesIO(base64.b64decode(encoded))) as image:
        assert max(image.size) == 320
        assert image.mode == "RGB"


def test_latest_frame_wins_without_blocking_producer(monkeypatch):
    encoding_started = threading.Event()
    release_encoder = threading.Event()
    emitted = []

    def blocked_encode(pixels, max_dimension, quality):
        encoding_started.set()
        release_encoder.wait(timeout=2)
        return str(int(pixels[0, 0, 0]))

    monkeypatch.setattr(preview_module, "encode_preview_jpeg", blocked_encode)
    encoder = LatestPreviewEncoder(emitted.append, max_fps=5)
    assert encoder.submit(job_id="job", preview_kind="video", pixels=np.full((3, 4, 4), 1))
    assert encoding_started.wait(timeout=1)

    started = time.perf_counter()
    for value in (2, 3, 4):
        encoder.submit(
            job_id="job",
            preview_kind="video",
            pixels=np.full((3, 4, 4), value),
            force=True,
        )
    producer_elapsed = time.perf_counter() - started
    assert producer_elapsed < 0.1
    assert encoder._queue.qsize() <= 1

    release_encoder.set()
    deadline = time.monotonic() + 2
    while len(emitted) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    encoder.close()
    assert emitted[-1].jpeg_base64 == "4"
    assert emitted[-1].sequence > emitted[0].sequence


def test_disabled_preview_does_no_copy_or_thread_work():
    encoder = LatestPreviewEncoder(lambda _packet: None, enabled=False)
    assert not encoder.submit(
        job_id="job",
        preview_kind="video",
        pixels=np.zeros((3, 10, 10), dtype=np.uint8),
    )
    assert encoder._thread is None


def test_close_discards_an_in_flight_preview_without_emitting(monkeypatch):
    encoding_started = threading.Event()
    release_encoder = threading.Event()
    emitted = []

    def blocked_encode(_pixels, max_dimension, quality):
        del max_dimension, quality
        encoding_started.set()
        release_encoder.wait(timeout=2)
        return "encoded"

    monkeypatch.setattr(preview_module, "encode_preview_jpeg", blocked_encode)
    encoder = LatestPreviewEncoder(emitted.append)
    encoder.submit(job_id="old-job", preview_kind="video", pixels=np.zeros((3, 4, 4)))
    assert encoding_started.wait(timeout=1)
    encoder.close()
    release_encoder.set()
    if encoder._thread is not None:
        encoder._thread.join(timeout=1)
    assert emitted == []
    assert encoder._queue.empty()


def test_late_first_tile_keeps_grid_and_pipeline_stage_through_worker_protocol():
    import json
    from unittest.mock import patch

    import localsr.worker.server as server_module
    from localsr.worker.server import WorkerServer

    events = []
    received = threading.Event()

    def emit(packet):
        with patch.object(
            server_module,
            "send_message",
            lambda message: events.append(json.loads(message.to_json())),
        ):
            WorkerServer._emit_live_preview(packet)
        received.set()

    encoder = LatestPreviewEncoder(emit)
    try:
        encoder.submit(
            job_id="late-hat",
            preview_kind="tile",
            pixels=np.zeros((3, 1024, 760), dtype=np.uint8),
            output_x=7168,
            output_y=4096,
            output_width=760,
            output_height=1024,
            image_width=7928,
            image_height=5444,
            active_tile_size=256,
            stage_index=1,
            force=True,
        )
        assert received.wait(timeout=2)
        data = events[0]["data"]
        assert data["active_tile_size"] == 256
        assert data["stage_index"] == 1
        assert data["output_width"] == 760 and data["image_width"] == 7928
        assert data["jpeg_base64"]
    finally:
        encoder.close()
