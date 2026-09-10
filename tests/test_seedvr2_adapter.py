"""Adapter contracts without allocating the 3B checkpoint in the test suite."""

import threading
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from localsr.video_models.seedvr2 import engine as adapter


def test_mps_configuration_loads_weights_via_cpu_and_bounds_vae_work(tmp_path, monkeypatch):
    (tmp_path / adapter.DIT_PREFERENCE[0]).write_bytes(b"fixture")
    configuration = {}

    def setup(**kwargs):
        configuration.update(kwargs)
        return {"dit_device": "cpu", "compute_dtype": torch.float32}

    def prepare(**kwargs):
        configuration.update(kwargs)
        return object(), {}

    monkeypatch.setattr(adapter, "setup_generation_context", setup)
    monkeypatch.setattr(adapter, "prepare_runner", prepare)
    monkeypatch.setattr(adapter, "load_text_embeddings", lambda *args: {})
    model = adapter.SeedVR2Engine(str(tmp_path), "mps", "fp32")
    assert configuration["dit_offload_device"] == configuration["vae_offload_device"] == "cpu"
    assert configuration["encode_tile_size"] == configuration["decode_tile_size"] == (128, 128)
    assert not configuration["dit_cache"] and not configuration["vae_cache"]  # no cross-job cache
    assert model.max_temporal_window == 5


def test_smaller_output_is_prepared_on_cpu_before_buffering():
    frame = np.zeros((3840, 2160, 3), dtype=np.uint8)
    frame[:1920, :, 0] = 255
    smaller = adapter.SeedVR2Engine.prepare_frame(frame, 256)
    assert smaller.shape == (454, 256, 3)
    assert smaller.nbytes < frame.nbytes / 60
    assert smaller[:200, :, 0].mean() > 254
    assert smaller[-200:, :, 0].mean() < 1
    assert adapter.SeedVR2Engine.prepare_frame(smaller, 256) is smaller


def test_each_streamed_clip_reloads_embeddings_reports_real_phases_and_releases_output(monkeypatch):
    model = adapter.SeedVR2Engine.__new__(adapter.SeedVR2Engine)
    model.device = "cpu"
    model.ctx = {"dit_device": "cpu", "compute_dtype": torch.float32, "text_embeds": None}
    model.runner, model.debug = object(), None
    loads = []
    monkeypatch.setattr(
        adapter, "load_text_embeddings", lambda *args: loads.append(True) or {"loaded": True}
    )
    monkeypatch.setattr(adapter, "compute_generation_info", lambda **kwargs: (kwargs["images"], {}))

    def encode(_runner, **kwargs):
        ctx = kwargs["ctx"]
        assert ctx["text_embeds"] and ctx["final_video"] is None
        ctx["input"] = kwargs["images"]
        kwargs["progress_callback"](1, 1, 5, "encoding")
        return ctx

    def enhance(_runner, **kwargs):
        ctx = kwargs["ctx"]
        assert kwargs["cache_model"]  # upstream False destroys the runner's model
        ctx["text_embeds"] = None  # upstream deliberately frees them here
        kwargs["progress_callback"](1, 1, 5, "enhancing")
        return ctx

    def decode(_runner, **kwargs):
        assert kwargs["cache_model"]
        kwargs["progress_callback"](1, 1, 5, "decoding")
        return kwargs["ctx"]

    def finish(**kwargs):
        ctx = kwargs["ctx"]
        ctx["final_video"] = ctx.pop("input")
        kwargs["progress_callback"](1, 1, 5, "finishing")
        return ctx

    monkeypatch.setattr(adapter, "encode_all_batches", encode)
    monkeypatch.setattr(adapter, "upscale_all_batches", enhance)
    monkeypatch.setattr(adapter, "decode_all_batches", decode)
    monkeypatch.setattr(adapter, "postprocess_all_batches", finish)
    events = []
    original = np.full((16, 24, 3), 128, dtype=np.uint8)
    for _ in range(2):
        output = model.process_frames(
            [original], resolution=16, progress_callback=lambda *event: events.append(event)
        )
        np.testing.assert_array_equal(output[0], original)
        assert model.ctx["final_video"] is None
    assert len(loads) == 2
    assert [stage for stage, done, total in events if done == 1] == [
        "encoding",
        "enhancing",
        "decoding",
        "finishing",
    ] * 2
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(adapter.SeedVR2CancelledError):
        model.process_frames([original], resolution=16, cancel_event=cancelled)


def test_worker_memory_error_offers_resolution_recovery_without_disabling_limits(
    tmp_path, monkeypatch
):
    from test_video_timing import IdentityEngine, make_vfr

    from localsr.worker.server import WorkerServer

    messages = []
    monkeypatch.setattr("localsr.worker.server.send_message", messages.append)

    class OutOfMemory(IdentityEngine):
        def process_frames(self, frames, **kwargs):
            raise RuntimeError(
                "MPS backend out of memory. Use PYTORCH_MPS_HIGH_WATERMARK_RATIO=0.0"
            )

    source = make_vfr(tmp_path / "input.mp4")
    output = tmp_path / "output.mp4"
    worker = SimpleNamespace(cancel_event=threading.Event(), _emit_live_preview=lambda _: None)
    with pytest.raises(RuntimeError, match="Choose a smaller Output resolution") as failure:
        WorkerServer._run_temporal_video_job(
            worker,
            "oom",
            {"video_path": str(source), "output_video_path": str(output), "preview_enabled": False},
            lambda *_: OutOfMemory(),
        )
    assert "HIGH_WATERMARK" not in str(failure.value)
    assert not output.exists()
    assert any(getattr(message, "stage", "") == "reading_frames" for message in messages)
