"""Synthetic quality, safety, device-routing, and streaming tests for core deflicker."""

from __future__ import annotations

import threading
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import av
import cv2
import numpy as np
import pytest
import torch

from localsr.core.deflicker import (
    AnalysisFrame,
    deflicker_stream,
    prepare_analysis_frame,
    resolve_deflicker_device,
)
from localsr.core.video_io import TimedVideoFrame, decode_timed_frames
from localsr.core.video_pipeline import VideoJobConfig, run_video_job


def _luma(rgb: np.ndarray) -> np.ndarray:
    arr = rgb.astype(np.float32)
    return 0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]


def _psnr(a: np.ndarray, b: np.ndarray, mask: np.ndarray | None = None) -> float:
    diff = (a.astype(np.float32) - b.astype(np.float32)) ** 2
    if mask is not None:
        diff = diff[mask]
    mse = float(diff.mean())
    return 99.0 if mse == 0.0 else float(10.0 * np.log10((255.0**2) / mse))


def _make_textured_frame(height: int, width: int, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    base = (
        128.0 + 40.0 * np.sin(xx * 0.12) * np.cos(yy * 0.10) + 25.0 * np.sin(xx * 0.04 + yy * 0.05)
    )
    tex = cv2.GaussianBlur(rng.normal(0.0, 22.0, (height, width)).astype(np.float32), (3, 3), 0.7)
    rgb = np.stack([base + tex, base + tex * 0.8 + 10.0, base + tex * 0.6 - 10.0], axis=-1)
    return np.clip(rgb, 24.0, 232.0).round().astype(np.uint8)


def _run_pairs(
    src: list[np.ndarray],
    raw: list[np.ndarray],
    *,
    device_str: str = "cpu",
    diagnostics: list[tuple[np.ndarray, bool]] | None = None,
) -> list[np.ndarray]:
    out = list(
        deflicker_stream(
            zip(raw, src, strict=True),
            device_str=device_str,
            diagnostics=diagnostics,
        )
    )
    assert all(isinstance(frame, np.ndarray) for frame in out)
    return [frame for frame in out if isinstance(frame, np.ndarray)]


def test_deflicker_reduces_static_region_shimmer_by_at_least_35_percent():
    rng = np.random.default_rng(101)
    src_base = _make_textured_frame(120, 160, seed=1)
    out_base = cv2.resize(src_base, (320, 240), interpolation=cv2.INTER_CUBIC)
    count = 30
    src = [src_base] * count
    raw = [
        np.clip(
            out_base.astype(np.float32) + rng.normal(0.0, 6.0, out_base.shape),
            0.0,
            255.0,
        )
        .round()
        .astype(np.uint8)
        for _ in range(count)
    ]

    proc = _run_pairs(src, raw, device_str="cpu")
    raw_shimmer = float(
        np.mean([np.abs(_luma(raw[t]) - _luma(raw[t - 1])).mean() for t in range(1, count)])
    )
    proc_shimmer = float(
        np.mean([np.abs(_luma(proc[t]) - _luma(proc[t - 1])).mean() for t in range(1, count)])
    )
    reduction = (raw_shimmer - proc_shimmer) / raw_shimmer
    assert reduction >= 0.35


def test_deflicker_reduces_source_flicker_and_improves_psnr():
    rng = np.random.default_rng(202)
    src_base = _make_textured_frame(120, 160, seed=2)
    clean_out = cv2.resize(src_base, (320, 240), interpolation=cv2.INTER_CUBIC)
    count = 30

    yy, xx = np.mgrid[0:120, 0:160].astype(np.float32)
    spatial_wave = np.sin(2.0 * np.pi * xx / 160.0) * np.cos(np.pi * yy / 120.0)
    global_gains = 1.0 + rng.normal(0.0, 0.07, size=count)

    src: list[np.ndarray] = []
    raw: list[np.ndarray] = []
    for t in range(count):
        phase = 1.0 if t % 2 == 0 else -1.0
        g_src = np.clip(global_gains[t] * (1.0 + phase * 0.06 * spatial_wave), 0.82, 1.20).astype(
            np.float32
        )
        g_out = cv2.resize(g_src, (320, 240), interpolation=cv2.INTER_LINEAR)
        src.append(
            np.clip(src_base.astype(np.float32) * g_src[..., None], 0, 255).round().astype(np.uint8)
        )
        raw.append(
            np.clip(clean_out.astype(np.float32) * g_out[..., None], 0, 255)
            .round()
            .astype(np.uint8)
        )

    proc = _run_pairs(src, raw, device_str="cpu")
    clean_luma = float(_luma(clean_out).mean())
    raw_luma_err = float(np.mean([abs(float(_luma(frame).mean()) - clean_luma) for frame in raw]))
    proc_luma_err = float(np.mean([abs(float(_luma(frame).mean()) - clean_luma) for frame in proc]))
    raw_psnr = float(np.mean([_psnr(frame, clean_out) for frame in raw]))
    proc_psnr = float(np.mean([_psnr(frame, clean_out) for frame in proc]))

    assert (raw_luma_err - proc_luma_err) / raw_luma_err >= 0.50
    assert proc_psnr > raw_psnr


def test_deflicker_preserves_lighting_step_change():
    src_base = _make_textured_frame(120, 160, seed=3)
    out_base = cv2.resize(src_base, (320, 240), interpolation=cv2.INTER_CUBIC)
    count = 35
    src = []
    raw = []
    for t in range(count):
        factor = 1.35 if t >= 20 else 1.0
        src.append(np.clip(src_base.astype(np.float32) * factor, 0, 255).round().astype(np.uint8))
        raw.append(np.clip(out_base.astype(np.float32) * factor, 0, 255).round().astype(np.uint8))

    proc = _run_pairs(src, raw, device_str="cpu")
    for t in range(20, 25):
        raw_mean = float(_luma(raw[t]).mean())
        proc_mean = float(_luma(proc[t]).mean())
        assert abs(proc_mean - raw_mean) / raw_mean <= 0.01


def test_deflicker_does_not_blend_across_hard_scene_cut():
    scene_a_src = _make_textured_frame(120, 160, seed=4)
    scene_a_out = cv2.resize(scene_a_src, (320, 240), interpolation=cv2.INTER_CUBIC)
    # Distinct second scene with inverted/shifted structure and colour so cut diff >> 25.
    scene_b_src = 255 - _make_textured_frame(120, 160, seed=99)
    scene_b_src[:, :, 0] = np.clip(scene_b_src[:, :, 0].astype(np.int16) + 50, 0, 255).astype(
        np.uint8
    )
    scene_b_out = cv2.resize(scene_b_src, (320, 240), interpolation=cv2.INTER_CUBIC)

    src = [scene_a_src] * 20 + [scene_b_src] * 10
    raw = [scene_a_out] * 20 + [scene_b_out] * 10
    diagnostics: list[tuple[np.ndarray, bool]] = []
    proc = _run_pairs(src, raw, device_str="cpu", diagnostics=diagnostics)

    assert diagnostics[20][1] is True
    for t in range(20, 23):
        assert _psnr(proc[t], raw[t]) >= 50.0


def test_deflicker_leaves_moving_object_ghost_free():
    rng = np.random.default_rng(505)
    src_bg = _make_textured_frame(120, 160, seed=5)
    out_bg = cv2.resize(src_bg, (320, 240), interpolation=cv2.INTER_CUBIC)
    count = 30
    src: list[np.ndarray] = []
    raw: list[np.ndarray] = []
    moving_masks: list[np.ndarray] = []

    for t in range(count):
        s = src_bg.copy()
        o = out_bg.copy()
        x0 = 15 + t * 3
        s[40:68, x0 : x0 + 26] = [215, 45, 45]
        o_noisy = (
            np.clip(o.astype(np.float32) + rng.normal(0.0, 2.0, o.shape), 0, 255)
            .round()
            .astype(np.uint8)
        )
        o_noisy[80:136, x0 * 2 : (x0 + 26) * 2] = [215, 45, 45]
        mask = np.zeros((240, 320), dtype=bool)
        mask[80:136, x0 * 2 : (x0 + 26) * 2] = True
        src.append(s)
        raw.append(o_noisy)
        moving_masks.append(mask)

    proc = _run_pairs(src, raw, device_str="cpu")
    deviations = [
        float(np.abs(_luma(proc[t]) - _luma(raw[t]))[moving_masks[t]].mean())
        for t in range(1, count)
    ]
    assert float(np.mean(deviations)) < 1.5


def test_deflicker_clean_clip_stage_a_gains_are_exact_one():
    src_base = _make_textured_frame(120, 160, seed=6)
    out_base = cv2.resize(src_base, (320, 240), interpolation=cv2.INTER_CUBIC)
    count = 20
    src = []
    raw = []
    for t in range(count):
        s = src_base.copy()
        o = out_base.copy()
        x0 = 10 + t * 3
        s[30:55, x0 : x0 + 20] = [200, 180, 40]
        o[60:110, x0 * 2 : (x0 + 20) * 2] = [200, 180, 40]
        src.append(s)
        raw.append(o)

    diagnostics: list[tuple[np.ndarray, bool]] = []
    proc = _run_pairs(src, raw, device_str="cpu", diagnostics=diagnostics)
    assert len(diagnostics) == count
    for gain, _cut in diagnostics:
        np.testing.assert_array_equal(gain, np.ones((12, 16), dtype=np.float32))
    # Stage A leaves the clip untouched; Stage B only touches <=1px bilinear mask edges.
    for actual, expected in zip(proc, raw, strict=True):
        assert _psnr(actual, expected) >= 55.0


def test_deflicker_keep_discard_callbacks_and_timing_metadata():
    src_base = _make_textured_frame(48, 64, seed=7)
    frames = [
        TimedVideoFrame(
            index=i,
            rgb=np.clip(src_base.astype(np.int16) + i, 0, 255).astype(np.uint8),
            timestamp=Fraction(i, 24),
            duration=Fraction(1, 24),
            time_base=Fraction(1, 24),
        )
        for i in range(6)
    ]

    reported: list[int] = []
    out = list(
        deflicker_stream(
            ((frame, frame.rgb) for frame in frames),
            on_frame=reported.append,
        )
    )
    assert reported == [0, 1, 2, 3, 4, 5]
    assert [f.index for f in out] == [f.index for f in frames]
    assert [f.timestamp for f in out] == [f.timestamp for f in frames]
    assert [f.duration for f in out] == [f.duration for f in frames]
    assert [f.time_base for f in out] == [f.time_base for f in frames]

    cancel = threading.Event()

    def upstream():
        for frame in frames:
            yield (frame, frame.rgb)
        cancel.set()
        raise InterruptedError("stopped")

    kept: list[TimedVideoFrame] = []
    kept_reports: list[int] = []
    with pytest.raises(InterruptedError):
        for frame in deflicker_stream(
            upstream(),
            cancel_event=cancel,
            on_frame=kept_reports.append,
            keep=lambda: True,
        ):
            assert isinstance(frame, TimedVideoFrame)
            kept.append(frame)
    assert len(kept) == 6
    assert kept_reports == [0, 1, 2, 3, 4, 5]

    cancel.clear()
    discarded: list[TimedVideoFrame] = []
    with pytest.raises(InterruptedError):
        for frame in deflicker_stream(
            upstream(),
            cancel_event=cancel,
            keep=lambda: False,
        ):
            assert isinstance(frame, TimedVideoFrame)
            discarded.append(frame)
    # 3-frame look-ahead holds back frames 3, 4, 5 when discarding.
    assert len(discarded) == 3


def test_prepare_analysis_frame_caps_longest_side_at_640():
    large = np.zeros((1080, 1920, 3), dtype=np.uint8)
    analysis = prepare_analysis_frame(large)
    assert isinstance(analysis, AnalysisFrame)
    assert analysis.y.shape == (360, 640)
    assert analysis.gray.shape == (360, 640)
    assert analysis.y64.shape == (48, 64)


def test_resolve_deflicker_device_routes_directml_and_openvino_to_cpu():
    assert resolve_deflicker_device("cpu") == torch.device("cpu")
    assert resolve_deflicker_device("directml:0") == torch.device("cpu")
    assert resolve_deflicker_device("openvino:GPU") == torch.device("cpu")
    if torch.backends.mps.is_available():
        assert resolve_deflicker_device("mps") == torch.device("mps")


@pytest.mark.skipif(not torch.backends.mps.is_available(), reason="MPS unavailable")
def test_deflicker_mps_matches_cpu_behavior():
    rng = np.random.default_rng(303)
    src_base = _make_textured_frame(120, 160, seed=8)
    clean_out = cv2.resize(src_base, (320, 240), interpolation=cv2.INTER_CUBIC)
    src = []
    raw = []
    for _ in range(16):
        g = float(1.0 + rng.normal(0.0, 0.06))
        src.append(np.clip(src_base.astype(np.float32) * g, 0, 255).round().astype(np.uint8))
        raw.append(
            np.clip(
                clean_out.astype(np.float32) * g + rng.normal(0.0, 4.0, clean_out.shape),
                0,
                255,
            )
            .round()
            .astype(np.uint8)
        )
    cpu_out = _run_pairs(src, raw, device_str="cpu")
    mps_out = _run_pairs(src, raw, device_str="mps")
    for a, b in zip(cpu_out, mps_out, strict=True):
        assert _psnr(a, b) >= 42.0


def test_end_to_end_video_job_deflicker_reports_stage_and_preserves_frames(tmp_path: Path):
    source = tmp_path / "flicker.mp4"
    output = tmp_path / "out.mp4"
    base = _make_textured_frame(48, 64, seed=9)
    with av.open(str(source), mode="w", format="mp4") as container:
        stream = container.add_stream("libvpx-vp9", rate=12)
        stream.width, stream.height, stream.pix_fmt = 64, 48, "yuv420p"
        stream.options = {"lossless": "1", "lag-in-frames": "0"}
        for t in range(8):
            gain = 1.0 + (0.08 if t % 2 == 0 else -0.08)
            rgb = np.clip(base.astype(np.float32) * gain, 0, 255).round().astype(np.uint8)
            for packet in stream.encode(av.VideoFrame.from_ndarray(rgb, format="rgb24")):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)

    class Identity1xEngine:
        def load_model(self, *args, **kwargs):
            pass

        def switch_model(self, *args, **kwargs):
            pass

        def process_frame(self, *, img_tensor, **kwargs):
            return (img_tensor.clamp(0, 1).numpy() * 255.0).round().astype(np.uint8)

    stages: list[tuple[str, int | None, int | None]] = []
    config = VideoJobConfig(
        video_path=str(source),
        model_path="identity.pth",
        output_video_path=str(output),
        model_info=SimpleNamespace(scale=1),
        device_str="cpu",
        precision_str="fp32",
        tile_size=64,
        halo=4,
        safe_memory=False,
        deflicker=True,
        deflicker_window=9,
    )
    result = run_video_job(
        config=config,
        engine=Identity1xEngine(),  # type: ignore[arg-type]
        cancel_event=threading.Event(),
        stage_cb=lambda stage, done=None, total=None: stages.append((stage, done, total)),
    )
    assert result.frames_processed == 8
    deflicker_stages = [(done, total) for stage, done, total in stages if stage == "deflickering"]
    assert deflicker_stages == [(i + 1, 8) for i in range(8)]
    assert len(list(decode_timed_frames(str(output)))) == 8


def test_deflicker_rejects_float_frames():
    float_frame = np.full((32, 32, 3), 0.5, dtype=np.float32)
    with pytest.raises(ValueError, match="uint8"):
        prepare_analysis_frame(float_frame)
    with pytest.raises(ValueError, match="uint8"):
        list(deflicker_stream([(float_frame, float_frame)]))


def test_run_video_job_rejects_deflicker_with_hdr_preservation(tmp_path: Path, monkeypatch):
    from dataclasses import replace

    from localsr.core import video_pipeline

    source = tmp_path / "hdr.mp4"
    with av.open(str(source), mode="w", format="mp4") as container:
        stream = container.add_stream("libvpx-vp9", rate=12)
        stream.width, stream.height, stream.pix_fmt = 32, 32, "yuv420p"
        rgb = np.zeros((32, 32, 3), dtype=np.uint8)
        for packet in stream.encode(av.VideoFrame.from_ndarray(rgb, format="rgb24")):
            container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)

    real_probe = video_pipeline.probe_video
    monkeypatch.setattr(
        video_pipeline,
        "probe_video",
        lambda path: replace(real_probe(path), hdr_format="PQ"),
    )
    monkeypatch.setattr(video_pipeline, "validate_hdr_encoder", lambda *args: None)
    config = VideoJobConfig(
        video_path=str(source),
        model_path="hat.pth",
        output_video_path=str(tmp_path / "out.mkv"),
        model_info=SimpleNamespace(architecture="HAT", scale=2, in_channels=3, out_channels=3),
        device_str="cpu",
        precision_str="fp16",
        tile_size=64,
        halo=0,
        safe_memory=False,
        deflicker=True,
        hdr_mode="preserve",
    )
    with pytest.raises(ValueError, match="De-flicker is not yet supported with HDR"):
        run_video_job(config, SimpleNamespace(), threading.Event())  # type: ignore[arg-type]


def test_deflicker_imports_and_runs_without_opencv_optical_flow(monkeypatch):
    """Packaged OpenCV builds may lack the video module; the worker must still start."""
    import importlib

    import cv2

    import localsr.core.deflicker as deflicker_module

    monkeypatch.delattr(cv2, "DISOpticalFlow_create", raising=False)
    monkeypatch.delattr(cv2, "DISOPTICAL_FLOW_PRESET_FAST", raising=False)
    module = importlib.reload(deflicker_module)
    try:
        rng = np.random.default_rng(3)
        base = rng.integers(40, 200, (48, 64, 3), dtype=np.uint8)
        frames = [base.copy() for _ in range(8)]
        out = list(module.deflicker_stream(zip(frames, frames, strict=True)))
        assert len(out) == 8
        assert all(frame.shape == base.shape and frame.dtype == np.uint8 for frame in out)
    finally:
        monkeypatch.undo()
        importlib.reload(deflicker_module)
