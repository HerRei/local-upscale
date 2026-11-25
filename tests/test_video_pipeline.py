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
    decode_timed_frames,
    encode_video,
    probe_video,
    uint8_chw_to_rgb_hwc,
)
from localsr.core.video_pipeline import VideoJobConfig, rgb_to_tensor, run_video_job

FRAME_WIDTH = 32
FRAME_HEIGHT = 24
FRAME_COUNT = 6


def decode_frames(path, start_frame=None, end_frame=None):
    """(index, rgb) pairs, in decode order, for compact assertions."""
    return [(frame.index, frame.rgb) for frame in decode_timed_frames(path, start_frame, end_frame)]


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
    stream = container.add_stream("libvpx-vp9", rate=Fraction(int(fps * 1000), 1000))
    stream.width = width
    stream.height = height
    stream.pix_fmt = "yuv420p"
    stream.options = {"crf": "20", "b": "0", "deadline": "realtime", "cpu-used": "8"}
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
    """Write a deterministic one-second VP9/Opus source for remux tests."""
    container = av.open(str(path), mode="w", format="mp4")
    video = container.add_stream("libvpx-vp9", rate=fps)
    video.width, video.height, video.pix_fmt = FRAME_WIDTH, FRAME_HEIGHT, "yuv420p"
    audio = container.add_stream("libopus", rate=48_000)
    samples_per_frame = 48_000 // fps
    for index in range(frames):
        rgb = np.full((FRAME_HEIGHT, FRAME_WIDTH, 3), (index * 25) % 256, dtype=np.uint8)
        for packet in video.encode(av.VideoFrame.from_ndarray(rgb, format="rgb24")):
            container.mux(packet)
        phase = np.linspace(0, 2 * np.pi, samples_per_frame, endpoint=False)
        samples = (np.sin(phase + index) * 8_000).astype(np.int16)
        frame = av.AudioFrame.from_ndarray(samples.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = 48_000
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
    assert probe.codec == "vp9"


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
        yield np.full((64, 64, 3), 100, dtype=np.uint8)
        raise RuntimeError("simulated mid-encode failure")

    # SVT-AV1 fails to allocate for tiny frames on the Windows runner.
    with pytest.raises(RuntimeError, match="simulated"):
        encode_video(bad_frames(), str(destination), fps=5.0, width=64, height=64)
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
        progress_cb=lambda done, total, elapsed, _remaining: progress_events.append(
            (done, total, elapsed)
        ),
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


def test_a_video_that_does_not_fit_is_refused_before_any_frame_and_leaves_nothing(
    synthetic_video, tmp_path, monkeypatch
):
    import shutil

    from localsr.core import disk_space

    monkeypatch.delenv("LOCALSR_DISK_RESERVE_BYTES")

    monkeypatch.setattr(
        disk_space,
        "disk_usage",
        lambda _path: shutil._ntuple_diskusage(100 * 10**9, 99 * 10**9, 10**9),
    )
    adapter = DummyAdapter(2)
    folder = tmp_path / "out"
    folder.mkdir()
    config = VideoJobConfig(
        video_path=str(synthetic_video),
        model_path="dummy",
        output_video_path=str(folder / "clip.mp4"),
        model_info=adapter.model_info,
        device_str="cpu",
        precision_str="fp32",
        tile_size=64,
        halo=4,
        safe_memory=False,
    )
    with pytest.raises(disk_space.DiskFullError, match="Not enough free disk space"):
        run_video_job(config, InferenceEngine(adapter), threading.Event())
    assert list(folder.iterdir()) == []


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
    cancel_event.set()
    with pytest.raises(InterruptedError, match="[Cc]ancelled"):
        run_video_job(config=config, engine=engine, cancel_event=cancel_event)
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
    """Reduce subtle output shimmer on a static source without breaking cuts."""
    from localsr.core.video_pipeline import _deflicker_frames

    rng = np.random.default_rng(17)
    src = np.full((24, 24, 3), 100, dtype=np.uint8)
    raw = [
        np.clip(100.0 + rng.normal(0.0, 6.0, (24, 24, 3)), 0, 255).round().astype(np.uint8)
        for _ in range(10)
    ]
    cancel = threading.Event()
    result = list(
        _deflicker_frames(zip(raw, [src] * 10, strict=True), window=3, cancel_event=cancel)
    )
    assert len(result) == 10
    raw_diff = float(
        np.mean([np.abs(raw[i].astype(np.float32) - raw[i - 1]).mean() for i in range(1, 10)])
    )
    out_diff = float(
        np.mean([np.abs(result[i].astype(np.float32) - result[i - 1]).mean() for i in range(1, 10)])
    )
    assert out_diff < raw_diff * 0.65


def test_deflicker_preserves_genuine_motion():
    """When a value steps and stays changed, deflicker preserves the step."""
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


def test_deflicker_window_is_accepted_for_compatibility():
    """``window`` stays accepted for request compatibility and does not alter output."""
    from localsr.core.video_pipeline import _deflicker_frames

    frames = [np.full((16, 16, 3), 40, dtype=np.uint8) for _ in range(5)]
    out_one = list(_deflicker_frames(iter(frames), window=1, cancel_event=threading.Event()))
    out_nine = list(_deflicker_frames(iter(frames), window=9, cancel_event=threading.Event()))
    assert len(out_one) == len(out_nine) == 5
    for a, b in zip(out_one, out_nine, strict=True):
        np.testing.assert_array_equal(a, b)


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
    video = container.add_stream("libvpx-vp9", rate=8)
    video.width, video.height, video.pix_fmt = 64, 48, "yuv420p"
    audio = container.add_stream("libopus", rate=48000)
    for index in range(8):
        rgb = np.full((48, 64, 3), index * 20, dtype=np.uint8)
        for packet in video.encode(av.VideoFrame.from_ndarray(rgb, format="rgb24")):
            container.mux(packet)
        samples = (np.sin(np.linspace(0, 3.14, 6000)) * 8000).astype(np.int16)
        frame = av.AudioFrame.from_ndarray(samples.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = 48000
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


def test_face_aware_video_runs_frames_without_a_face_on_the_general_model(
    synthetic_video, tmp_path, monkeypatch
):
    """The face checkpoint is loaded last; a frame without faces must still use the other."""
    from localsr.core import video_pipeline
    from localsr.core.face_detection import FaceBox, FaceMask

    class Constant(nn.Module):
        def __init__(self, scale, color):
            super().__init__()
            self.scale, self.color = scale, color

        def forward(self, x):
            _, _, height, width = x.shape
            return torch.full((1, 3, height * self.scale, width * self.scale), self.color)

    class TwoModels(DummyAdapter):
        def load(self, path, device, precision):
            color = 0.9 if "face" in path else 0.1
            return Constant(self.scale, color).to(device).to(precision), None

    adapter = TwoModels(2)
    engine = InferenceEngine(adapter)
    faces_in = {0: True, 1: False, 2: True, 3: False, 4: False, 5: False}
    calls = []

    def detect(rgb, cancel_event=None):
        index = len(calls)
        calls.append(index)
        full = np.ones(rgb.shape[:2], dtype=bool)
        if faces_in[index]:
            return FaceMask(mask=full, boxes=(FaceBox(0, 0, rgb.shape[1], rgb.shape[0], 1.0),))
        return FaceMask(mask=~full, boxes=())

    monkeypatch.setattr(video_pipeline, "detect_faces", detect)
    output = tmp_path / "faces.mp4"
    config = VideoJobConfig(
        video_path=str(synthetic_video),
        model_path="general.pth",
        output_video_path=str(output),
        model_info=adapter.model_info,
        device_str="cpu",
        precision_str="fp32",
        tile_size=64,
        halo=4,
        safe_memory=False,
        face_model_path="face.pth",
        face_model_info=adapter.model_info,
        face_fidelity=1.0,
        face_detection_interval=0,
    )
    run_video_job(config=config, engine=engine, cancel_event=threading.Event())
    means = [float(rgb.mean()) for _, rgb in decode_frames(str(output))]
    expected = [True, False, True, False, False, False]
    assert [mean > 128 for mean in means] == expected, means


def test_video_restore_without_fix_stages_runs_only_video_options(tmp_path):
    from test_frame_interpolation import RecordingSynthesizer

    source = _make_synthetic_video_with_audio(tmp_path / "source_audio.mp4", frames=4, fps=8)

    class FailOnLoadAdapter:
        def load(self, path, device, precision):
            raise AssertionError(f"Unexpected model load: {path}")

        def release(self):
            pass

    engine = InferenceEngine(FailOnLoadAdapter())
    output = tmp_path / "restore_options_only.mp4"
    config = VideoJobConfig(
        video_path=str(source),
        model_path="",
        output_video_path=str(output),
        model_info=None,
        device_str="cpu",
        precision_str="fp32",
        tile_size=64,
        halo=4,
        safe_memory=False,
        output_scale=1,
        audio_path=str(source),
        deflicker=True,
        interpolation="x2",
        interpolation_order="after_upscale",
        preprocess_stages=(),
    )
    result = run_video_job(
        config=config,
        engine=engine,
        cancel_event=threading.Event(),
        synthesizer=RecordingSynthesizer(),
    )
    assert result.frames_processed == 4
    probe = probe_video(str(output))
    assert (probe.width, probe.height) == (FRAME_WIDTH, FRAME_HEIGHT)
    assert len(list(decode_frames(str(output)))) == 8


def test_video_restore_at_1x_preserves_dimensions_frames_timing_and_audio(tmp_path):
    source = _make_synthetic_video_with_audio(tmp_path / "source_audio.mp4", frames=8, fps=8)
    adapter = DummyAdapter(scale=1)
    engine = InferenceEngine(adapter)
    output = tmp_path / "restored_1x.mp4"
    config = VideoJobConfig(
        video_path=str(source),
        model_path="restore.pth",
        output_video_path=str(output),
        model_info=adapter.model_info,
        device_str="cpu",
        precision_str="fp32",
        tile_size=64,
        halo=4,
        safe_memory=False,
        output_scale=1,
        audio_path=str(source),
    )
    run_video_job(config=config, engine=engine, cancel_event=threading.Event())
    probe = probe_video(str(output))
    assert (probe.width, probe.height) == (FRAME_WIDTH, FRAME_HEIGHT)
    assert probe.fps == pytest.approx(8.0, abs=0.2)
    frames = list(decode_frames(str(output)))
    assert len(frames) == 8
    with av.open(str(output)) as container:
        assert any(s.type == "audio" for s in container.streams)


def test_video_fix_first_and_upscale_with_1x_and_interpolation_and_unstable_fallback(
    tmp_path,
):
    from test_frame_interpolation import RecordingSynthesizer

    from localsr.core.inference import UnstableRestorationError
    from localsr.core.video_pipeline import VideoFixStage

    source = _make_synthetic_video_with_audio(tmp_path / "source_audio.mp4", frames=4, fps=8)

    class AddColorModel(nn.Module):
        def __init__(self, scale, delta=0.0, unstable=False):
            super().__init__()
            self.scale = scale
            self.delta = delta
            self.unstable = unstable

        def forward(self, x):
            if self.unstable:
                raise UnstableRestorationError("black frame")
            y = torch.nn.functional.interpolate(x, scale_factor=self.scale, mode="nearest")
            return (y + self.delta).clamp(0.0, 1.0)

    info_1x = DummyAdapter(1).model_info
    info_2x = DummyAdapter(2).model_info

    class MultiAdapter:
        def __init__(self):
            self.loaded = []

        def load(self, path, device, precision):
            self.loaded.append(path)
            if "unstable" in path:
                return AddColorModel(1, unstable=True).to(device).to(precision), info_1x
            if "fix" in path:
                return AddColorModel(1, delta=0.1).to(device).to(precision), info_1x
            return AddColorModel(2, delta=0.0).to(device).to(precision), info_2x

        def release(self):
            pass

    adapter = MultiAdapter()
    engine = InferenceEngine(adapter)

    # 1) Fix stage (1x) + 2x primary skipped at output_scale=1 + RIFE x2 interpolation + audio
    out_1x = tmp_path / "fix_1x_interp.mp4"
    config_1x = VideoJobConfig(
        video_path=str(source),
        model_path="upscale2x.pth",
        output_video_path=str(out_1x),
        model_info=info_2x,
        device_str="cpu",
        precision_str="fp32",
        tile_size=64,
        halo=4,
        safe_memory=False,
        output_scale=1,
        audio_path=str(source),
        interpolation="x2",
        interpolation_order="after_upscale",
        preprocess_stages=(
            VideoFixStage(
                model_path="fix_noise.pth",
                model_info=info_1x,
                model_id="scunet_color_real_psnr",
                halo=8,
                fix="noise",
                time_factor=1.0,
            ),
        ),
    )
    run_video_job(
        config=config_1x,
        engine=engine,
        cancel_event=threading.Event(),
        synthesizer=RecordingSynthesizer(),
    )
    assert "upscale2x.pth" not in adapter.loaded
    probe = probe_video(str(out_1x))
    assert (probe.width, probe.height) == (FRAME_WIDTH, FRAME_HEIGHT)
    assert len(list(decode_frames(str(out_1x)))) == 8  # 4 frames x2 interpolated -> 8 frames
    with av.open(str(out_1x)) as container:
        assert any(s.type == "audio" for s in container.streams)

    # 2) Unstable fix stage skips gracefully with a warning and still completes.
    warnings: list[str] = []
    out_warn = tmp_path / "unstable_fix.mp4"
    config_warn = VideoJobConfig(
        video_path=str(source),
        model_path="upscale2x.pth",
        output_video_path=str(out_warn),
        model_info=info_2x,
        device_str="cpu",
        precision_str="fp32",
        tile_size=64,
        halo=4,
        safe_memory=False,
        output_scale=2,
        preprocess_stages=(
            VideoFixStage(
                model_path="unstable_fix.pth",
                model_info=info_1x,
                model_id="fbcnn_color",
                halo=8,
                fix="jpeg",
                time_factor=1.0,
            ),
        ),
    )
    run_video_job(
        config=config_warn,
        engine=engine,
        cancel_event=threading.Event(),
        warning_callback=warnings.append,
    )
    assert len(warnings) == 1
    assert "skipped" in warnings[0]
    probe_warn = probe_video(str(out_warn))
    assert (probe_warn.width, probe_warn.height) == (FRAME_WIDTH * 2, FRAME_HEIGHT * 2)


def test_worker_server_runs_video_job_with_stages_payload(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from localsr.core.model_catalog import CatalogModel, ModelPurpose
    from localsr.worker.server import WorkerServer

    source = _make_synthetic_video(tmp_path / "in.mp4", frames=2, fps=8)
    fix_file = tmp_path / "fix.pth"
    primary_file = tmp_path / "primary.pth"
    fix_file.write_bytes(b"fix")
    primary_file.write_bytes(b"primary")

    fake_catalog = {
        "fix_model": CatalogModel(
            model_id="fix_model",
            name="Fix",
            filename="fix.pth",
            description="Fix",
            size_bytes=3,
            sha256="0" * 64,
            download_url="https://example.com/fix.pth",
            architecture="SCUNet",
            native_scale=1,
            purposes=(ModelPurpose.DENOISE,),
            recommended_halo=8,
            time_factor=1.5,
        ),
        "primary_model": CatalogModel(
            model_id="primary_model",
            name="Primary",
            filename="primary.pth",
            description="Primary",
            size_bytes=7,
            sha256="0" * 64,
            download_url="https://example.com/primary.pth",
            architecture="SPAN",
            native_scale=2,
            purposes=(ModelPurpose.GENERAL,),
            recommended_halo=8,
            time_factor=2.0,
        ),
    }
    info_1x = DummyAdapter(1).model_info
    info_2x = DummyAdapter(2).model_info

    class InspectAdapter:
        def inspect(self, path):
            return info_1x if "fix" in path else info_2x

        def load(self, path, device, precision):
            return DummyModel(1 if "fix" in path else 2).to(device).to(precision), None

        def release(self):
            pass

    captured = {}
    sent = []

    def fake_run_video_job(config, **_kwargs):
        captured["config"] = config
        return SimpleNamespace(
            output_path=config.output_video_path,
            frames_processed=2,
            elapsed_seconds=0.1,
            inference_seconds=0.1,
            partial=False,
            kept_seconds=0.0,
            kept_frames=0,
            notes=[],
        )

    monkeypatch.setattr("localsr.worker.server.run_video_job", fake_run_video_job)
    monkeypatch.setattr("localsr.worker.server.CATALOG_BY_ID", fake_catalog)
    monkeypatch.setattr("localsr.worker.server.send_message", sent.append)
    monkeypatch.setattr("localsr.worker.server.require_video_room", lambda *a, **kw: None)

    server = WorkerServer()
    server.model_adapter = InspectAdapter()
    server.engine = InferenceEngine(server.model_adapter)
    out = tmp_path / "out.mp4"
    server._run_video_job(
        "job-1",
        {
            "video_path": str(source),
            "model_path": str(primary_file),
            "model_id": "primary_model",
            "output_video_path": str(out),
            "device": "cpu",
            "precision": "fp32",
            "tile_size": 128,
            "halo": 8,
            "safe_memory": False,
            "output_scale": 2,
            "stages": [
                {
                    "kind": "restore",
                    "model_id": "fix_model",
                    "model_path": str(fix_file),
                    "role": "fix",
                    "fix": "noise",
                },
                {
                    "kind": "upscale",
                    "model_id": "primary_model",
                    "model_path": str(primary_file),
                    "role": "primary",
                },
            ],
        },
    )
    cfg = captured["config"]
    assert len(cfg.preprocess_stages) == 1
    assert cfg.preprocess_stages[0].model_id == "fix_model"
    assert cfg.preprocess_stages[0].time_factor == pytest.approx(1.5)
    from localsr.protocol.messages import VideoJobCompleted

    assert cfg.primary_time_factor == pytest.approx(2.0)
    assert isinstance(sent[-1], VideoJobCompleted)
    assert sent[-1].output_path == str(out)


def test_1x_upscale_video_never_loads_or_calls_sr_model_and_runs_fix_and_audio(tmp_path):
    from localsr.core.video_pipeline import VideoFixStage

    source = _make_synthetic_video_with_audio(tmp_path / "source_audio.mp4", frames=4, fps=8)
    info_1x = DummyAdapter(1).model_info
    info_4x = DummyAdapter(4).model_info

    sr_calls = []

    class FixModel(nn.Module):
        def forward(self, x):
            sr_calls.append("fix_forward")
            return (x + 0.05).clamp(0.0, 1.0)

    class TrackingAdapter:
        def __init__(self):
            self.loaded = []

        def load(self, path, device, precision):
            self.loaded.append(path)
            if "upscale" in path:
                raise AssertionError(f"SR model {path} must not be loaded at 1x")
            return FixModel().to(device).to(precision), info_1x

        def release(self):
            pass

    class TrackingAudioDenoiser:
        def __init__(self):
            self.calls = 0

        def denoise_channels(self, samples, *, cancel_event=None, progress_cb=None):
            self.calls += 1
            return samples

    adapter = TrackingAdapter()
    denoiser = TrackingAudioDenoiser()
    engine = InferenceEngine(adapter)
    output = tmp_path / "upscale_1x.mp4"
    config = VideoJobConfig(
        video_path=str(source),
        model_path="upscale_x4.pth",
        output_video_path=str(output),
        model_info=info_4x,
        device_str="cpu",
        precision_str="fp32",
        tile_size=64,
        halo=4,
        safe_memory=False,
        output_scale=1,
        audio_path=str(source),
        audio_denoise=True,
        audio_denoiser=denoiser,
        preprocess_stages=(
            VideoFixStage(
                model_path="fix_blur.pth",
                model_info=info_1x,
                model_id="nafnet_gopro_deblur",
                halo=8,
                fix="blur",
                time_factor=1.0,
            ),
        ),
    )
    result = run_video_job(config, engine, threading.Event())
    assert result.frames_processed == 4
    assert adapter.loaded == ["fix_blur.pth"]
    assert sr_calls == ["fix_forward"] * 4
    assert denoiser.calls > 0
    probe = probe_video(str(output))
    assert (probe.width, probe.height) == (FRAME_WIDTH, FRAME_HEIGHT)
    with av.open(str(output)) as container:
        assert any(s.type == "audio" for s in container.streams)


def test_worker_server_1x_upscale_skips_inspecting_sr_model(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from localsr.core.model_catalog import CatalogModel, ModelPurpose
    from localsr.worker.server import WorkerServer

    source = _make_synthetic_video(tmp_path / "in.mp4", frames=2, fps=8)
    fix_file = tmp_path / "fix.pth"
    fix_file.write_bytes(b"fix")

    fake_catalog = {
        "fix_model": CatalogModel(
            model_id="fix_model",
            name="Fix",
            filename="fix.pth",
            description="Fix",
            size_bytes=3,
            sha256="0" * 64,
            download_url="https://example.com/fix.pth",
            architecture="SCUNet",
            native_scale=1,
            purposes=(ModelPurpose.DENOISE,),
            recommended_halo=8,
            time_factor=1.5,
        ),
    }
    info_1x = DummyAdapter(1).model_info
    inspected = []

    class InspectAdapter:
        def inspect(self, path):
            inspected.append(path)
            if "missing_upscaler" in path:
                raise AssertionError("Unused 4x upscaler must not be inspected at 1x")
            return info_1x

        def release(self):
            pass

    captured = {}
    sent = []

    def fake_run_video_job(config, **_kwargs):
        captured["config"] = config
        return SimpleNamespace(
            output_path=config.output_video_path,
            frames_processed=2,
            elapsed_seconds=0.1,
            inference_seconds=0.1,
            partial=False,
            kept_seconds=0.0,
            kept_frames=0,
            notes=[],
        )

    monkeypatch.setattr("localsr.worker.server.run_video_job", fake_run_video_job)
    monkeypatch.setattr("localsr.worker.server.CATALOG_BY_ID", fake_catalog)
    monkeypatch.setattr("localsr.worker.server.send_message", sent.append)

    server = WorkerServer()
    server.model_adapter = InspectAdapter()
    server.engine = InferenceEngine(server.model_adapter)
    out = tmp_path / "out_1x.mp4"
    server._run_video_job(
        "job-1x",
        {
            "video_path": str(source),
            "model_path": str(tmp_path / "missing_upscaler.pth"),
            "output_video_path": str(out),
            "device": "cpu",
            "precision": "fp32",
            "tile_size": 128,
            "halo": 8,
            "safe_memory": False,
            "output_scale": 1,
            "stages": [
                {
                    "kind": "restore",
                    "model_id": "fix_model",
                    "model_path": str(fix_file),
                    "role": "fix",
                    "fix": "noise",
                },
                {
                    "kind": "upscale",
                    "model_id": "span_photo_x4",
                    "model_path": str(tmp_path / "missing_upscaler.pth"),
                    "role": "primary",
                },
            ],
        },
    )
    cfg = captured["config"]
    assert inspected == [str(fix_file)]
    assert cfg.model_path == ""
    assert cfg.model_info is None
    assert len(cfg.preprocess_stages) == 1


@pytest.mark.parametrize(("codec", "container"), [("av1", "mp4"), ("vp9", "mkv"), ("ffv1", "mkv")])
@pytest.mark.parametrize("start", [0, 2])
def test_stopped_encoder_keeps_playable_trimmed_video_and_audio(tmp_path, codec, container, start):
    source = _make_synthetic_video_with_audio(tmp_path / "source.mp4", frames=16, fps=8)
    frames = decode_timed_frames(str(source), start_frame=start)

    def interrupted():
        for index, frame in enumerate(frames):
            if index == 4:
                raise InterruptedError("stopped")
            yield frame

    output = encode_video(
        interrupted(),
        str(tmp_path / f"output.{container}"),
        fps=8,
        container_format=container,
        video_codec=codec,
        width=FRAME_WIDTH,
        height=FRAME_HEIGHT,
        audio_source=str(source),
        keep_partial=True,
    )
    assert "_partial_0m00s" in output
    with av.open(output) as result:
        video = result.streams.video[0]
        assert len(list(result.decode(video))) == 4
    with av.open(output) as result:
        audio = result.streams.audio[0]
        samples = sum(frame.samples for frame in result.decode(audio))
        assert abs(samples / audio.codec_context.sample_rate - 0.5) <= 1 / 8


def test_video_job_reports_a_kept_prefix(synthetic_video, tmp_path):
    adapter = DummyAdapter(2)
    cancel = threading.Event()
    config = VideoJobConfig(
        video_path=str(synthetic_video),
        model_path="dummy",
        output_video_path=str(tmp_path / "output.mp4"),
        model_info=adapter.model_info,
        device_str="cpu",
        precision_str="fp32",
        tile_size=64,
        halo=4,
        safe_memory=False,
    )

    def stop(index, total):
        if index >= 4:
            cancel.set()

    result = run_video_job(config, InferenceEngine(adapter), cancel, frame_started_cb=stop)
    assert result.partial
    assert result.kept_seconds > 0
    assert Path(result.output_path).is_file()
    assert 0 < len(decode_frames(result.output_path)) < FRAME_COUNT
    assert not Path(config.output_video_path).exists()


def test_stopped_subtitle_packet_is_cut_at_both_ends():
    from localsr.core.video_io import _next_trimmed_packet

    packet = av.Packet(b"caption")
    packet.time_base = Fraction(1, 1000)
    packet.pts = packet.dts = 500
    packet.duration = 2000
    result = _next_trimmed_packet(iter([packet]), 1.0, 2.0, cut_durations=True)
    assert result is packet
    assert packet.pts == 0
    assert packet.duration == 1000


def test_encoder_shutdown_flushes_before_close_even_on_failure():
    from localsr.core.video_io import _close_encoder

    events = []

    class Stream:
        def encode(self):
            events.append("flush")
            return ["packet"]

    class Container:
        def close(self):
            events.append("close")

    def mux(packet):
        events.append(packet)
        raise ValueError("mux failed")

    with pytest.raises(ValueError, match="mux failed"):
        _close_encoder(Container(), Stream(), mux)
    assert events == ["flush", "packet", "close"]


def test_encode_video_flushes_encoder_before_close_when_frame_loop_fails(monkeypatch, tmp_path):
    import localsr.core.video_io as video_io

    events = []
    real_close = video_io._close_encoder

    def tracked_close(container, stream, mux):
        events.append("close_encoder")
        return real_close(container, stream, mux)

    monkeypatch.setattr(video_io, "_close_encoder", tracked_close)

    def failing_frames():
        yield np.zeros((32, 48, 3), dtype=np.uint8)
        raise RuntimeError("boom mid-encode")

    with pytest.raises(video_io.VideoStageError, match="boom mid-encode"):
        video_io._encode_video(
            failing_frames(),
            str(tmp_path / "failed.mp4"),
            fps=24,
            video_codec="av1",
            width=48,
            height=32,
        )
    assert events == ["close_encoder"]
    assert not list(tmp_path.iterdir())


def test_video_colour_preserves_timing_and_audio_with_sparse_keyframes(tmp_path):
    from types import SimpleNamespace

    source = _make_synthetic_video_with_audio(tmp_path / "colour-input.mp4", frames=24, fps=24)
    output = tmp_path / "colour-output.mp4"
    info = SimpleNamespace(architecture="DDColor", scale=1, in_channels=3, out_channels=3)

    class StubEngine:
        def __init__(self):
            self.calls = 0

        def load_model(self, *args):
            pass

        def switch_model(self, *args):
            pass

        def process_frame(self, *, img_tensor, **kwargs):
            self.calls += 1
            rgb = (img_tensor.numpy() * 255).astype(np.uint8)
            rgb[0] = np.maximum(rgb[0], 150)
            return rgb

    engine = StubEngine()
    config = VideoJobConfig(
        video_path=str(source),
        model_path="ddcolor.pth",
        output_video_path=str(output),
        model_info=info,
        device_str="cpu",
        precision_str="fp32",
        tile_size=64,
        halo=0,
        safe_memory=False,
        output_scale=1,
        audio_path=str(source),
        video_codec="vp9",
    )
    result = run_video_job(config, engine, threading.Event())
    assert result.frames_processed == 24
    source_frames = list(decode_timed_frames(str(source)))
    output_frames = list(decode_timed_frames(str(output)))
    assert len(output_frames) == 24
    assert [float(frame.timestamp) for frame in output_frames] == pytest.approx(
        [float(frame.timestamp) for frame in source_frames], abs=0.001
    )
    assert [float(frame.duration) for frame in output_frames] == pytest.approx(
        [float(frame.duration) for frame in source_frames], abs=0.001
    )
    assert probe_video(str(output)).fps == pytest.approx(24, abs=0.1)
    with av.open(str(output)) as container:
        assert any(stream.type == "audio" for stream in container.streams)
    assert engine.calls < 24


def test_video_colour_runs_between_other_fixes_before_upscale(tmp_path):
    from types import SimpleNamespace

    from localsr.core.video_pipeline import VideoFixStage

    source = _make_synthetic_video_with_audio(tmp_path / "ordered-input.mp4", frames=8, fps=8)
    output = tmp_path / "ordered-output.mp4"
    fix_info = SimpleNamespace(architecture="Dummy", scale=1, in_channels=3, out_channels=3)
    colour_info = SimpleNamespace(architecture="DDColor", scale=1, in_channels=3, out_channels=3)
    up_info = SimpleNamespace(architecture="Dummy", scale=2, in_channels=3, out_channels=3)

    class StageEngine:
        current = ""
        colour_calls = 0
        after_colour_calls = 0

        def load_model(self, *args):
            pass

        def switch_model(self, path):
            self.current = path

        def process_frame(self, *, img_tensor, **kwargs):
            pixels = (img_tensor.numpy() * 255).clip(0, 255).astype(np.uint8)
            if self.current == "before":
                pixels[:] = 100
            elif self.current == "colour":
                self.colour_calls += 1
                assert pixels.mean() == pytest.approx(100, abs=1)
                pixels[0] = 180
                pixels[1:] = 70
            elif self.current == "after":
                self.after_colour_calls += 1
                assert pixels[0].mean() > pixels[1].mean() + 20
            elif self.current == "up":
                pixels = np.repeat(np.repeat(pixels, 2, axis=1), 2, axis=2)
            return pixels

    engine = StageEngine()
    config = VideoJobConfig(
        video_path=str(source),
        model_path="up",
        output_video_path=str(output),
        model_info=up_info,
        device_str="cpu",
        precision_str="fp32",
        tile_size=64,
        halo=0,
        safe_memory=False,
        output_scale=2,
        audio_path=str(source),
        video_codec="vp9",
        preprocess_stages=(
            VideoFixStage("before", fix_info, fix="noise"),
            VideoFixStage("colour", colour_info, fix="colour"),
            VideoFixStage("after", fix_info, fix="blur"),
        ),
    )
    events = []
    run_video_job(
        config,
        engine,
        threading.Event(),
        frame_started_cb=lambda index, total: events.append(("start", index)),
        source_frame_cb=lambda index, total, pixels: events.append(("source", index)),
    )
    assert events == [
        event for index in range(8) for event in (("start", index), ("source", index))
    ]
    assert engine.colour_calls == 1
    assert engine.after_colour_calls == 8
    probe = probe_video(str(output))
    assert (probe.width, probe.height) == (FRAME_WIDTH * 2, FRAME_HEIGHT * 2)
    assert len(list(decode_timed_frames(str(output)))) == 8


def test_video_colour_is_refused_with_hdr_preservation(tmp_path, monkeypatch):
    from dataclasses import replace
    from types import SimpleNamespace

    import localsr.core.video_pipeline as video_pipeline

    source = _make_synthetic_video_with_audio(tmp_path / "hdr-colour.mp4", frames=4, fps=24)
    real_probe = video_pipeline.probe_video

    def hdr_probe(path):
        return replace(real_probe(path), hdr_format="PQ")

    monkeypatch.setattr(video_pipeline, "probe_video", hdr_probe)
    monkeypatch.setattr(video_pipeline, "validate_hdr_encoder", lambda *args: None)
    info = SimpleNamespace(architecture="DDColor", scale=1, in_channels=3, out_channels=3)
    config = VideoJobConfig(
        video_path=str(source),
        model_path="ddcolor.pth",
        output_video_path=str(tmp_path / "out.mkv"),
        model_info=info,
        device_str="cpu",
        precision_str="fp32",
        tile_size=64,
        halo=0,
        safe_memory=False,
        output_scale=1,
        hdr_mode="preserve",
    )
    with pytest.raises(ValueError, match="Colour is not yet supported with HDR"):
        run_video_job(config, SimpleNamespace(), threading.Event())


def test_the_frame_loop_is_reported_after_audio_cleanup(tmp_path):
    """After audio cleanup reaches 100 %, the frame loop must announce itself, or the
    app keeps the audio step's 100 % and shows "Saving the result" while models load."""
    from types import SimpleNamespace

    source = _make_synthetic_video_with_audio(tmp_path / "steps.mp4", frames=6, fps=24)
    info = SimpleNamespace(architecture="DDColor", scale=1, in_channels=3, out_channels=3)

    class StubEngine:
        def __init__(self):
            self.calls = 0
            self.notes = []

        def load_model(self, *args):
            pass

        def switch_model(self, *args):
            pass

        def add_note(self, note):
            self.notes.append(note)

        def process_frame(self, *, img_tensor, **kwargs):
            self.calls += 1
            return (img_tensor.numpy() * 255).astype(np.uint8)

    stages = []
    config = VideoJobConfig(
        video_path=str(source),
        model_path="ddcolor.pth",
        output_video_path=str(tmp_path / "out.mp4"),
        model_info=info,
        device_str="cpu",
        precision_str="fp32",
        tile_size=64,
        halo=0,
        safe_memory=False,
        output_scale=1,
        audio_path=str(source),
        video_codec="vp9",
    )
    run_video_job(
        config,
        StubEngine(),
        threading.Event(),
        stage_cb=lambda stage, *counts: stages.append(stage),
    )
    assert "reading_frames" in stages


def test_colour_preparation_is_counted_before_the_first_frame(tmp_path):
    from types import SimpleNamespace

    source = _make_synthetic_video_with_audio(tmp_path / "grey.mp4", frames=6, fps=24)
    info = SimpleNamespace(architecture="DDColor", scale=1, in_channels=3, out_channels=3)

    class StubEngine:
        def __init__(self):
            self.calls = 0
            self.notes = []

        def load_model(self, *args):
            pass

        def switch_model(self, *args):
            pass

        def add_note(self, note):
            self.notes.append(note)

        def process_frame(self, *, img_tensor, **kwargs):
            self.calls += 1
            return (img_tensor.numpy() * 255).astype(np.uint8)

    calls = []
    config = VideoJobConfig(
        video_path=str(source),
        model_path="ddcolor.pth",
        output_video_path=str(tmp_path / "out.mp4"),
        model_info=info,
        device_str="cpu",
        precision_str="fp32",
        tile_size=64,
        halo=0,
        safe_memory=False,
        output_scale=1,
        video_codec="vp9",
    )
    run_video_job(
        config,
        StubEngine(),
        threading.Event(),
        stage_cb=lambda stage, *counts: calls.append((stage, *counts)),
    )
    counted = [call for call in calls if call[0] == "colour_preparing"]
    assert counted, calls
    assert [call[1] for call in counted] == list(range(1, len(counted) + 1))
    assert all(call[2] == 6 for call in counted)


def test_video_colour_is_skipped_with_a_note_when_the_video_has_colour(tmp_path):
    from types import SimpleNamespace

    from localsr.core.video_pipeline import _video_has_colour

    def _write_clip(path: Path, *, colourful: bool) -> None:
        with av.open(str(path), mode="w", format="mp4") as container:
            stream = container.add_stream("libvpx-vp9", rate=24)
            stream.width, stream.height, stream.pix_fmt = 96, 64, "yuv420p"
            stream.options = {"lossless": "1", "lag-in-frames": "0"}
            for index in range(8):
                rgb = np.empty((64, 96, 3), dtype=np.uint8)
                if colourful:
                    rgb[:, :32] = [220, 40, 40]
                    rgb[:, 32:64] = [40, 200, 60]
                    rgb[:, 64:] = [40, 80, 220]
                else:
                    rgb[...] = 80 + index * 10
                for packet in stream.encode(av.VideoFrame.from_ndarray(rgb, format="rgb24")):
                    container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)

    source = tmp_path / "colourful.mp4"
    _write_clip(source, colourful=True)
    assert _video_has_colour(
        SimpleNamespace(video_path=str(source), start_frame=0, hdr_mode="reject")
    )
    grey = tmp_path / "grey.mp4"
    _write_clip(grey, colourful=False)
    assert not _video_has_colour(
        SimpleNamespace(video_path=str(grey), start_frame=0, hdr_mode="reject")
    )

    info = SimpleNamespace(architecture="DDColor", scale=1, in_channels=3, out_channels=3)

    class StubEngine:
        def __init__(self):
            self.calls = 0
            self.notes = []

        def load_model(self, *args):
            pass

        def switch_model(self, *args):
            pass

        def add_note(self, note):
            self.notes.append(note)

        def process_frame(self, *, img_tensor, **kwargs):
            self.calls += 1
            return (img_tensor.numpy() * 255).astype(np.uint8)

    engine = StubEngine()
    loaded = []
    engine.load_model = lambda path, *args, **kwargs: loaded.append(path)
    warnings = []
    config = VideoJobConfig(
        video_path=str(source),
        model_path="ddcolor.pth",
        output_video_path=str(tmp_path / "out.mp4"),
        model_info=info,
        device_str="cpu",
        precision_str="fp32",
        tile_size=64,
        halo=0,
        safe_memory=False,
        output_scale=1,
        video_codec="vp9",
    )
    result = run_video_job(config, engine, threading.Event(), warning_callback=warnings.append)
    assert result.frames_processed == 8
    assert engine.calls == 0
    assert warnings == ["This video already has colour, so Colour was skipped."]
    assert engine.notes == warnings
    # The skipped DDColor is never loaded onto the graphics card.
    assert "ddcolor.pth" not in loaded


def test_stopped_video_job_discards_partial_and_cleans_scratch_when_keep_partial_is_false(
    synthetic_video, tmp_path
):
    adapter = DummyAdapter(2)
    cancel = threading.Event()
    out_dir = tmp_path / "out"
    scratch_dir = tmp_path / "scratch"
    out_dir.mkdir()
    scratch_dir.mkdir()
    config = VideoJobConfig(
        video_path=str(synthetic_video),
        model_path="dummy",
        output_video_path=str(out_dir / "output.mp4"),
        model_info=adapter.model_info,
        device_str="cpu",
        precision_str="fp32",
        tile_size=64,
        halo=4,
        safe_memory=False,
        temporary_directory=str(scratch_dir),
    )

    def stop(index, total):
        if index >= 4:
            cancel.set()

    with pytest.raises(InterruptedError):
        run_video_job(
            config,
            InferenceEngine(adapter),
            cancel,
            frame_started_cb=stop,
            keep_partial=False,
        )
    assert list(out_dir.iterdir()) == []
    assert list(scratch_dir.iterdir()) == []


def test_deflicker_reports_each_frame_it_smooths():
    import threading

    from localsr.core.video_pipeline import _deflicker_frames

    frames = [np.full((4, 4, 3), value, dtype=np.uint8) for value in (10, 12, 11, 13, 10)]
    reported = []
    out = list(_deflicker_frames(iter(frames), 3, threading.Event(), reported.append))
    assert len(out) == 5
    assert reported == [0, 1, 2, 3, 4]


def test_deflicker_hands_on_its_held_back_frame_when_a_stop_keeps_the_finished_part():
    """Bug hunt 2026-11-23: 20 finished frames, 19 in the kept part."""
    import threading

    from localsr.core.video_pipeline import _deflicker_frames

    cancel = threading.Event()
    frames = [np.full((4, 4, 3), 10 + index, dtype=np.uint8) for index in range(5)]

    def upstream():
        yield from frames
        cancel.set()
        raise InterruptedError("stopped")

    kept = []
    with pytest.raises(InterruptedError):
        for frame in _deflicker_frames(upstream(), 3, cancel, keep=lambda: True):
            kept.append(frame)
    assert len(kept) == 5

    discarded = []
    cancel.clear()
    with pytest.raises(InterruptedError):
        for frame in _deflicker_frames(upstream(), 3, cancel, keep=lambda: False):
            discarded.append(frame)
    assert len(discarded) == 2  # the 3 held-back look-ahead frames go with the discarded job
