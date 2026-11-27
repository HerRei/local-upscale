import threading

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
    received = threading.Event()

    def emit(packet):
        emitted.append(packet)
        if len(emitted) == 2:
            received.set()

    def blocked_encode(pixels, max_dimension, quality):
        encoding_started.set()
        assert release_encoder.wait(timeout=10)
        return str(int(pixels[0, 0, 0]))

    monkeypatch.setattr(preview_module, "encode_preview_jpeg", blocked_encode)
    encoder = LatestPreviewEncoder(emit, max_fps=5)
    assert encoder.submit(job_id="job", preview_kind="video", pixels=np.full((3, 4, 4), 1))
    assert encoding_started.wait(timeout=5)

    submitted = threading.Event()

    def produce():
        for value in (2, 3, 4):
            encoder.submit(
                job_id="job",
                preview_kind="video",
                pixels=np.full((3, 4, 4), value),
                force=True,
            )
        submitted.set()

    producer = threading.Thread(target=produce)
    producer.start()
    try:
        assert submitted.wait(timeout=5), "producer blocked on the encoder"
        assert encoder._queue.qsize() <= 1
    finally:
        release_encoder.set()
        producer.join(timeout=5)
    assert not producer.is_alive()
    assert received.wait(timeout=5)
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
    assert encoding_started.wait(timeout=5)
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


# Steps: the picture each step before the main model finishes with (stage_results).


class _StepWriter:
    def __init__(self, array):
        self.array = array
        self.shape = array.shape

    def get_array(self):
        return self.array

    def cleanup(self):
        pass


class _StepAdapter:
    allow_unverified_checkpoints = False

    def inspect(self, path):
        from localsr.core.model_adapter import NormalizedModelInfo

        if path.endswith("colour.pth"):
            return NormalizedModelInfo("DDColor", 1, 3, 3, True, False, 1, 1, path, [])
        scale = 1 if path.endswith("restore.pth") else 2
        return NormalizedModelInfo("Test", scale, 3, 3, True, False, 1, 1, path, [])

    def release(self):
        pass


class _StepEngine:
    """Every model returns a flat picture of its own grey, at the model's scale."""

    def __init__(self, unstable=()):
        self.unstable = set(unstable)
        self.calls = []

    def process_image(self, **kwargs):
        from localsr.core.inference import UnstableRestorationError

        path = kwargs["model_path"]
        self.calls.append(path)
        if path in self.unstable:
            raise UnstableRestorationError("NAFNet produced unstable values")
        tensor = kwargs["img_data"]["tensor"]
        scale = kwargs["model_info"].scale
        kwargs["progress_callback"](1, 1, kwargs["tile_size"])
        return _StepWriter(
            np.full(
                (3, int(tensor.shape[1]) * scale, int(tensor.shape[2]) * scale),
                40 * len(self.calls),
                dtype=np.uint8,
            )
        )

    def release_model(self, path=None):
        pass

    def release_models_except(self, keep):
        pass


def _step_job(tmp_path, fixes, **extra):
    """A photo job: the fixes as (kind, model_id, fix, file name), then a 2x upscale."""
    data = {
        "job_id": "steps-job",
        "image_path": str(tmp_path / "input.png"),
        "model_path": str(tmp_path / "upscale.pth"),
        "output_path": str(tmp_path / "output.png"),
        "scratch_directory": str(tmp_path / "scratch"),
        "output_format": "png",
        "device": "cpu",
        "precision": "fp32",
        "tile_size": 512,
        "halo": 8,
        "jpeg_quality": 95,
        "preserve_metadata": True,
        "safe_memory": True,
        "output_scale": 2,
        "preview_enabled": True,
        "preview_max_dimension": 320,
        "stages": [
            {
                "kind": kind,
                "role": "fix",
                "fix": fix,
                "model_id": model_id,
                "model_path": str(tmp_path / name),
            }
            for kind, model_id, fix, name in fixes
        ]
        + [
            {
                "kind": "upscale",
                "role": "primary",
                "model_id": "upscale",
                "model_path": str(tmp_path / "upscale.pth"),
            }
        ],
        **extra,
    }
    return data


def _run_step_job(tmp_path, monkeypatch, capsys, data, *, width=3000, height=2000, unstable=()):
    import json

    import torch

    from localsr.core.image_io import ImageManager
    from localsr.worker.server import WorkerServer

    server = WorkerServer()
    server.model_adapter = _StepAdapter()
    server.engine = _StepEngine(unstable=[str(tmp_path / name) for name in unstable])
    monkeypatch.setattr(
        ImageManager,
        "load",
        lambda *_args, **_kwargs: {
            "tensor": torch.full((3, height, width), 0.5),
            "safe_exif": {},
            "icc_profile": None,
        },
    )
    monkeypatch.setattr(ImageManager, "save", lambda *_args, **_kwargs: None)
    server._run_job(data["job_id"], data)
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


_TWO_FIXES = [
    ("deblock", "fbcnn_color", "jpeg", "jpeg-restore.pth"),
    ("restore", "nafnet_sidd_width64", "noise", "noise-restore.pth"),
]


def _positions(messages, kind):
    return {
        message["data"]["stage_index"]: index
        for index, message in enumerate(messages)
        if message["type"] == kind
    }


def test_each_fix_before_the_upscale_sends_its_picture_at_viewer_size(
    tmp_path, monkeypatch, capsys
):
    import base64
    import io

    data = _step_job(tmp_path, _TWO_FIXES, stage_picture_max_dimension=2048)
    messages = _run_step_job(tmp_path, monkeypatch, capsys, data)

    results = [m["data"] for m in messages if m["type"] == "stage_result"]
    assert [result["stage_index"] for result in results] == [0, 1]
    started = _positions(messages, "stage_started")
    sent = _positions(messages, "stage_result")
    for index in (0, 1):
        # The picture belongs to its step and arrives before the next one starts.
        assert started[index] < sent[index] < started[index + 1]
    for result, (_, _, fix, _) in zip(results, _TWO_FIXES, strict=True):
        assert result["job_id"] == "steps-job"
        assert result["stage_count"] == 3
        assert (result["step"], result["fix"]) == (fix, fix)
        assert result["skipped"] is False and result["note"] == ""
        assert (result["width"], result["height"]) == (3000, 2000)
        assert (result["preview_width"], result["preview_height"]) == (2048, 1365)
        assert result["image_path"] == ""
        with Image.open(io.BytesIO(base64.b64decode(result["jpeg_base64"]))) as picture:
            assert picture.format == "JPEG"
            assert picture.size == (2048, 1365)
    # The picture is the step's own result, not its input.
    first = Image.open(io.BytesIO(base64.b64decode(results[0]["jpeg_base64"])))
    second = Image.open(io.BytesIO(base64.b64decode(results[1]["jpeg_base64"])))
    assert abs(int(np.asarray(first).mean()) - 40) <= 2
    assert abs(int(np.asarray(second).mean()) - 80) <= 2
    # The stage messages name the step and the fix.
    stage_started = [m["data"] for m in messages if m["type"] == "stage_started"]
    assert [(m["step"], m["fix"]) for m in stage_started] == [
        ("jpeg", "jpeg"),
        ("noise", "noise"),
        ("upscale", ""),
    ]
    stage_completed = [m["data"] for m in messages if m["type"] == "stage_completed"]
    assert [(m["step"], m["fix"]) for m in stage_completed] == [
        ("jpeg", "jpeg"),
        ("noise", "noise"),
        ("upscale", ""),
    ]
    assert messages[-1]["type"] == "job_completed"


def test_without_the_field_a_job_sends_the_messages_of_today(tmp_path, monkeypatch, capsys):
    def shape(messages):
        # Live previews are sampled by the encoder thread; the job's own messages are not.
        return [
            (message["type"], message["data"].get("stage_index"))
            for message in messages
            if message["type"] not in ("live_preview_frame", "stage_result")
        ]

    without = _run_step_job(tmp_path, monkeypatch, capsys, _step_job(tmp_path, _TWO_FIXES))
    assert not [m for m in without if m["type"] == "stage_result"]
    with_pictures = _run_step_job(
        tmp_path,
        monkeypatch,
        capsys,
        _step_job(tmp_path, _TWO_FIXES, stage_picture_max_dimension=2048),
    )
    assert shape(without) == shape(with_pictures)
    # The 320 px full-image picture of each fix is still sent either way.
    for messages in (without, with_pictures):
        whole = [
            m["data"]["stage_index"]
            for m in messages
            if m["type"] == "live_preview_frame"
            and m["data"]["output_width"] == m["data"]["image_width"]
        ]
        assert whole == [0, 1]


def test_colour_as_a_step_sends_its_picture_but_still_no_small_full_image_tile(
    tmp_path, monkeypatch, capsys
):
    data = _step_job(
        tmp_path,
        [("restore", "ddcolor_modelscope", "colour", "colour.pth")],
        stage_picture_max_dimension=2048,
    )
    messages = _run_step_job(tmp_path, monkeypatch, capsys, data, width=640, height=480)
    results = [m["data"] for m in messages if m["type"] == "stage_result"]
    assert len(results) == 1
    assert results[0]["step"] == "colour" and not results[0]["skipped"]
    assert results[0]["jpeg_base64"]
    # Below the limit the picture keeps its size.
    assert (results[0]["preview_width"], results[0]["preview_height"]) == (640, 480)
    assert not [
        m for m in messages if m["type"] == "live_preview_frame" and m["data"]["stage_index"] == 0
    ]


def test_an_unstable_blur_fix_sends_a_skipped_result_without_a_picture(
    tmp_path, monkeypatch, capsys
):
    fixes = [
        ("restore", "nafnet_gopro_deblur", "blur", "blur-restore.pth"),
        ("restore", "nafnet_sidd_width64", "noise", "noise-restore.pth"),
    ]
    data = _step_job(tmp_path, fixes, stage_picture_max_dimension=1024)
    messages = _run_step_job(
        tmp_path, monkeypatch, capsys, data, width=800, height=600, unstable=["blur-restore.pth"]
    )
    blur, noise = [m["data"] for m in messages if m["type"] == "stage_result"]
    assert blur["skipped"] is True
    assert "was skipped for this image" in blur["note"]
    assert (blur["jpeg_base64"], blur["width"], blur["preview_width"]) == ("", 0, 0)
    # The next step goes on with a picture of its own.
    assert noise["skipped"] is False and noise["jpeg_base64"]
    assert (noise["preview_width"], noise["preview_height"]) == (800, 600)
    assert messages[-1]["type"] == "job_completed"
