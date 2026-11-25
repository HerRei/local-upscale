"""Unit and real-weight tests for the MLX FlashVSR v1.1 Tiny temporal engine."""

from __future__ import annotations

import math
import os
import sys
import threading
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import av
import numpy as np
import pytest
from PIL import Image

from localsr.core import video_engines
from localsr.core.video_engines import TemporalEngineUnavailable, resolve_video_engine
from localsr.core.video_io import probe_video
from localsr.protocol.messages import TileUpdate, VideoJobCompleted
from localsr.video_models.flashvsr import engine as flashvsr_engine
from localsr.video_models.flashvsr.engine import (
    MAX_TILE_SIDE,
    SPATIAL_MULTIPLE,
    FlashVSREngine,
    padded_frame_count,
    plan_tiles,
)
from localsr.worker.request_validation import validate_video_job
from localsr.worker.server import WorkerServer, _engine_features, _video_engines

_FLASHVSR_MODELS = os.environ.get("LOCALSR_FLASHVSR_MODELS", "")
_HAS_REAL_WEIGHTS = bool(_FLASHVSR_MODELS and Path(_FLASHVSR_MODELS).is_dir())


def _synthetic_frames(count: int, width: int, height: int) -> list[np.ndarray]:
    """Deterministic RGB uint8 moving pattern for unit and real-weight tests."""
    frames: list[np.ndarray] = []
    yy, xx = np.mgrid[0:height, 0:width]
    for idx in range(count):
        r = ((xx * 5 + idx * 9) % 256).astype(np.uint8)
        g = ((yy * 7 + idx * 5) % 256).astype(np.uint8)
        b = (((xx + yy) * 3 + idx * 11) % 256).astype(np.uint8)
        frame = np.stack([r, g, b], axis=-1)
        bx = (idx * 3) % max(1, width - 8)
        by = (idx * 2) % max(1, height - 8)
        frame[by : by + 8, bx : bx + 8] = [240, 220, 40]
        frames.append(np.ascontiguousarray(frame))
    return frames


def _psnr(img_a: np.ndarray, img_b: np.ndarray) -> float:
    diff = img_a.astype(np.float64) - img_b.astype(np.float64)
    mse = float(np.mean(diff * diff))
    if mse == 0.0:
        return float("inf")
    return 10.0 * math.log10((255.0 * 255.0) / mse)


def _stub_engine(
    *,
    budget_bytes: int | None = None,
    tile_size_override: tuple[int, int] | None = None,
    cancel_after_tile: int | None = None,
    cancel_event: threading.Event | None = None,
    cancel_in_step: bool = False,
) -> tuple[FlashVSREngine, list[dict]]:
    calls: list[dict] = []

    class StubPipeline:
        def __call__(
            self,
            *,
            height: int,
            width: int,
            num_frames: int,
            seed: int,
            LQ_video: np.ndarray,
            is_full_block: bool,
            if_buffer: bool,
            topk_ratio: float,
            kv_ratio: float,
            local_range: int,
            color_fix: bool,
            progress_callback=None,
        ) -> np.ndarray:
            del seed, is_full_block, if_buffer, kv_ratio, local_range, color_fix
            tile_idx = len(calls)
            calls.append(
                {
                    "height": height,
                    "width": width,
                    "num_frames": num_frames,
                    "lq_shape": tuple(LQ_video.shape),
                    "lq_video": LQ_video.copy(),
                    "topk_ratio": topk_ratio,
                }
            )
            total_steps = (num_frames - 1) // 8 - 2
            for step in range(1, total_steps + 1):
                if cancel_in_step and cancel_event is not None and step == 1:
                    cancel_event.set()
                if progress_callback is not None:
                    progress_callback(step, total_steps)
            if (
                cancel_after_tile is not None
                and cancel_event is not None
                and tile_idx == cancel_after_tile
            ):
                cancel_event.set()
            return LQ_video[0, :, : num_frames - 4, :, :]

    engine = FlashVSREngine.__new__(FlashVSREngine)
    engine.bundle_dir = Path("/stub")
    engine.device = "mps"
    engine.max_temporal_window = flashvsr_engine.MAX_TEMPORAL_WINDOW
    engine._budget_bytes_override = budget_bytes
    engine._tile_size_override = tile_size_override
    engine._upscale_resample = Image.Resampling.NEAREST
    engine._numpy_stub_arrays = True
    engine.pipeline = StubPipeline()
    return engine, calls


def test_resolve_video_engine_flashvsr_with_and_without_mlx(monkeypatch):
    import platform as std_platform

    if (
        sys.platform == "darwin"
        and std_platform.machine().lower() == "arm64"
        and video_engines.mlx_installed()
        and video_engines.mlx_usable()
    ):
        factory = resolve_video_engine("flashvsr")
        assert factory is FlashVSREngine
        assert "flashvsr" in _video_engines()
        assert "video_flashvsr" in _engine_features()

    monkeypatch.setattr(video_engines.sys, "platform", "darwin")
    monkeypatch.setattr(video_engines.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(video_engines, "mlx_installed", lambda: True)
    monkeypatch.setattr(video_engines, "mlx_usable", lambda: True)
    assert resolve_video_engine("flashvsr") is FlashVSREngine
    assert "flashvsr" in _video_engines()
    assert "video_flashvsr" in _engine_features()

    monkeypatch.undo()
    monkeypatch.setattr(video_engines.sys, "platform", "darwin")
    monkeypatch.setattr(video_engines.platform, "machine", lambda: "arm64")
    monkeypatch.setitem(sys.modules, "mlx.core", None)
    with pytest.raises(TemporalEngineUnavailable, match="MLX"):
        resolve_video_engine("flashvsr")
    assert "flashvsr" not in _video_engines()

    monkeypatch.undo()
    monkeypatch.setattr(video_engines.sys, "platform", "darwin")
    monkeypatch.setattr(video_engines.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(video_engines, "mlx_installed", lambda: True)
    monkeypatch.delitem(sys.modules, "mlx.core", raising=False)
    video_engines._mlx_child_check.cache_clear()
    try:
        monkeypatch.setattr(
            video_engines.subprocess,
            "run",
            lambda *args, **kwargs: SimpleNamespace(returncode=1, stderr=b"Metal failed"),
        )
        with pytest.raises(TemporalEngineUnavailable, match="could not start MLX"):
            resolve_video_engine("flashvsr")
        assert "flashvsr" not in _video_engines()
    finally:
        video_engines._mlx_child_check.cache_clear()

    monkeypatch.undo()
    monkeypatch.setattr(video_engines.sys, "platform", "linux")
    import torch

    monkeypatch.setattr(torch, "version", SimpleNamespace(cuda=None, hip=None), raising=False)
    with pytest.raises(TemporalEngineUnavailable, match="Apple Silicon"):
        resolve_video_engine("flashvsr")
    assert "flashvsr" not in _video_engines()


def test_validate_video_job_accepts_flashvsr():
    validate_video_job(
        {
            "job_id": "flash-1",
            "video_path": "in.mp4",
            "model_path": "",
            "output_video_path": "out.mp4",
            "device": "mps",
            "tile_size": 128,
            "halo": 16,
            "precision": "bf16",
            "safe_memory": True,
            "model_kind": "flashvsr",
            "bundle_dir": "/tmp/flashvsr",
            "temporal_window": 37,
            "temporal_overlap": 8,
            "target_resolution": 0,
            "output_scale": 4,
        }
    )


@pytest.mark.parametrize(
    "n_in",
    [1, 5, 13, 21, 22, 25, 29, 30, 33, 37, 41, 61],
)
def test_padded_frame_count_is_8n_plus_1_and_covers_input(n_in: int):
    n_pad = padded_frame_count(n_in)
    assert n_pad >= 25
    assert (n_pad - 1) % 8 == 0
    assert n_pad - 4 >= n_in
    if n_pad > 25:
        assert (n_pad - 8) - 4 < n_in


def test_odd_size_padding_to_128_multiples_and_crop_back_is_exact():
    frames = _synthetic_frames(3, width=97, height=61)
    engine, calls = _stub_engine(budget_bytes=20_000_000_000)

    out = engine.process_frames(frames)
    assert len(out) == 3
    assert len(calls) == 1
    assert calls[0]["width"] == 512  # ceil(97 * 4 = 388, 128)
    assert calls[0]["height"] == 256  # ceil(61 * 4 = 244, 128)
    assert calls[0]["width"] % SPATIAL_MULTIPLE == 0
    assert calls[0]["height"] % SPATIAL_MULTIPLE == 0

    for src, dst in zip(frames, out, strict=True):
        assert dst.shape == (244, 388, 3)
        assert dst.dtype == np.uint8
        expected = np.asarray(
            Image.fromarray(src).resize((388, 244), Image.Resampling.NEAREST),
            dtype=np.uint8,
        )
        # uint8 -> [-1, 1] -> uint8 via * 127.5 matches within 1 LSB or exact
        assert np.max(np.abs(dst.astype(np.int16) - expected.astype(np.int16))) <= 1


@pytest.mark.parametrize(
    ("out_w", "out_h", "budget_bytes", "override"),
    [
        (1920, 1080, 10_800_000_000, None),
        (3840, 2160, 20_000_000_000, None),
        (896, 512, 10_800_000_000, (512, 384)),
        (1024, 1024, 10_800_000_000, (640, 640)),
    ],
)
def test_tiles_cover_every_pixel_once_and_weights_sum_to_one(
    out_w: int,
    out_h: int,
    budget_bytes: int,
    override: tuple[int, int] | None,
):
    plan = plan_tiles(out_w, out_h, budget_bytes=budget_bytes, tile_size_override=override)
    assert plan.padded_width % SPATIAL_MULTIPLE == 0
    assert plan.padded_height % SPATIAL_MULTIPLE == 0
    assert plan.tile_width % SPATIAL_MULTIPLE == 0
    assert plan.tile_height % SPATIAL_MULTIPLE == 0
    assert plan.tile_width <= MAX_TILE_SIDE
    assert plan.tile_height <= MAX_TILE_SIDE

    weight_canvas = np.zeros((plan.padded_height, plan.padded_width), dtype=np.float32)
    cover_count = np.zeros((plan.padded_height, plan.padded_width), dtype=np.int32)
    for tile in plan.tiles:
        assert tile.width == plan.tile_width
        assert tile.height == plan.tile_height
        assert 0 <= tile.x and tile.x + tile.width <= plan.padded_width
        assert 0 <= tile.y and tile.y + tile.height <= plan.padded_height
        weight_canvas[tile.y : tile.y + tile.height, tile.x : tile.x + tile.width] += tile.weights
        cover_count[tile.y : tile.y + tile.height, tile.x : tile.x + tile.width] += 1

    assert np.all(cover_count >= 1)
    assert np.allclose(weight_canvas, 1.0, atol=1e-6)


def test_frame_smaller_than_tile_and_tiled_stub_roundtrip():
    small_frames = _synthetic_frames(2, width=40, height=30)
    small_engine, small_calls = _stub_engine(budget_bytes=10_800_000_000)
    small_out = small_engine.process_frames(small_frames)
    assert len(small_calls) == 1
    assert small_out[0].shape == (120, 160, 3)

    # Multi-tile stub roundtrip: 224x128 -> 896x512 forced 2x2 tiles
    big_frames = _synthetic_frames(2, width=224, height=128)
    tiled_engine, tiled_calls = _stub_engine(
        budget_bytes=10_800_000_000,
        tile_size_override=(512, 384),
    )
    tile_events: list[tuple] = []
    progress_events: list[tuple] = []
    tiled_out = tiled_engine.process_frames(
        big_frames,
        progress_callback=lambda *e: progress_events.append(e),
        tile_callback=lambda *e: tile_events.append(e),
    )
    assert len(tiled_calls) == 4
    assert all(c["width"] == 512 and c["height"] == 384 for c in tiled_calls)
    expected_topk = 2.0 * 768.0 * 1280.0 / float(512 * 384)
    assert all(math.isclose(c["topk_ratio"], expected_topk) for c in tiled_calls)
    assert len(tile_events) == 8
    assert [e[1] for e in tile_events] == ["started", "completed"] * 4
    assert any(stage == "enhancing" for stage, _, _ in progress_events)

    for src, dst in zip(big_frames, tiled_out, strict=True):
        expected = np.asarray(
            Image.fromarray(src).resize((896, 512), Image.Resampling.NEAREST),
            dtype=np.uint8,
        )
        assert np.max(np.abs(dst.astype(np.int16) - expected.astype(np.int16))) <= 1


def test_frame_count_padding_repeats_last_frame_and_returns_exact_length():
    frames = _synthetic_frames(25, width=32, height=32)
    engine, calls = _stub_engine(budget_bytes=10_800_000_000)

    out = engine.process_frames(frames)
    assert len(out) == 25
    assert calls[0]["num_frames"] == 33
    lq = calls[0]["lq_video"]
    assert lq.shape == (1, 3, 33, 128, 128)
    for padded_idx in range(25, 33):
        np.testing.assert_array_equal(lq[0, :, padded_idx], lq[0, :, 24])


def test_cancel_between_tiles_and_steps_raises_interrupted_error():
    frames = _synthetic_frames(2, width=224, height=128)

    cancel_tiles = threading.Event()
    engine_tiles, calls_tiles = _stub_engine(
        budget_bytes=10_800_000_000,
        tile_size_override=(512, 384),
        cancel_after_tile=0,
        cancel_event=cancel_tiles,
    )
    with pytest.raises(InterruptedError):
        engine_tiles.process_frames(frames, cancel_event=cancel_tiles)
    assert len(calls_tiles) == 1

    cancel_step = threading.Event()
    engine_step, _ = _stub_engine(
        budget_bytes=10_800_000_000,
        cancel_event=cancel_step,
        cancel_in_step=True,
    )
    with pytest.raises(InterruptedError):
        engine_step.process_frames(frames, cancel_event=cancel_step)


def test_low_memory_budget_refuses_below_512x512():
    too_small_budget = flashvsr_engine.FIXED_MEMORY_BYTES + int(
        256 * 256 * flashvsr_engine.PER_PIXEL_MEMORY_BYTES
    )
    with pytest.raises(RuntimeError, match="512x512") as exc_info:
        plan_tiles(1024, 1024, budget_bytes=too_small_budget)
    msg = str(exc_info.value)
    # Must not trigger SeedVR2's "out of memory" message rewrite in WorkerServer.
    assert "out of memory" not in msg.lower()
    # Refusal must name the largest output resolution (shorter edge, px) that fits.
    match = pytest.importorskip("re").search(
        r"Choose an output resolution of (\d+) px or less, or frame-by-frame video\.",
        msg,
    )
    assert match is not None, f"Unexpected refusal message: {msg}"
    max_res = int(match.group(1))
    assert plan_tiles(max_res, max_res, budget_bytes=too_small_budget).tiles
    with pytest.raises(RuntimeError):
        plan_tiles(max_res + 2, max_res + 2, budget_bytes=too_small_budget)

    # When not even 128x128 fits, no impossible resolution is suggested.
    tiny_budget = flashvsr_engine.FIXED_MEMORY_BYTES + 1000
    with pytest.raises(RuntimeError, match="Close other applications") as tiny_exc:
        plan_tiles(1024, 1024, budget_bytes=tiny_budget)
    assert "out of memory" not in str(tiny_exc.value).lower()


def test_plan_tiles_accounts_for_window_length_and_unified_host_buffers(monkeypatch):
    # On a 16 GB Mac (~10.8 GB budget from mps_recommended_working_set_bytes), on-demand LQ
    # and disk-backed canvas keep host RAM per tile small, so 4K and 5760x7680 at 37 frames
    # plan valid tiles >= 512x512 instead of failing on full-frame host buffers.
    monkeypatch.setattr(
        flashvsr_engine,
        "mps_recommended_working_set_bytes",
        lambda: 12_713_115_648,
    )
    for out_w, out_h in ((3840, 2160), (5760, 7680)):
        plan = plan_tiles(out_w, out_h, n_frames=37)
        assert plan.tile_width >= flashvsr_engine.MIN_TILE_SIDE
        assert plan.tile_height >= flashvsr_engine.MIN_TILE_SIDE
        assert plan.tile_width * plan.tile_height <= plan.max_tile_area
        weight_canvas = np.zeros((plan.padded_height, plan.padded_width), dtype=np.float32)
        for tile in plan.tiles:
            weight_canvas[tile.y : tile.y + tile.height, tile.x : tile.x + tile.width] += (
                tile.weights
            )
        assert np.allclose(weight_canvas, 1.0, atol=1e-6)

    # A 61-frame window on the same 16 GB Mac still exceeds 512x512 and names a fitting resolution.
    with pytest.raises(RuntimeError, match="Choose an output resolution of") as exc_61:
        plan_tiles(5760, 7680, n_frames=61)
    import re as _re

    match = _re.search(r"of (\d+) px or less", str(exc_61.value))
    assert match is not None
    fitting_res = int(match.group(1))
    fitting_w, fitting_h = flashvsr_engine.output_size(1440, 1920, fitting_res)
    assert plan_tiles(fitting_w, fitting_h, n_frames=61).tiles
    next_w, next_h = flashvsr_engine.output_size(1440, 1920, fitting_res + 2)
    with pytest.raises(RuntimeError):
        plan_tiles(next_w, next_h, n_frames=61)

    # On a 32 GB Mac (20 GB budget), longer windows shrink max_tile_area via both n_pad and tile RAM.
    plan_37 = plan_tiles(3840, 2160, n_frames=37, budget_bytes=20_000_000_000)
    plan_61 = plan_tiles(3840, 2160, n_frames=61, budget_bytes=20_000_000_000)
    assert plan_61.max_tile_area < plan_37.max_tile_area
    assert plan_61.tile_width * plan_61.tile_height <= plan_61.max_tile_area


def test_engine_rejects_non_mps_and_release_clears_pipeline():
    with pytest.raises(ValueError, match="Apple Silicon GPU"):
        FlashVSREngine("/nonexistent", "cpu", "bf16")

    engine, _ = _stub_engine(budget_bytes=10_800_000_000)
    engine.close()
    assert engine.pipeline is None
    with pytest.raises(RuntimeError, match="released"):
        engine.process_frames(_synthetic_frames(1, 32, 32))


def test_vendored_pipeline_loads_safetensors_without_torch_load(monkeypatch):
    import torch

    pkg_dir = Path(__file__).parents[1] / "src" / "localsr" / "video_models" / "flashvsr"
    assert (pkg_dir / "posi_prompt.safetensors").is_file()
    assert not list(pkg_dir.rglob("*.pth"))

    if not video_engines.mlx_installed():
        pytest.skip("MLX is only available on Apple Silicon (macOS arm64)")
    if not video_engines.mlx_usable():
        pytest.skip("MLX is not usable on this Mac")

    from localsr.video_models.flashvsr.mlx_flashvsr.dit import WAN_1_3B_CONFIG
    from localsr.video_models.flashvsr.mlx_flashvsr.model import WanModel
    from localsr.video_models.flashvsr.mlx_flashvsr.pipeline import FlashVSRTinyPipeline

    monkeypatch.setattr(
        torch,
        "load",
        lambda *_args, **_kwargs: pytest.fail("FlashVSR prompt loading must not call torch.load"),
    )
    small_dit = WanModel(
        dim=128,
        in_dim=WAN_1_3B_CONFIG["in_dim"],
        ffn_dim=256,
        out_dim=WAN_1_3B_CONFIG["out_dim"],
        text_dim=WAN_1_3B_CONFIG["text_dim"],
        freq_dim=WAN_1_3B_CONFIG["freq_dim"],
        num_heads=1,
        num_layers=1,
    )
    pipe = FlashVSRTinyPipeline(dit=small_dit, tcdecoder=None, dtype="bfloat16")
    pipe.init_cross_kv()
    assert pipe.prompt_emb_posi is not None
    assert tuple(pipe.prompt_emb_posi["context"].shape) == (1, 512, 4096)


def test_worker_temporal_routing_sizes_flashvsr_like_every_temporal_engine(tmp_path, monkeypatch):
    import localsr.worker.server as server_module

    source_mp4 = tmp_path / "in.mp4"
    output_mp4 = tmp_path / "out.mp4"
    frames = _synthetic_frames(10, width=32, height=24)
    with av.open(str(source_mp4), "w") as container:
        stream = container.add_stream("libvpx-vp9", rate=24)
        stream.width, stream.height, stream.pix_fmt = 32, 24, "yuv420p"
        stream.codec_context.time_base = Fraction(1, 24)
        stream.options = {"lossless": "1", "lag-in-frames": "0"}
        for idx, rgb in enumerate(frames):
            frame = av.VideoFrame.from_ndarray(rgb, format="rgb24")
            frame.pts, frame.time_base = idx, Fraction(1, 24)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)

    room_calls: list[tuple[int, int]] = []
    monkeypatch.setattr(
        server_module,
        "require_video_room",
        lambda _data, width, height, _frames, **_kw: room_calls.append((width, height)),
    )
    monkeypatch.setattr(server_module, "send_message", lambda _msg: None)

    configured: list[tuple[int, int, int]] = []
    seen_batch_sizes: list[int] = []

    class FakeMemory:
        latest = SimpleNamespace(clip_frames=5, output_width=0, output_height=0)

        def phase(self, _stage):
            pass

        def configure(self, width, height, clip_frames):
            configured.append((width, height, clip_frames))
            self.latest.output_width = width
            self.latest.output_height = height

        def sample(self):
            pass

    engine, _calls = _stub_engine(budget_bytes=10_800_000_000)
    orig_process = engine.process_frames

    def spy_process(chunk_frames, **kwargs):
        seen_batch_sizes.append(kwargs["batch_size"])
        return orig_process(chunk_frames, **kwargs)

    engine.process_frames = spy_process  # type: ignore[method-assign]
    closed = []
    engine.close = lambda: closed.append(True)  # type: ignore[method-assign]

    worker = SimpleNamespace(cancel_event=threading.Event(), _emit_live_preview=lambda _: None)
    WorkerServer._run_temporal_video_job_impl(
        worker,
        "flash-route",
        {
            "video_path": str(source_mp4),
            "output_video_path": str(output_mp4),
            "model_kind": "flashvsr",
            "video_codec": "vp9",
            "container": "mp4",
            "target_resolution": 96,
            "temporal_window": 37,
            "temporal_overlap": 8,
            "preview_enabled": False,
        },
        lambda *_args: engine,
        FakeMemory(),
    )
    assert room_calls == [(128, 96)]
    assert configured[0] == (128, 96, 5)
    assert seen_batch_sizes == [37]
    assert closed == [True]
    assert probe_video(str(output_mp4)).width == 128
    assert probe_video(str(output_mp4)).height == 96


@pytest.mark.skipif(
    not _HAS_REAL_WEIGHTS,
    reason="Set LOCALSR_FLASHVSR_MODELS to run real-weight FlashVSR tests.",
)
def test_real_weights_untiled_matches_port_pipeline():
    import importlib.util

    import mlx.core as mx

    port_root = Path("/Users/hermesheiniger/Projects/FlashVSR/flashvsr-mlx-port")
    if port_root.is_dir():
        sys.path.insert(0, str(port_root))
        try:
            port_mod = importlib.import_module("mlx_flashvsr.pipeline")
            port_cls = port_mod.FlashVSRTinyPipeline
        finally:
            if sys.path and sys.path[0] == str(port_root):
                sys.path.pop(0)
    else:
        from localsr.video_models.flashvsr.mlx_flashvsr.pipeline import (
            FlashVSRTinyPipeline as port_cls,
        )

    frames = _synthetic_frames(25, width=64, height=32)
    engine = FlashVSREngine(_FLASHVSR_MODELS, device="mps", precision="bf16")
    try:
        engine_out = engine.process_frames(frames, seed=0)
        assert len(engine_out) == 25
        assert engine_out[0].shape == (128, 256, 3)
    finally:
        engine.close()

    # Compare against direct FlashVSRTinyPipeline on the same 33-frame padded input.
    up_list = []
    for frame in frames:
        up = np.asarray(
            Image.fromarray(frame).resize((256, 128), Image.Resampling.BICUBIC),
            dtype=np.float32,
        )
        up_list.append(np.transpose(up, (2, 0, 1)) / 127.5 - 1.0)
    up_list.extend([up_list[-1]] * 8)
    lq_mx = mx.array(np.stack(up_list, axis=1)[None, ...], dtype=mx.float32).astype(mx.bfloat16)
    mx.eval(lq_mx)

    port_pipe = port_cls.from_pretrained(_FLASHVSR_MODELS, dtype="bfloat16")
    try:
        ref_tensor = port_pipe(
            height=128,
            width=256,
            num_frames=33,
            seed=0,
            LQ_video=lq_mx,
            is_full_block=False,
            if_buffer=True,
            topk_ratio=2.0 * 768.0 * 1280.0 / float(128 * 256),
            kv_ratio=3.0,
            local_range=11,
            color_fix=True,
        )
        mx.eval(ref_tensor)
        ref_np = np.transpose(np.asarray(ref_tensor.astype(mx.float32)), (1, 2, 3, 0))[:25]
        ref_uint8 = np.clip((ref_np + 1.0) * 127.5, 0.0, 255.0).astype(np.uint8)
    finally:
        del port_pipe
        mx.clear_cache()

    psnr_val = _psnr(np.stack(engine_out), ref_uint8)
    print(f"\n[FlashVSR untiled 256x128 x 25f] PSNR vs port pipeline: {psnr_val:.2f} dB")
    assert psnr_val > 40.0


@pytest.mark.timeout(600)
@pytest.mark.skipif(
    not _HAS_REAL_WEIGHTS,
    reason="Set LOCALSR_FLASHVSR_MODELS to run real-weight FlashVSR tests.",
)
def test_real_weights_tiled_2x2_vs_untiled_and_seam_check():
    frames = _synthetic_frames(33, width=224, height=128)
    engine = FlashVSREngine(_FLASHVSR_MODELS, device="mps", precision="bf16")
    try:
        # Untiled baseline (force single 896x512 tile).
        engine._tile_size_override = (896, 512)
        untiled_out = np.stack(engine.process_frames(frames, seed=0))
        assert untiled_out.shape == (33, 512, 896, 3)

        # Forced 2x2 tiles: (512, 384) -> x in [0, 384], y in [0, 128]
        engine._tile_size_override = (512, 384)
        tiled_out = np.stack(engine.process_frames(frames, seed=0))
        assert tiled_out.shape == (33, 512, 896, 3)

        psnr_val = _psnr(tiled_out, untiled_out)

        # Seam check: compare mean abs step across tile-border edges vs interior pixels.
        tiled_f = tiled_out.astype(np.float32)
        horiz_grad = np.abs(tiled_f[:, :, 1:, :] - tiled_f[:, :, :-1, :])
        vert_grad = np.abs(tiled_f[:, 1:, :, :] - tiled_f[:, :-1, :, :])
        # Tile boundary x coords for [0, 512) and [384, 896) are x=384 and x=512.
        border_x = [383, 511]
        border_y = [127, 383]
        border_diff_x = float(np.mean(horiz_grad[:, :, border_x, :]))
        interior_diff_x = float(np.mean(np.delete(horiz_grad, border_x, axis=2)))
        border_diff_y = float(np.mean(vert_grad[:, border_y, :, :]))
        interior_diff_y = float(np.mean(np.delete(vert_grad, border_y, axis=1)))

        vs_untiled = np.abs(tiled_f - untiled_out.astype(np.float32))
        vs_untiled_border = float(
            0.5
            * (np.mean(vs_untiled[:, :, [384, 512], :]) + np.mean(vs_untiled[:, [128, 384], :, :]))
        )
        vs_untiled_elsewhere = float(np.mean(vs_untiled))

        print(
            f"\n[FlashVSR 2x2 tiled vs untiled 896x512 x 33f] PSNR={psnr_val:.2f} dB | "
            f"grad_x(border={border_diff_x:.3f}, interior={interior_diff_x:.3f}) | "
            f"grad_y(border={border_diff_y:.3f}, interior={interior_diff_y:.3f}) | "
            f"abs_diff(border={vs_untiled_border:.3f}, overall={vs_untiled_elsewhere:.3f})"
        )
        assert psnr_val > 25.0
        # Blended border gradient should not jump dramatically above interior gradient.
        assert border_diff_x <= interior_diff_x * 2.0 + 1.0
        assert border_diff_y <= interior_diff_y * 2.0 + 1.0
    finally:
        engine.close()


@pytest.mark.skipif(
    not _HAS_REAL_WEIGHTS,
    reason="Set LOCALSR_FLASHVSR_MODELS to run real-weight FlashVSR tests.",
)
def test_worker_end_to_end_flashvsr(tmp_path, monkeypatch):
    import localsr.worker.server as server_module

    source_mp4 = tmp_path / "input_64x48_24f.mp4"
    output_mp4 = tmp_path / "output_256x192.mp4"
    frames = _synthetic_frames(24, width=64, height=48)
    with av.open(str(source_mp4), "w") as container:
        stream = container.add_stream("libvpx-vp9", rate=24)
        stream.width, stream.height, stream.pix_fmt = 64, 48, "yuv420p"
        stream.codec_context.time_base = Fraction(1, 24)
        stream.options = {"lossless": "1", "lag-in-frames": "0"}
        for idx, rgb in enumerate(frames):
            frame = av.VideoFrame.from_ndarray(rgb, format="rgb24")
            frame.pts, frame.time_base = idx, Fraction(1, 24)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)

    bundle_dir = Path(_FLASHVSR_MODELS).resolve()
    catalog_model = SimpleNamespace(
        model_id="flashvsr_v1_1_tiny",
        name="FlashVSR v1.1 Tiny",
        engine_kind="flashvsr",
    )
    monkeypatch.setitem(server_module.VIDEO_CATALOG_BY_ID, "flashvsr_v1_1_tiny", catalog_model)

    class DummyStore:
        def bundle_dir_for(self, candidate):
            assert candidate is catalog_model
            return bundle_dir

        def is_bundle_installed(self, candidate):
            assert candidate is catalog_model
            return True

    messages: list[object] = []
    monkeypatch.setattr("localsr.worker.server.send_message", messages.append)

    server = WorkerServer()
    server.model_store = DummyStore()

    request_data = {
        "job_id": "flashvsr-e2e-1",
        "video_path": str(source_mp4),
        "hdr_mode": "tone_map",
        "model_path": "",
        "output_video_path": str(output_mp4),
        "container": "mp4",
        "video_codec": "vp9",
        "external_ffmpeg": None,
        "crf": 18,
        "fps": None,
        "device": "mps",
        "tile_size": 128,
        "halo": 16,
        "precision": "bf16",
        "safe_memory": True,
        "neural_engine": True,
        "keyframe_interval": None,
        "start_frame": None,
        "end_frame": None,
        "face_model_path": None,
        "deflicker": False,
        "deflicker_window": 3,
        "model_kind": "flashvsr",
        "video_model_id": "flashvsr_v1_1_tiny",
        "bundle_dir": str(bundle_dir),
        "temporal_window": 37,
        "temporal_overlap": 8,
        "target_resolution": 192,  # 4x of the 48 px shorter edge: 256x192.
        "video_low_memory": True,
        "output_scale": 4,
        "allow_unverified_checkpoint": False,
        "preview_enabled": True,
        "preview_max_fps": 2.0,
        "preview_max_dimension": 320,
        "interpolation": "off",
        "audio_denoise": False,
        "audio_denoise_model_id": None,
        "audio_denoise_bundle_dir": None,
    }
    validate_video_job(request_data)
    server._run_video_job("flashvsr-e2e-1", request_data)

    assert output_mp4.is_file()
    out_probe = probe_video(str(output_mp4))
    assert out_probe.width == 256
    assert out_probe.height == 192
    assert out_probe.frame_count == 24
    assert any(isinstance(msg, VideoJobCompleted) for msg in messages)
    assert any(isinstance(msg, TileUpdate) for msg in messages)


def test_output_size_follows_the_requested_shorter_edge():
    from localsr.video_models.flashvsr.engine import model_size, output_size

    assert output_size(97, 61, 0) == (388, 244)  # 0 = the model's own 4x
    assert output_size(64, 48, 96) == (128, 96)  # 2x
    assert output_size(64, 48, 100) == (132, 100)  # even sides, source proportions
    assert model_size(64, 48, 128, 96) == (128, 96)  # up to 4x the model runs at the output size
    assert model_size(64, 48, 512, 384) == (256, 192)  # beyond 4x it runs at 4x


def test_engine_honours_a_2x_request_and_resizes_beyond_4x():
    frames = _synthetic_frames(5, width=40, height=30)
    engine, calls = _stub_engine(budget_bytes=10_800_000_000)
    half = engine.process_frames(frames, resolution=60)
    assert half[0].shape == (60, 80, 3)
    assert calls[-1]["width"] == 128 and calls[-1]["height"] == 128  # 80x60 padded to 128
    big = engine.process_frames(frames, resolution=240)
    assert big[0].shape == (240, 320, 3)
    assert calls[-1]["width"] == 256 and calls[-1]["height"] == 128  # model ran at 160x120


def test_float_to_uint8_rounds_instead_of_truncating_on_float16_canvas(monkeypatch):
    # Constant 200.0 output across single-tile and float16 multi-tile overlap must stay 200.
    class Const200Engine(FlashVSREngine):
        def __init__(self, out_val: float):
            self.bundle_dir = Path("/stub")
            self.device = "mps"
            self._backend = "mps"
            self.max_temporal_window = flashvsr_engine.MAX_TEMPORAL_WINDOW
            self._budget_bytes_override = 20_000_000_000
            self._tile_size_override = None
            self._upscale_resample = Image.Resampling.NEAREST
            self.pipeline = object()
            self._out_val = out_val

        def _run_tile(self, lq_tile_uint8, n_in, n_pad, tile, seed, step_callback):
            del lq_tile_uint8, n_pad, seed, step_callback
            return np.full((n_in, tile.height, tile.width, 3), self._out_val, dtype=np.float32)

    frames = _synthetic_frames(2, width=40, height=30)
    single_out = Const200Engine(199.8).process_frames(frames)
    assert np.all(single_out[0] == 200)

    # Trigger float16 canvas on a small 2x2 tiled clip.
    monkeypatch.setattr(flashvsr_engine, "FLOAT16_CANVAS_ELEMENTS", 1)
    tiled_engine = Const200Engine(200.0)
    tiled_engine._tile_size_override = (512, 384)
    tiled_frames = _synthetic_frames(2, width=224, height=128)
    tiled_out = tiled_engine.process_frames(tiled_frames)
    assert np.all(tiled_out[0] == 200)


@pytest.mark.parametrize(
    ("in_w", "in_h", "resolution", "tile_override", "resample"),
    [
        (224, 128, 0, (512, 384), Image.Resampling.BICUBIC),
        (97, 61, 0, (256, 256), Image.Resampling.BICUBIC),
        (320, 240, 360, (256, 256), Image.Resampling.BICUBIC),
        (224, 128, 0, (512, 384), Image.Resampling.NEAREST),
    ],
)
def test_on_demand_tile_lq_matches_full_frame_slice(
    in_w: int,
    in_h: int,
    resolution: int,
    tile_override: tuple[int, int],
    resample: Image.Resampling,
):
    frames = _synthetic_frames(4, width=in_w, height=in_h)
    final_w, final_h = flashvsr_engine.output_size(in_w, in_h, resolution)
    out_w, out_h = flashvsr_engine.model_size(in_w, in_h, final_w, final_h)
    plan = plan_tiles(
        out_w,
        out_h,
        n_frames=len(frames),
        budget_bytes=20_000_000_000,
        tile_size_override=tile_override,
    )

    pad_right = plan.padded_width - out_w
    pad_bottom = plan.padded_height - out_h
    full_padded = np.empty((len(frames), plan.padded_height, plan.padded_width, 3), dtype=np.uint8)
    for idx, frame in enumerate(frames):
        up = np.asarray(Image.fromarray(frame).resize((out_w, out_h), resample), dtype=np.uint8)
        if pad_right > 0 or pad_bottom > 0:
            up = np.pad(up, ((0, pad_bottom), (0, pad_right), (0, 0)), mode="edge")
        full_padded[idx] = up

    engine, calls = _stub_engine(budget_bytes=20_000_000_000, tile_size_override=tile_override)
    engine._upscale_resample = resample
    engine.process_frames(frames, resolution=resolution)

    assert len(calls) == len(plan.tiles)
    for tile, call in zip(plan.tiles, calls, strict=True):
        expected_slice = full_padded[
            :, tile.y : tile.y + tile.height, tile.x : tile.x + tile.width, :
        ]
        # Convert [-1, 1] stub input back to uint8 for direct pixel comparison.
        actual_u8 = np.rint(
            (np.transpose(call["lq_video"][0, :, : len(frames)], (1, 2, 3, 0)) + 1.0) * 127.5
        ).astype(np.uint8)
        assert actual_u8.shape == expected_slice.shape
        max_diff = int(np.max(np.abs(actual_u8.astype(np.int16) - expected_slice.astype(np.int16))))
        assert max_diff <= 1


@pytest.mark.parametrize("use_float16_canvas", [False, True])
def test_memmap_accumulator_matches_ram_and_cleans_up(tmp_path, monkeypatch, use_float16_canvas):
    frames = _synthetic_frames(4, width=224, height=128)
    if use_float16_canvas:
        monkeypatch.setattr(flashvsr_engine, "FLOAT16_CANVAS_ELEMENTS", 1)

    ram_engine, _ = _stub_engine(budget_bytes=20_000_000_000, tile_size_override=(512, 384))
    ram_engine._upscale_resample = Image.Resampling.BICUBIC
    ram_out = ram_engine.process_frames(frames)

    monkeypatch.setattr(flashvsr_engine, "MEMMAP_CANVAS_BYTES", 1)
    seen_during_run: list[list[str]] = []
    mm_engine, _ = _stub_engine(budget_bytes=20_000_000_000, tile_size_override=(512, 384))
    mm_engine._upscale_resample = Image.Resampling.BICUBIC
    mm_engine.temporary_directory = tmp_path
    orig_pipeline = mm_engine.pipeline

    def spy_pipeline(**kwargs):
        seen_during_run.append(sorted(p.name for p in tmp_path.glob("localsr-flashvsr-*/*")))
        return orig_pipeline(**kwargs)

    mm_engine.pipeline = spy_pipeline
    mm_out = mm_engine.process_frames(frames)

    assert len(seen_during_run) == 4
    assert all(names == ["canvas.dat", "weight_sum.dat"] for names in seen_during_run)
    assert list(tmp_path.iterdir()) == []
    for r_frame, m_frame in zip(ram_out, mm_out, strict=True):
        np.testing.assert_array_equal(m_frame, r_frame)


def test_memmap_temp_files_removed_on_error_and_cancel(tmp_path, monkeypatch):
    monkeypatch.setattr(flashvsr_engine, "MEMMAP_CANVAS_BYTES", 1)
    frames = _synthetic_frames(2, width=224, height=128)

    # Error mid-tiling removes temp files.
    err_engine, _ = _stub_engine(budget_bytes=20_000_000_000, tile_size_override=(512, 384))
    err_calls = 0
    orig_err_pipe = err_engine.pipeline

    def failing_pipeline(**kwargs):
        nonlocal err_calls
        err_calls += 1
        assert list(tmp_path.glob("localsr-flashvsr-*/*.dat"))
        if err_calls == 2:
            raise RuntimeError("simulated tile failure")
        return orig_err_pipe(**kwargs)

    err_engine.pipeline = failing_pipeline
    with pytest.raises(RuntimeError, match="simulated tile failure"):
        err_engine.process_frames(frames, temporary_directory=tmp_path)
    assert list(tmp_path.iterdir()) == []

    # Cancel between tiles removes temp files.
    cancel_tiles = threading.Event()
    cancel_engine, _ = _stub_engine(
        budget_bytes=20_000_000_000,
        tile_size_override=(512, 384),
        cancel_after_tile=0,
        cancel_event=cancel_tiles,
    )
    with pytest.raises(InterruptedError):
        cancel_engine.process_frames(
            frames,
            cancel_event=cancel_tiles,
            temporary_directory=tmp_path,
        )
    assert list(tmp_path.iterdir()) == []

    # Cancel mid-step removes temp files.
    cancel_step = threading.Event()
    step_engine, _ = _stub_engine(
        budget_bytes=20_000_000_000,
        tile_size_override=(512, 384),
        cancel_event=cancel_step,
        cancel_in_step=True,
    )
    with pytest.raises(InterruptedError):
        step_engine.process_frames(
            frames,
            cancel_event=cancel_step,
            temporary_directory=tmp_path,
        )
    assert list(tmp_path.iterdir()) == []
