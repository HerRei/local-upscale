"""End-to-end video pipeline tests using PyAV and a dummy model.

The dummy model performs nearest-neighbor upscaling so the test runs on CPU
without real weights. The pipeline is exercised fully: probe → decode →
per-frame inference → encode → atomic rename. Cancellation is also covered.
"""

from __future__ import annotations

import threading
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
import pytest
import torch
from torch import nn

from localsr.core.inference import InferenceEngine
from localsr.core.model_adapter import NormalizedModelInfo
from localsr.core.video_io import (
    decode_frames,
    encode_video,
    probe_video,
    uint8_chw_to_rgb_hwc,
)
from localsr.core.video_pipeline import VideoJobConfig, rgb_to_tensor, run_video_job

FRAME_WIDTH = 32
FRAME_HEIGHT = 24
FRAME_COUNT = 6
FPS = 12.0


def _make_synthetic_video(
    path: Path,
    width: int = FRAME_WIDTH,
    height: int = FRAME_HEIGHT,
    frames: int = FRAME_COUNT,
    fps: float = FPS,
) -> Path:
    """Write a small, deterministic mp4 with a different color per frame."""
    container = av.open(str(path), mode="w", format="mp4")
    stream = container.add_stream("libx264", rate=Fraction(int(fps * 1000), 1000))
    stream.width = width
    stream.height = height
    stream.pix_fmt = "yuv420p"
    stream.options = {"crf": "20", "preset": "ultrafast"}
    for index in range(frames):
        # A different solid color per frame so decode order is verifiable.
        rgb = np.full((height, width, 3), index * 30, dtype=np.uint8)
        rgb[:, :, 1] = 255 - index * 20
        frame = av.VideoFrame.from_ndarray(rgb, format="rgb24")
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()
    return path


class DummyModel(nn.Module):
    def __init__(self, scale):
        super().__init__()
        self.scale = scale

    def forward(self, x):
        return torch.nn.functional.interpolate(x, scale_factor=self.scale, mode="nearest")


class DummyAdapter:
    def __init__(self, scale, in_c=3, out_c=3):
        self.scale = scale
        self.model_info = NormalizedModelInfo(
            architecture="Dummy",
            scale=scale,
            in_channels=in_c,
            out_channels=out_c,
            tiling_supported=True,
            half_supported=False,
            size_requirements_min=1,
            size_requirements_mult=1,
            filename="dummy.pth",
            warnings=[],
        )

    def load(self, path, device, precision):
        return DummyModel(self.scale).to(device).to(precision), None

    def release(self):
        pass


@pytest.fixture
def synthetic_video(tmp_path):
    path = tmp_path / "input.mp4"
    _make_synthetic_video(path)
    return path


def test_probe_video_returns_dimensions_and_fps(synthetic_video):
    probe = probe_video(str(synthetic_video))
    assert probe.width == FRAME_WIDTH
    assert probe.height == FRAME_HEIGHT
    assert probe.fps == pytest.approx(FPS, abs=0.1)
    assert probe.codec in {"h264", "mpeg4", "avc1"} or probe.codec.startswith("h264")


def test_decode_frames_yields_every_frame_in_order(synthetic_video):
    frames = list(decode_frames(str(synthetic_video)))
    assert len(frames) == FRAME_COUNT
    for index, (frame_index, rgb) in enumerate(frames):
        assert frame_index == index
        assert rgb.shape == (FRAME_HEIGHT, FRAME_WIDTH, 3)
        assert rgb.dtype == np.uint8


def test_decode_frames_respects_start_and_end_bounds(synthetic_video):
    frames = list(decode_frames(str(synthetic_video), start_frame=2, end_frame=4))
    assert [index for index, _ in frames] == [2, 3, 4]


def test_encode_video_produces_playable_file_with_correct_dimensions(tmp_path):
    width, height, count = 40, 30, 4
    frames = (np.full((height, width, 3), 200, dtype=np.uint8) for _ in range(count))
    destination = tmp_path / "out.mp4"
    result = encode_video(
        frames,
        str(destination),
        fps=10.0,
        width=width,
        height=height,
    )
    assert result == str(destination)
    assert destination.is_file()
    probe = probe_video(str(destination))
    assert probe.width == width
    assert probe.height == height
    assert probe.fps == pytest.approx(10.0, abs=0.1)


def test_encode_video_atomic_rename_no_tmp_left_on_failure(tmp_path):
    destination = tmp_path / "broken.mp4"

    def bad_frames():
        yield np.full((10, 10, 3), 100, dtype=np.uint8)
        raise RuntimeError("simulated mid-encode failure")

    with pytest.raises(RuntimeError, match="simulated"):
        encode_video(bad_frames(), str(destination), fps=5.0, width=10, height=10)
    assert not destination.exists()
    assert not (tmp_path / "broken.mp4.tmp").exists()


def test_rgb_to_tensor_and_back_round_trip():
    rgb = np.array([[[10, 20, 30], [40, 50, 60]]], dtype=np.uint8)
    tensor = rgb_to_tensor(rgb)
    assert tensor.shape == (3, 1, 2)
    assert tensor.dtype == torch.float32
    back = uint8_chw_to_rgb_hwc((tensor * 255).byte().numpy())
    assert back.shape == (1, 2, 3)
    assert np.array_equal(back, rgb)


def test_run_video_job_upscales_every_frame(synthetic_video, tmp_path):
    scale = 2
    adapter = DummyAdapter(scale)
    engine = InferenceEngine(adapter)
    output = tmp_path / "upscaled.mp4"

    config = VideoJobConfig(
        video_path=str(synthetic_video),
        model_path="dummy",
        output_video_path=str(output),
        model_info=adapter.model_info,
        device_str="cpu",
        precision_str="fp32",
        tile_size=64,
        halo=4,
        safe_memory=False,
    )

    started = []
    completed = []
    progress_events = []

    result = run_video_job(
        config=config,
        engine=engine,
        cancel_event=threading.Event(),
        frame_started_cb=lambda idx, total: started.append((idx, total)),
        frame_completed_cb=lambda idx, total, b64: completed.append((idx, total, b64)),
        progress_cb=lambda done, total, elapsed: progress_events.append((done, total, elapsed)),
    )

    assert result.frames_processed == FRAME_COUNT
    assert output.is_file()
    probe = probe_video(str(output))
    assert probe.width == FRAME_WIDTH * scale
    assert probe.height == FRAME_HEIGHT * scale
    assert probe.fps == pytest.approx(FPS, abs=0.1)

    # The first decode of the output should yield the same number of frames.
    out_frames = list(decode_frames(str(output)))
    assert len(out_frames) == FRAME_COUNT

    # frame_started_cb fired once per frame, in order.
    assert [idx for idx, _ in started] == list(range(FRAME_COUNT))
    # frame_completed_cb fired once per frame.
    assert len(completed) == FRAME_COUNT
    # progress_cb fired at least once per frame.
    assert len(progress_events) >= FRAME_COUNT
    assert progress_events[-1][0] == FRAME_COUNT


def test_run_video_job_cancel_stops_before_encode(synthetic_video, tmp_path):
    scale = 2
    adapter = DummyAdapter(scale)
    engine = InferenceEngine(adapter)
    output = tmp_path / "cancelled.mp4"

    config = VideoJobConfig(
        video_path=str(synthetic_video),
        model_path="dummy",
        output_video_path=str(output),
        model_info=adapter.model_info,
        device_str="cpu",
        precision_str="fp32",
        tile_size=64,
        halo=4,
        safe_memory=False,
    )

    cancel_event = threading.Event()

    def cancel_after_first_frame(idx, total):
        if idx >= 1:
            cancel_event.set()

    # The encoder will receive fewer frames than expected because the
    # generator stops early. The pipeline should still produce a valid
    # (short) output rather than crash.
    result = run_video_job(
        config=config,
        engine=engine,
        cancel_event=cancel_event,
        frame_started_cb=cancel_after_first_frame,
    )
    # At most one frame was processed before the cancel signal was observed.
    assert result.frames_processed <= 2
    # The output file may or may not exist depending on where cancellation
    # was caught; either is acceptable as long as no temp file is leaked.
    assert not (tmp_path / "cancelled.mp4.tmp").exists()


def test_video_job_protocol_messages_include_video_specific_types():
    from localsr.protocol.messages import (
        VideoFrameCompleted,
        VideoFrameStarted,
        VideoJobCompleted,
    )

    started = VideoFrameStarted(job_id="j1", frame_index=3, total_frames=10)
    completed = VideoFrameCompleted(
        job_id="j1",
        frame_index=3,
        total_frames=10,
        frames_processed=4,
        elapsed_seconds=12.5,
        estimated_remaining_seconds=25.0,
        jpeg_base64="",
    )
    job_done = VideoJobCompleted(
        job_id="j1",
        output_path="/tmp/out.mp4",
        frames_processed=10,
        elapsed_seconds=40.0,
        inference_seconds=38.0,
    )

    import json

    assert json.loads(started.to_json())["type"] == "video_frame_started"
    assert json.loads(completed.to_json())["type"] == "video_frame_completed"
    assert json.loads(job_done.to_json())["type"] == "video_job_completed"
