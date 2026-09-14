"""Adapter contracts without allocating the 3B checkpoint in the test suite."""

import threading
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from localsr.core.video_engines import seedvr2_runtime_issue
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
    model.runner, model.debug = object(), SimpleNamespace()
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


@pytest.mark.parametrize("tile_size", [16, 64])
@pytest.mark.skipif(seedvr2_runtime_issue() is not None, reason=seedvr2_runtime_issue() or "")
def test_vae_preview_regions_follow_actual_work_without_changing_tensors(tile_size):
    from localsr.video_models.seedvr2.vendor.models.video_vae_v3.modules.attn_video_vae import (
        VideoAutoencoderKL,
    )

    events = []
    debug = SimpleNamespace(log=lambda *a, **k: None, tile_callback=lambda *e: events.append(e))
    vae = SimpleNamespace(
        debug=debug,
        spatial_downsample_factor=2,
        slicing_encode=lambda x: torch.nn.functional.avg_pool3d(x, (1, 2, 2)),
        slicing_decode=lambda x: x.repeat_interleave(2, dim=-2).repeat_interleave(2, dim=-1),
    )
    source = torch.linspace(-1, 1, 3 * 3 * 32 * 48).reshape(1, 3, 3, 32, 48)

    def roundtrip():
        encoded = VideoAutoencoderKL.tiled_encode(vae, source, (tile_size, tile_size), (4, 4))
        return VideoAutoencoderKL.tiled_decode(vae, encoded, (tile_size, tile_size), (4, 4))

    observed = roundtrip()
    debug.tile_callback = None
    torch.testing.assert_close(observed, roundtrip(), rtol=0, atol=0)
    count = 12 if tile_size == 16 else 1
    for stage in ["encoding", "decoding"]:
        regions = [event for event in events if event[0] == stage]
        assert len(regions) == count * 2
        assert [event[1] for event in regions] == ["started", "completed"] * count
        assert regions[-1][2:4] == (count, count)
        assert all(event[-2:] == (48, 32) for event in regions)
        if count > 1:
            assert regions[2][4:8] == (12, 0, 16, 16)  # actual overlap, not HAT's grid
            assert regions[-1][4:8] == (36, 24, 12, 8)


def test_vae_observer_clips_spatial_padding_and_cleans_up_on_failure():
    model = adapter.SeedVR2Engine.__new__(adapter.SeedVR2Engine)
    model.device = "cpu"
    model.ctx = {"true_target_dims": (454, 256)}
    previous = object()
    model.debug = SimpleNamespace(tile_callback=previous)
    regions = []
    with pytest.raises(InterruptedError):
        with model._observe_tiles(lambda *e: regions.append(e), lambda *e: None):
            model.debug.tile_callback("decoding", "started", 14, 15, 224, 448, 32, 16, 256, 464)
            raise InterruptedError()
    assert regions[0][4:] == (224, 448, 32, 6, 256, 454)
    assert model.debug.tile_callback is previous


def test_rocm_memory_saving_uses_upstream_offload_and_exact_selected_checkpoint(
    tmp_path, monkeypatch
):
    for filename in adapter.DIT_PREFERENCE:
        (tmp_path / filename).write_bytes(b"fixture")
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
    adapter.SeedVR2Engine(str(tmp_path), "cuda:0", "fp32", model_id="seedvr2_3b_fp8")
    assert configuration["dit_model"] == "seedvr2_ema_3b_fp8_e4m3fn.safetensors"
    assert configuration["tensor_offload_device"] == "cpu"
    assert configuration["block_swap_config"] == {"blocks_to_swap": 32, "swap_io_components": True}
    assert configuration["decode_tile_size"] == (128, 128)
    adapter.SeedVR2Engine(str(tmp_path), "cuda:0", "fp32", low_memory=False, model_id="seedvr2_3b")
    assert configuration["dit_model"] == "seedvr2_ema_3b_fp16.safetensors"
    assert configuration["block_swap_config"] is None
    assert configuration["tensor_offload_device"] is None
    assert configuration["decode_tile_size"] == (512, 512)


def test_color_matrix_preserves_large_portrait_pixel_positions():
    from localsr.video_models.seedvr2.vendor.utils.color_fix import _channel_color_matrix

    # Cross the exact row boundary at which gfx1200's tall GEMM corrupted pixels.
    pixels = torch.linspace(0, 1, (524288 + 19) * 3).reshape(-1, 3)
    matrix = torch.tensor(
        [
            [0.4124564, 0.3575761, 0.1804375],
            [0.2126729, 0.7151522, 0.0721750],
            [0.0193339, 0.1191920, 0.9503041],
        ]
    )
    actual = _channel_color_matrix(pixels, matrix)
    torch.testing.assert_close(actual, pixels @ matrix.T, atol=2e-7, rtol=1e-6)
    assert actual.data_ptr() != pixels.data_ptr()


@pytest.mark.skipif(
    not torch.version.hip or not torch.cuda.is_available(), reason="ROCm hardware acceptance"
)
def test_rocm_large_color_matrix_matches_cpu_across_driver_boundary():
    from localsr.video_models.seedvr2.vendor.utils.color_fix import _apply_color_matrix

    pixels = torch.linspace(0, 1, 720 * 1280 * 3).reshape(-1, 3)
    matrix = torch.tensor(
        [
            [0.4124564, 0.3575761, 0.1804375],
            [0.2126729, 0.7151522, 0.0721750],
            [0.0193339, 0.1191920, 0.9503041],
        ]
    )
    actual = _apply_color_matrix(pixels.cuda(), matrix.cuda()).cpu()
    torch.testing.assert_close(actual, pixels @ matrix.T, atol=2e-7, rtol=1e-6)


def test_cancellation_interrupts_inside_a_clip_with_previews_disabled_and_removes_hook(monkeypatch):
    model = adapter.SeedVR2Engine.__new__(adapter.SeedVR2Engine)
    event = threading.Event()
    calls = []

    class Step(torch.nn.Module):
        def forward(self, value):
            calls.append(True)
            event.set()
            return value + 1

    network = torch.nn.Sequential(Step(), Step(), Step())
    monkeypatch.setattr(model, "_process_frames", lambda frames, **kwargs: network(torch.zeros(1)))
    with pytest.raises(adapter.SeedVR2CancelledError):
        model.process_frames([], cancel_event=event, tile_callback=None)
    assert len(calls) == 1  # stopped before the rest of the clip, not after it
    # The job hook is gone even after an exception. Other models still run.
    network(torch.zeros(1))
    assert len(calls) == 4
