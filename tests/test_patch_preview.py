"""The preview patch: a square of one photo run through a plan, with its cache."""

from __future__ import annotations

import base64
import io
import json
import threading
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest
import spandrel.architectures.ESRGAN as E
import torch
from PIL import Image

from localsr.core.inference import InferenceEngine
from localsr.core.model_adapter import ModelAdapter
from localsr.worker import server as worker_server
from localsr.worker.server import WorkerServer

SIZE = (300, 260)  # width, height


def _checkpoint(path: Path, scale: int, seed: int) -> str:
    """A tiny ESRGAN that changes the picture visibly: untrained it answers near zero."""
    torch.manual_seed(seed)
    model = E.ESRGAN(in_nc=3, out_nc=3, num_filters=8, num_blocks=1, scale=scale)
    weights = model.state_dict()
    last = max(int(key.split(".")[1]) for key in weights if key.endswith("weight"))
    with torch.no_grad():
        weights[f"model.{last}.weight"].mul_(6)
        weights[f"model.{last}.bias"].add_(0.45)
    torch.save(weights, path)
    return str(path)


def _picture(path: Path, size: tuple[int, int] = SIZE, seed: int = 5) -> str:
    rng = np.random.default_rng(seed)
    width, height = size
    x = np.linspace(40, 210, width, dtype=np.float32)[None, :, None]
    y = np.linspace(0, 40, height, dtype=np.float32)[:, None, None]
    pixels = x + y + np.array([0, 12, 24], dtype=np.float32) + rng.normal(0, 14, (height, width, 3))
    Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8)).save(path)
    return str(path)


def _fix(model_path: str, fix: str = "noise") -> dict:
    return {
        "kind": "restore",
        "role": "fix",
        "fix": fix,
        "model_id": f"test-{fix}",
        "model_path": model_path,
    }


def _upscale(model_path: str) -> dict:
    return {
        "kind": "upscale",
        "role": "primary",
        "model_id": "test-upscale",
        "model_path": model_path,
    }


class Harness:
    def __init__(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setenv("LOCALSR_WORK_DIR", str(tmp_path / "work"))
        self.tmp = tmp_path
        self.server = WorkerServer()
        self.adapter = ModelAdapter(allow_unverified_checkpoints=True)
        self.engine = InferenceEngine(self.adapter)
        self.server.model_adapter = self.adapter
        self.server.engine = self.engine
        self.sent: list = []
        monkeypatch.setattr(worker_server, "send_message", self.sent.append)
        self.loads = 0
        original = self.engine.load_model

        def counted(*args, **kwargs):
            self.loads += 1
            return original(*args, **kwargs)

        monkeypatch.setattr(self.engine, "load_model", counted)
        self.noise = _checkpoint(tmp_path / "noise.pth", 1, 1)
        self.jpeg = _checkpoint(tmp_path / "jpeg.pth", 1, 2)
        self.up2 = _checkpoint(tmp_path / "up2.pth", 2, 3)
        self.up4 = _checkpoint(tmp_path / "up4.pth", 4, 4)
        self.image = _picture(tmp_path / "photo.png")

    def data(self, stages: list[dict], **fields) -> dict:
        data = {
            "patch_id": "p1",
            "slot": "a",
            "image_path": self.image,
            "frame_index": -1,
            "region": {"x": 40, "y": 30, "width": 192, "height": 192},
            "padding": 32,
            "stages": stages,
            "output_scale": 1,
            "device": "cpu",
            "precision": "fp32",
            "tile_size": 256,
            "halo": 16,
            "neural_engine": False,
            "safe_memory": True,
        }
        data.update(fields)
        return data

    def run(self, stages: list[dict], **fields) -> list[dict]:
        self.sent.clear()
        self.server._run_patch(self.data(stages, **fields))
        return [json.loads(message.to_json()) for message in self.sent]


@pytest.fixture
def harness(monkeypatch, tmp_path):
    return Harness(monkeypatch, tmp_path)


def _types(messages: list[dict]) -> list[str]:
    return [message["type"] for message in messages]


def _png(stage: dict) -> np.ndarray:
    data = base64.b64decode(stage["data"]["png_base64"])
    with Image.open(io.BytesIO(data)) as image:
        assert image.mode == "RGB"
        return np.asarray(image)


def _done(messages: list[dict]) -> list[dict]:
    return [m for m in messages if m["type"] == "patch_stage" and m["data"]["state"] == "done"]


def test_the_worker_lists_the_feature():
    from localsr.worker.server import _engine_features

    assert "patch_preview" in _engine_features([])


def test_one_noise_step_answers_with_a_png_of_the_region(harness):
    messages = harness.run(
        [_fix(harness.noise)], region={"x": 40, "y": 30, "width": 192, "height": 192}
    )
    assert _types(messages) == ["patch_started", "patch_stage", "patch_stage", "patch_completed"]
    running, done = messages[1]["data"], messages[2]
    assert (running["state"], running["stage_index"], running["stage_count"]) == ("running", 0, 1)
    assert done["data"]["cached"] is False
    assert (done["data"]["width"], done["data"]["height"]) == (192, 192)
    assert _png(done).shape == (192, 192, 3)
    assert done["data"]["elapsed_seconds"] > 0
    assert messages[3]["data"]["patch_id"] == "p1"


def test_the_full_size_region_is_384_square(harness):
    harness.image = _picture(harness.tmp / "big.png", (500, 450))
    messages = harness.run(
        [_fix(harness.noise)], region={"x": 20, "y": 10, "width": 384, "height": 384}
    )
    assert _png(_done(messages)[0]).shape == (384, 384, 3)


def test_noise_then_the_upscaler_grows_the_second_picture(harness):
    harness.image = _picture(harness.tmp / "big.png", (500, 450))
    messages = harness.run(
        [_fix(harness.noise), _upscale(harness.up4)],
        region={"x": 20, "y": 10, "width": 384, "height": 384},
        output_scale=4,
    )
    first, second = _done(messages)
    assert _png(first).shape == (384, 384, 3)
    assert _png(second).shape == (1536, 1536, 3)
    assert (second["data"]["stage_index"], second["data"]["stage_count"]) == (1, 2)


def test_an_output_size_below_the_native_scale_is_resized(harness):
    messages = harness.run([_upscale(harness.up4)], output_scale=2)
    assert _png(_done(messages)[0]).shape == (384, 384, 3)


def test_the_same_request_again_is_answered_from_the_cache_without_loading_a_model(harness):
    stages = [_fix(harness.noise), _upscale(harness.up2)]
    first = harness.run(stages, output_scale=2)
    loads = harness.loads
    assert loads >= 2
    second = harness.run(stages, output_scale=2)
    assert harness.loads == loads
    assert [m["data"]["cached"] for m in _done(second)] == [True, True]
    assert "running" not in [m["data"].get("state") for m in second if m["type"] == "patch_stage"]
    for before, after in zip(_done(first), _done(second), strict=True):
        assert before["data"]["png_base64"] == after["data"]["png_base64"]


def test_a_changed_order_reuses_nothing_and_an_old_prefix_is_still_cached(harness):
    noise, jpeg = _fix(harness.noise), _fix(harness.jpeg, "jpeg")
    harness.run([noise])
    reordered = harness.run([jpeg, noise])
    assert [m["data"]["cached"] for m in _done(reordered)] == [False, False]
    alone = harness.run([noise])
    assert [m["data"]["cached"] for m in _done(alone)] == [True]


def test_a_new_last_step_runs_only_itself(harness):
    noise = _fix(harness.noise)
    harness.run([noise])
    loads = harness.loads
    grown = harness.run([noise, _upscale(harness.up2)], output_scale=2)
    assert [m["data"]["cached"] for m in _done(grown)] == [True, False]
    # The upscaler loads; the fix does not.
    assert harness.loads == loads + 1


@pytest.mark.parametrize(
    "region",
    [
        {"x": 0, "y": 0, "width": 192, "height": 192},
        {"x": 108, "y": 68, "width": 192, "height": 192},
    ],
)
def test_a_region_at_a_corner_has_its_size(harness, region):
    messages = harness.run([_fix(harness.noise)], region=region)
    assert _types(messages)[-1] == "patch_completed"
    assert _png(_done(messages)[0]).shape == (192, 192, 3)


def test_a_region_past_the_edge_is_cut_to_the_picture(harness):
    messages = harness.run(
        [_fix(harness.noise)], region={"x": 200, "y": 200, "width": 384, "height": 384}
    )
    assert _png(_done(messages)[0]).shape == (60, 100, 3)


def test_a_region_outside_the_picture_fails(harness):
    messages = harness.run(
        [_fix(harness.noise)], region={"x": 400, "y": 0, "width": 64, "height": 64}
    )
    assert messages[-1]["type"] == "patch_failed"
    assert "outside" in messages[-1]["data"]["error_message"]


def test_the_patch_equals_the_same_pixels_of_a_job(harness):
    from localsr.worker.server import WorkerServer as Server

    size = (200, 200)
    harness.image = _picture(harness.tmp / "tile.png", size)
    output = harness.tmp / "job.png"
    job = {
        "job_id": "job",
        "image_path": harness.image,
        "model_path": "",
        "output_path": str(output),
        "scratch_directory": str(harness.tmp / "scratch"),
        "output_format": "png",
        "device": "cpu",
        "precision": "fp32",
        "tile_size": 256,
        "halo": 16,
        "jpeg_quality": 95,
        "preserve_metadata": False,
        "safe_memory": True,
        "output_scale": 1,
        "preview_enabled": False,
        "neural_engine": False,
        "stages": [_fix(harness.noise)],
    }
    Server._run_job(harness.server, "job", job)
    with Image.open(output) as result:
        expected = np.asarray(result.convert("RGB"))
    region = {"x": 68, "y": 68, "width": 64, "height": 64}
    messages = harness.run([_fix(harness.noise)], region=region)
    patch = _png(_done(messages)[0])
    window = expected[68:132, 68:132].astype(np.int16)
    assert np.abs(patch.astype(np.int16) - window).max() <= 1
    # The padding is what makes it so: with none, the square's edge sees a border.
    bare = _png(_done(harness.run([_fix(harness.noise)], region=region, padding=0))[0])
    assert np.abs(bare.astype(np.int16) - window).max() > 1


def test_a_masked_step_is_skipped_with_its_reason_and_the_picture_goes_on(
    harness, tmp_path, monkeypatch
):
    monkeypatch.setenv("LOCALSR_DEV_STEPS", "1")
    mask = tmp_path / "mask.png"
    Image.fromarray(np.full((8, 8), 255, dtype=np.uint8)).save(mask)
    masked = {"kind": "grain", "step": "grain", "mask_path": str(mask)}
    messages = harness.run([_upscale(harness.up2), masked], output_scale=2)
    skipped = [m for m in messages if m["data"].get("state") == "skipped"]
    assert len(skipped) == 1 and skipped[0]["data"]["note"]
    assert len(_done(messages)) == 1
    assert _png(_done(messages)[0]).shape == (384, 384, 3)


def test_a_face_stage_is_a_skipped_chip_after_the_upscaler(harness):
    face = {
        "kind": "face_restore",
        "model_id": "test-face",
        "model_path": harness.up2,
        "fidelity": 0.5,
        "execution": "fused-with-upscale",
    }
    messages = harness.run([_upscale(harness.up2), face], output_scale=2)
    stages = [m["data"] for m in messages if m["type"] == "patch_stage"]
    assert [(s["stage_index"], s["state"]) for s in stages if s["state"] != "running"] == [
        (0, "done"),
        (1, "skipped"),
    ]
    assert stages[-1]["stage_count"] == 2


def test_an_invalid_request_fails_with_its_patch_id(harness):
    data = harness.data([_fix(harness.noise)])
    data["slot"] = "c"
    harness.sent.clear()
    harness.server._run_patch(data)
    [message] = [json.loads(m.to_json()) for m in harness.sent]
    assert message["type"] == "patch_failed"
    assert message["data"]["patch_id"] == "p1"
    assert "slot" in message["data"]["error_message"]


def test_a_region_larger_than_the_protocol_allows_is_refused(harness):
    messages = harness.run(
        [_fix(harness.noise)], region={"x": 0, "y": 0, "width": 4096, "height": 64}
    )
    assert messages[-1]["type"] == "patch_failed"
    assert "width" in messages[-1]["data"]["error_message"]


# Colour -------------------------------------------------------------------------------


def _ddcolor_checkpoint(path: Path) -> str:
    from test_ddcolor import _save_small_checkpoint

    return str(_save_small_checkpoint(path))


def _colour_fix(path: str) -> dict:
    return {
        "kind": "restore",
        "role": "fix",
        "fix": "colour",
        "model_id": "test-colour",
        "model_path": path,
    }


def test_colour_runs_the_model_once_for_two_regions_of_one_file(harness, monkeypatch):
    from localsr.image_models.ddcolor import engine as dd_engine

    runs: list[tuple[int, int]] = []
    original = dd_engine.predict_ab

    def counted(model, rgb_float, **kwargs):
        runs.append(rgb_float.shape[:2])
        return original(model, rgb_float, **kwargs)

    monkeypatch.setattr(dd_engine, "predict_ab", counted)
    harness.image = _picture(harness.tmp / "wide.png", (900, 300))
    colour = _colour_fix(_ddcolor_checkpoint(harness.tmp / "ddcolor.pth"))
    first = harness.run([colour], region={"x": 10, "y": 10, "width": 200, "height": 200})
    second = harness.run([colour], region={"x": 600, "y": 40, "width": 200, "height": 200})
    assert [m["data"]["cached"] for m in _done(second)] == [False]
    assert len(runs) == 1
    # The whole picture, reduced to 512 on its long side, not the crop.
    assert runs[0] == (171, 512)
    for messages in (first, second):
        assert _png(_done(messages)[0]).shape == (200, 200, 3)


def test_colour_guesses_from_the_whole_picture_not_the_crop(harness, monkeypatch):
    """Two crops of one picture are coloured by one set of planes: where they overlap they
    agree, which a model run on each crop would not give."""
    harness.image = _picture(harness.tmp / "wide.png", (400, 300))
    colour = _colour_fix(_ddcolor_checkpoint(harness.tmp / "ddcolor.pth"))
    left = _png(
        _done(harness.run([colour], region={"x": 0, "y": 50, "width": 200, "height": 100}))[0]
    )
    right = _png(
        _done(harness.run([colour], region={"x": 100, "y": 50, "width": 200, "height": 100}))[0]
    )
    assert np.abs(left[:, 100:].astype(np.int16) - right[:, :100].astype(np.int16)).max() <= 1


# The Colour model between photos ----------------------------------------------------


def _count_loads(harness, monkeypatch) -> list[str]:
    loaded: list[str] = []
    original = harness.adapter.load

    def counted(path, *args, **kwargs):
        loaded.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(harness.adapter, "load", counted)
    return loaded


def _three_photos(harness) -> list[str]:
    return [
        _picture(harness.tmp / f"photo-{seed}.png", (420, 300), seed=seed) for seed in (11, 12, 13)
    ]


def test_colour_stays_loaded_so_a_new_photo_does_not_load_the_model_again(harness, monkeypatch):
    from localsr.worker import patch_preview

    monkeypatch.setattr(patch_preview, "colour_may_stay_loaded", lambda _device: True)
    loaded = _count_loads(harness, monkeypatch)
    path = _ddcolor_checkpoint(harness.tmp / "ddcolor.pth")
    colour = _colour_fix(path)
    for photo in _three_photos(harness):
        messages = harness.run([colour], image_path=photo)
        assert _types(messages)[-1] == "patch_completed"
    assert loaded == [path]
    assert path in harness.engine._loaded_models


def test_colour_is_loaded_again_where_the_device_has_no_room_to_keep_it(harness, monkeypatch):
    from localsr.worker import patch_preview

    monkeypatch.setattr(patch_preview, "colour_may_stay_loaded", lambda _device: False)
    loaded = _count_loads(harness, monkeypatch)
    path = _ddcolor_checkpoint(harness.tmp / "ddcolor.pth")
    for photo in _three_photos(harness)[:2]:
        harness.run([_colour_fix(path)], image_path=photo)
    assert loaded == [path, path]
    assert harness.engine._loaded_models == {}


def test_switching_the_patch_off_releases_the_colour_model(harness, monkeypatch):
    from localsr.worker import patch_preview

    monkeypatch.setattr(patch_preview, "colour_may_stay_loaded", lambda _device: True)
    path = _ddcolor_checkpoint(harness.tmp / "ddcolor.pth")
    harness.run([_colour_fix(path)])
    assert path in harness.engine._loaded_models
    harness.server._release_patch_colour()
    assert harness.engine._loaded_models == {}
    assert harness.server.patch_cache.colour_model is None


def test_the_patch_release_message_reaches_the_release(harness, monkeypatch):
    released = threading.Event()
    server = WorkerServer(requests=_lines({"type": "patch_release", "data": {}}))
    monkeypatch.setattr(server, "_release_patch_colour", released.set)
    server.model_adapter = harness.adapter
    server.engine = harness.engine
    runner = threading.Thread(target=server.run, name="localsr-test-worker")
    runner.start()
    runner.join(60)
    assert not runner.is_alive()
    assert released.is_set()


def test_a_release_without_a_colour_patch_does_not_build_the_engine():
    server = WorkerServer()
    server._release_patch_colour()
    assert server._engine is None


def test_a_patch_without_colour_keeps_the_model_and_a_job_or_idle_release_drops_it(
    harness, monkeypatch
):
    from localsr.worker import patch_preview

    monkeypatch.setattr(patch_preview, "colour_may_stay_loaded", lambda _device: True)
    path = _ddcolor_checkpoint(harness.tmp / "ddcolor.pth")
    harness.run([_colour_fix(path)])
    harness.run([_fix(harness.noise)])
    assert path in harness.engine._loaded_models
    # A job that does not use it makes room for its own models, as for any warm model.
    harness.engine.release_models_except({harness.up2})
    assert path not in harness.engine._loaded_models
    harness.run([_colour_fix(path)], image_path=_three_photos(harness)[0])
    assert path in harness.engine._loaded_models
    server = harness.server
    server._idle_since = 0.0
    server._release_when_idle(worker_server.IDLE_RELEASE_SECONDS + 1)
    assert harness.engine._loaded_models == {}


# Cancelling and scheduling ----------------------------------------------------------


def test_cancel_during_a_run_reports_cancelled_and_the_next_request_runs(harness, monkeypatch):
    original = harness.engine.process_image
    cancelled: list[bool] = []

    def cancelling(*args, **kwargs):
        if not cancelled:
            cancelled.append(True)
            harness.server._read_line(
                json.dumps({"type": "cancel_request", "data": {"job_id": "p1"}})
            )
        return original(*args, **kwargs)

    monkeypatch.setattr(harness.engine, "process_image", cancelling)
    messages = harness.run([_fix(harness.noise), _upscale(harness.up2)], output_scale=2)
    assert _types(messages)[-1] == "patch_cancelled"
    assert "patch_completed" not in _types(messages)
    assert harness.server.active_job_id is None
    assert not harness.server.cancel_event.is_set()
    again = harness.run([_fix(harness.noise)], patch_id="p2")
    assert _types(again)[-1] == "patch_completed"


def _lines(*requests: dict):
    return [json.dumps(request) + "\n" for request in requests]


def test_a_job_queued_behind_a_cancelled_patch_starts(harness, monkeypatch, tmp_path):
    job_done = threading.Event()
    messages: list = []

    def collect(message):
        messages.append(json.loads(message.to_json()))
        if message.to_json().startswith('{"type": "job_'):
            kind = json.loads(message.to_json())["type"]
            if kind in {"job_completed", "job_failed", "job_cancelled"}:
                job_done.set()

    monkeypatch.setattr(worker_server, "send_message", collect)
    patch = {
        "type": "patch_request",
        "data": harness.data([_fix(harness.noise), _upscale(harness.up2)], output_scale=2),
    }
    cancel = {"type": "cancel_request", "data": {"job_id": "p1"}}
    job = {
        "type": "job_request",
        "data": {
            "job_id": "job-after",
            "image_path": harness.image,
            "model_path": harness.up2,
            "output_path": str(tmp_path / "out.png"),
            "scratch_directory": str(tmp_path / "scratch"),
            "output_format": "png",
            "device": "cpu",
            "precision": "fp32",
            "tile_size": 256,
            "halo": 8,
            "jpeg_quality": 95,
            "preserve_metadata": False,
            "safe_memory": True,
            "output_scale": 2,
            "allow_unverified_checkpoint": True,
            "preview_enabled": False,
            "neural_engine": False,
        },
    }

    def requests():
        yield from _lines(patch, cancel, job)
        # End of input cancels whatever still runs, so it waits for the job.
        job_done.wait(120)

    server = WorkerServer(requests=requests())
    server.model_adapter = harness.adapter
    server.engine = harness.engine
    runner = threading.Thread(target=server.run, name="localsr-test-worker")
    runner.start()
    runner.join(180)
    assert not runner.is_alive()
    kinds = [m["type"] for m in messages]
    assert "patch_cancelled" in kinds and "patch_completed" not in kinds
    assert kinds.index("patch_cancelled") < kinds.index("job_started")
    assert "job_completed" in kinds
    assert (tmp_path / "out.png").is_file()


def test_two_queued_patches_for_one_slot_run_only_the_second(harness):
    first = {"type": "patch_request", "data": harness.data([_fix(harness.noise)], patch_id="old")}
    second = {"type": "patch_request", "data": harness.data([_fix(harness.noise)], patch_id="new")}
    other = {
        "type": "patch_request",
        "data": harness.data([_fix(harness.noise)], patch_id="b", slot="b"),
    }
    for request in (first, other, second):
        harness.server._read_line(json.dumps(request))
    queued = []
    while not harness.server.message_queue.empty():
        queued.append(harness.server.message_queue.get_nowait()["data"]["patch_id"])
    assert queued == ["b", "new"]
    dropped = [json.loads(m.to_json()) for m in harness.sent]
    assert [(m["type"], m["data"]["patch_id"]) for m in dropped] == [("patch_cancelled", "old")]


# Video --------------------------------------------------------------------------------


def _clip(path: Path, frames: int = 3, size: tuple[int, int] = (420, 400)) -> str:
    import av

    width, height = size
    with av.open(str(path), mode="w", format="mp4") as container:
        stream = container.add_stream("libvpx-vp9", rate=12)
        stream.width, stream.height, stream.pix_fmt = width, height, "yuv420p"
        stream.options = {"lossless": "1", "lag-in-frames": "0"}
        for index in range(frames):
            rgb = np.empty((height, width, 3), dtype=np.uint8)
            rgb[:, :, 0] = 60 + index * 60
            rgb[:, :, 1] = np.linspace(40, 200, width, dtype=np.uint8)[None, :]
            rgb[:, :, 2] = 100
            frame = av.VideoFrame.from_ndarray(rgb, format="rgb24")
            frame.pts = index
            frame.time_base = Fraction(1, 12)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return str(path)


def test_a_patch_on_a_video_cuts_its_first_frame(harness, monkeypatch):
    monkeypatch.setenv("LOCALSR_DEV_STEPS", "1")
    clip = _clip(harness.tmp / "clip.mp4")
    grain = {"kind": "grain", "step": "grain", "params": {"strength": 30, "seed": 7}}
    messages = harness.run(
        [grain],
        image_path=clip,
        frame_index=0,
        region={"x": 10, "y": 8, "width": 384, "height": 384},
    )
    assert _types(messages)[-1] == "patch_completed"
    picture = _png(_done(messages)[0])
    assert picture.shape == (384, 384, 3)
    from localsr.core.video_io import decode_timed_frames

    first = next(decode_timed_frames(clip, 0, 0, hdr_mode="tone_map"))
    from localsr.core.grain import add_grain

    # The region and its 32 px of padding cover the whole 420 x 400 frame.
    grained = add_grain(
        torch.from_numpy(np.ascontiguousarray(first.rgb.transpose(2, 0, 1))).float().div(255),
        strength=30,
        seed=7,
    )
    expected = grained.mul(255).round().byte().numpy().transpose(1, 2, 0)[8:392, 10:394]
    assert np.abs(picture.astype(np.int16) - expected.astype(np.int16)).max() <= 1
    # Frame 2 is a different red: the patch is the frame it was told, not the last one.
    assert abs(int(picture[..., 0].mean()) - 60) < 10
