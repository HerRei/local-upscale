"""End-to-end video pipeline tests using PyAV and a dummy model.

The dummy model performs nearest-neighbor upscaling so the test runs on CPU
without real weights. The pipeline is exercised fully: probe → decode →
per-frame inference → encode → atomic rename. Cancellation is also covered.
"""

from __future__ import annotations

import shutil
import subprocess
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
    VideoStageError,
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


def _make_synthetic_video_with_audio(path: Path, *, frames: int = 8, fps: int = 8) -> Path:
    """Write a deterministic one-second H.264/AAC source for remux tests."""
    container = av.open(str(path), mode="w", format="mp4")
    video = container.add_stream("libx264", rate=fps)
    video.width, video.height, video.pix_fmt = FRAME_WIDTH, FRAME_HEIGHT, "yuv420p"
    audio = container.add_stream("aac", rate=44_100)
    samples_per_frame = 44_100 // fps
    for index in range(frames):
        rgb = np.full((FRAME_HEIGHT, FRAME_WIDTH, 3), index * 25, dtype=np.uint8)
        for packet in video.encode(av.VideoFrame.from_ndarray(rgb, format="rgb24")):
            container.mux(packet)
        phase = np.linspace(0, 2 * np.pi, samples_per_frame, endpoint=False)
        samples = (np.sin(phase + index) * 8_000).astype(np.int16)
        frame = av.AudioFrame.from_ndarray(samples.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = 44_100
        for packet in audio.encode(frame):
            container.mux(packet)
    for stream in (video, audio):
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
    unrelated = Path(f"{destination}.tmp")
    unrelated.write_text("belongs to the user", encoding="utf-8")

    def bad_frames():
        yield np.full((10, 10, 3), 100, dtype=np.uint8)
        raise RuntimeError("simulated mid-encode failure")

    with pytest.raises(RuntimeError, match="simulated"):
        encode_video(bad_frames(), str(destination), fps=5.0, width=10, height=10)
    assert not destination.exists()
    assert unrelated.read_text(encoding="utf-8") == "belongs to the user"
    assert not list(tmp_path.glob(".broken.mp4.localsr-*.tmp"))


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


def test_frame_video_respects_smaller_requested_scale(synthetic_video, tmp_path):
    """A native 4x model can produce the 2x/3x dimensions shown by the UI."""
    adapter = DummyAdapter(scale=4)
    engine = InferenceEngine(adapter)
    output = tmp_path / "requested-2x.mp4"
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
        output_scale=2,
    )
    result = run_video_job(config, engine, threading.Event())
    probe = probe_video(result.output_path)
    assert (probe.width, probe.height) == (FRAME_WIDTH * 2, FRAME_HEIGHT * 2)


def test_frame_video_rejects_scale_above_model_native_scale(synthetic_video, tmp_path):
    adapter = DummyAdapter(scale=2)
    engine = InferenceEngine(adapter)
    config = VideoJobConfig(
        video_path=str(synthetic_video),
        model_path="dummy",
        output_video_path=str(tmp_path / "invalid.mp4"),
        model_info=adapter.model_info,
        device_str="cpu",
        precision_str="fp32",
        tile_size=64,
        halo=4,
        safe_memory=False,
        output_scale=4,
    )
    with pytest.raises(ValueError, match="native 2×"):
        run_video_job(config, engine, threading.Event())


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

    # Cancellation propagates through the frame generator so the encoder's
    # atomic-output guard removes both the partial and the final destination.
    with pytest.raises(InterruptedError, match="[Cc]ancelled"):
        run_video_job(
            config=config,
            engine=engine,
            cancel_event=cancel_event,
            frame_started_cb=cancel_after_first_frame,
        )
    assert not output.exists()
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


def test_deflicker_reduces_low_amplitude_static_flicker():
    """Reduce subtle static flicker without treating hard cuts as outliers."""
    from localsr.core.video_pipeline import _deflicker_frames

    # Five frames where the middle one is a bright outlier.
    frame_base = np.full((4, 4, 3), 50, dtype=np.uint8)
    frame_outlier = np.full((4, 4, 3), 60, dtype=np.uint8)
    frames = [
        frame_base.copy(),
        frame_base.copy(),
        frame_outlier,
        frame_base.copy(),
        frame_base.copy(),
    ]
    cancel = threading.Event()
    result = list(_deflicker_frames(iter(frames), window=3, cancel_event=cancel))
    assert len(result) == 5
    assert 50 < result[2].mean() < 60


def test_deflicker_preserves_genuine_motion():
    """When a value changes and stays changed, the median should track it."""
    from localsr.core.video_pipeline import _deflicker_frames

    frames = [
        np.full((4, 4, 3), 10, dtype=np.uint8),
        np.full((4, 4, 3), 10, dtype=np.uint8),
        np.full((4, 4, 3), 100, dtype=np.uint8),
        np.full((4, 4, 3), 100, dtype=np.uint8),
        np.full((4, 4, 3), 100, dtype=np.uint8),
    ]
    cancel = threading.Event()
    result = list(_deflicker_frames(iter(frames), window=3, cancel_event=cancel))
    assert len(result) == 5
    # The first frames should be dark, the last frames should be bright.
    assert result[0].mean() < 30
    assert result[-1].mean() > 80


def test_deflicker_window_one_is_passthrough():
    """A window of 1 means no de-flicker — frames pass through unchanged."""
    from localsr.core.video_pipeline import _deflicker_frames

    frames = [np.full((4, 4, 3), v, dtype=np.uint8) for v in (10, 50, 200)]
    cancel = threading.Event()
    result = list(_deflicker_frames(iter(frames), window=1, cancel_event=cancel))
    assert len(result) == 3
    assert result[0].mean() == 10
    assert result[1].mean() == 50
    assert result[2].mean() == 200


def test_deflicker_keeps_only_a_bounded_streaming_window():
    """Long videos must not retain every restored frame in memory."""
    import gc
    import weakref

    from localsr.core.video_pipeline import _deflicker_frames

    references = []

    def frames():
        for value in range(40):
            gc.collect()
            assert sum(reference() is not None for reference in references) <= 5
            frame = np.full((8, 8, 3), value, dtype=np.uint8)
            references.append(weakref.ref(frame))
            yield frame

    for _ in _deflicker_frames(frames(), window=3, cancel_event=threading.Event()):
        pass


def test_video_job_request_includes_deflicker_fields():
    import json

    from localsr.protocol.messages import VideoJobRequest

    req = VideoJobRequest(
        job_id="j1",
        video_path="/tmp/test.mp4",
        model_path="/tmp/general.pth",
        output_video_path="/tmp/out.mp4",
        container="mp4",
        crf=18,
        fps=None,
        device="cpu",
        tile_size=128,
        halo=16,
        precision="fp32",
        safe_memory=True,
        deflicker=True,
        deflicker_window=3,
    )
    data = json.loads(req.to_json())["data"]
    assert data["deflicker"] is True
    assert data["deflicker_window"] == 3


def test_encode_video_remuxes_source_audio(tmp_path):
    import av
    import numpy as np

    from localsr.core.video_io import encode_video, probe_video

    source = tmp_path / "with-audio.mp4"
    container = av.open(str(source), mode="w")
    video = container.add_stream("libx264", rate=8)
    video.width, video.height, video.pix_fmt = 64, 48, "yuv420p"
    audio = container.add_stream("aac", rate=44100)
    for index in range(8):
        rgb = np.full((48, 64, 3), index * 20, dtype=np.uint8)
        for packet in video.encode(av.VideoFrame.from_ndarray(rgb, format="rgb24")):
            container.mux(packet)
        samples = (np.sin(np.linspace(0, 3.14, 5512)) * 8000).astype(np.int16)
        frame = av.AudioFrame.from_ndarray(samples.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = 44100
        for packet in audio.encode(frame):
            container.mux(packet)
    for stream in (video, audio):
        for packet in stream.encode():
            container.mux(packet)
    container.close()

    frames = (np.full((96, 128, 3), i * 20, dtype=np.uint8) for i in range(8))
    destination = tmp_path / "out.mp4"
    encode_video(
        frames,
        str(destination),
        fps=8,
        width=128,
        height=96,
        audio_source=str(source),
    )
    assert destination.is_file()
    assert probe_video(str(destination)).frame_count == 8
    with av.open(str(destination)) as result:
        kinds = {stream.type for stream in result.streams}
    assert "audio" in kinds

    # A source without audio still encodes a valid silent video.
    silent = tmp_path / "silent.mp4"
    frames = (np.full((96, 128, 3), 40, dtype=np.uint8) for _ in range(4))
    encode_video(
        frames,
        str(silent),
        fps=8,
        width=128,
        height=96,
        audio_source=str(destination.with_name("missing.mp4")),
    )
    assert silent.is_file()


@pytest.mark.parametrize("container_format", ["mp4", "matroska"])
def test_generated_video_preserves_frame_count_timestamps_and_speed(tmp_path, container_format):
    suffix = "mp4" if container_format == "mp4" else "mkv"
    destination = tmp_path / f"timing.{suffix}"
    frame_count = 9
    fps = 30000 / 1001
    frames = (np.full((24, 32, 3), index * 10, dtype=np.uint8) for index in range(frame_count))
    encode_video(
        frames,
        str(destination),
        fps=fps,
        container_format=container_format,
        width=32,
        height=24,
    )

    decoded = list(decode_frames(str(destination)))
    assert len(decoded) == frame_count
    with av.open(str(destination)) as result:
        stream = next(stream for stream in result.streams if stream.type == "video")
        timestamps = [
            float(frame.pts * frame.time_base)
            for frame in result.decode(stream)
            if frame.pts is not None and frame.time_base is not None
        ]
    assert timestamps == sorted(timestamps)
    assert timestamps[-1] == pytest.approx((frame_count - 1) / fps, abs=0.02)
    assert probe_video(str(destination)).duration_seconds == pytest.approx(
        frame_count / fps, abs=0.08
    )


def test_trimmed_video_has_exact_range_and_synchronized_audio(tmp_path):
    synthetic_video = _make_synthetic_video_with_audio(tmp_path / "trim-source.mp4")
    adapter = DummyAdapter(scale=2)
    engine = InferenceEngine(adapter)
    output = tmp_path / "trimmed.mkv"
    started = []
    completed = []
    result = run_video_job(
        VideoJobConfig(
            video_path=str(synthetic_video),
            model_path="dummy",
            output_video_path=str(output),
            model_info=adapter.model_info,
            device_str="cpu",
            precision_str="fp32",
            tile_size=64,
            halo=4,
            safe_memory=False,
            container="matroska",
            start_frame=2,
            end_frame=4,
        ),
        engine,
        threading.Event(),
        frame_started_cb=lambda index, total: started.append((index, total)),
        frame_completed_cb=lambda index, total, _preview: completed.append((index, total)),
    )
    assert result.frames_processed == 3
    assert started == [(0, 3), (1, 3), (2, 3)]
    assert completed == [(0, 3), (1, 3), (2, 3)]
    assert len(list(decode_frames(str(output)))) == 3
    with av.open(str(output)) as container:
        assert {stream.type for stream in container.streams} == {"video", "audio"}
        assert float(container.duration) / 1_000_000 < 0.6


def test_mkv_remuxes_compatible_generated_subtitles(tmp_path):
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg CLI is unavailable for subtitle fixture generation")
    video = _make_synthetic_video(tmp_path / "plain.mp4")
    subtitles = tmp_path / "fixture.srt"
    subtitles.write_text(
        "1\n00:00:00,000 --> 00:00:00,400\nLocalSR fixture\n",
        encoding="utf-8",
    )
    source = tmp_path / "subtitled.mkv"
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(video),
            "-i",
            str(subtitles),
            "-map",
            "0:v:0",
            "-map",
            "1:0",
            "-c:v",
            "copy",
            "-c:s",
            "srt",
            str(source),
        ],
        check=True,
    )
    output = tmp_path / "subtitled-output.mkv"
    encode_video(
        (np.zeros((24, 32, 3), dtype=np.uint8) for _ in range(FRAME_COUNT)),
        str(output),
        fps=FPS,
        container_format="matroska",
        width=32,
        height=24,
        audio_source=str(source),
    )
    with av.open(str(output)) as result:
        assert any(stream.type == "subtitle" for stream in result.streams)


def test_remux_failure_names_stage_and_cleans_every_temporary(tmp_path, monkeypatch):
    import localsr.core.video_io as video_io

    source = tmp_path / "source.mp4"
    source.write_bytes(b"exists")
    destination = tmp_path / "failure.mp4"

    def fail_remux(*_args, **_kwargs):
        raise VideoStageError("audio remux", "fixture failure")

    monkeypatch.setattr(video_io, "_remux_source_streams", fail_remux)
    with pytest.raises(VideoStageError, match="audio remux.*fixture failure"):
        encode_video(
            (np.zeros((24, 32, 3), dtype=np.uint8) for _ in range(2)),
            str(destination),
            fps=5,
            width=32,
            height=24,
            audio_source=str(source),
        )
    assert not destination.exists()
    assert not list(tmp_path.glob(".failure.mp4.localsr-*.tmp"))
