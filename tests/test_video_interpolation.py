"""Frame interpolation inside real video jobs: order, timing, audio and refusals."""

from __future__ import annotations

import threading
from fractions import Fraction
from types import SimpleNamespace

import av
import numpy as np
import pytest
from test_frame_interpolation import RecordingSynthesizer
from test_video_pipeline import _make_synthetic_video_with_audio
from test_video_timing import IdentityEngine, make_vfr, timestamps

from localsr.core.video_io import decode_timed_frames, probe_video
from localsr.core.video_pipeline import VideoJobConfig, run_video_job


class CountingEngine(IdentityEngine):
    def __init__(self):
        self.frames = 0

    def process_frame(self, *, img_tensor, **kwargs):
        self.frames += 1
        return super().process_frame(img_tensor=img_tensor, **kwargs)


def interpolated_job(source, destination, synthesizer=None, engine=None, **options):
    engine = engine or CountingEngine()
    started: list[tuple[int, int]] = []
    warnings: list[str] = []
    result = run_video_job(
        VideoJobConfig(
            str(source),
            "test",
            str(destination),
            SimpleNamespace(scale=1),
            "cpu",
            "fp32",
            64,
            4,
            False,
            **options,
        ),
        engine,
        threading.Event(),
        frame_started_cb=lambda index, total: started.append((index, total)),
        warning_callback=warnings.append,
        synthesizer=synthesizer if synthesizer is not None else RecordingSynthesizer(),
    )
    return result, engine, started, warnings


def make_cfr(path, frames=12, fps=24, width=48, height=32):
    """A lossless constant-rate clip whose grey level rises by 8 per frame."""
    with av.open(str(path), "w") as container:
        stream = container.add_stream("libvpx-vp9", rate=fps)
        stream.width, stream.height, stream.pix_fmt = width, height, "yuv420p"
        stream.options = {"lossless": "1", "lag-in-frames": "0"}
        for index in range(frames):
            rgb = np.full((height, width, 3), 100 + index * 8, dtype=np.uint8)
            frame = av.VideoFrame.from_ndarray(rgb, format="rgb24")
            frame.pts, frame.time_base = index, Fraction(1, fps)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return path


def test_doubling_after_the_upscale_keeps_vfr_timing_and_upscales_only_source_frames(tmp_path):
    source = make_vfr(tmp_path / "vfr.mp4")
    output = tmp_path / "double.mp4"
    result, engine, started, _ = interpolated_job(source, output, interpolation="x2")
    selected = list(decode_timed_frames(str(source)))
    expected = []
    for current, following in zip(selected, selected[1:], strict=False):
        start = float(current.timestamp - selected[0].timestamp)
        expected += [start, start + float(following.timestamp - current.timestamp) / 2]
    expected.append(float(selected[-1].timestamp - selected[0].timestamp))
    assert timestamps(output) == pytest.approx(expected, abs=0.001)
    assert engine.frames == len(selected) == result.frames_processed
    assert {total for _, total in started} == {len(selected)}
    assert probe_video(str(output)).duration_seconds == pytest.approx(
        float(selected[-1].timestamp + selected[-1].duration - selected[0].timestamp), abs=0.002
    )


def test_interpolating_before_the_upscale_upscales_every_output_frame(tmp_path):
    source = make_vfr(tmp_path / "vfr.mp4")
    output = tmp_path / "before.mp4"
    _, engine, started, _ = interpolated_job(
        source, output, interpolation="x2", interpolation_order="before_upscale"
    )
    count = len(list(decode_timed_frames(str(source))))
    assert engine.frames == 2 * count - 1 == len(timestamps(output))
    assert [index for index, _ in started] == list(range(2 * count - 1))
    assert {total for _, total in started} == {2 * count - 1}


def test_sixty_frames_per_second_from_a_24_fps_clip(tmp_path):
    source = make_cfr(tmp_path / "film.mp4", frames=12, fps=24)
    output = tmp_path / "sixty.mp4"
    synthesizer = RecordingSynthesizer()
    interpolated_job(source, output, synthesizer, interpolation="fps60")
    assert timestamps(output) == pytest.approx([index / 60 for index in range(30)], abs=0.001)
    assert probe_video(str(output)).fps == pytest.approx(60, rel=0.01)
    assert probe_video(str(output)).duration_seconds == pytest.approx(0.5, abs=0.002)
    # The grey ramp is interpolated, not repeated: 1/60 s lies 0.4 of the way to frame 1.
    second = list(decode_timed_frames(str(output)))[1].rgb
    assert int(np.median(second)) == pytest.approx(103, abs=2)
    assert synthesizer.calls


def test_interpolated_video_keeps_the_source_audio_and_its_length(tmp_path):
    source = _make_synthetic_video_with_audio(tmp_path / "audio.mp4", frames=8, fps=8)
    output = tmp_path / "with-audio.mp4"
    interpolated_job(source, output, interpolation="x4")
    with av.open(str(output)) as container:
        kinds = sorted(stream.type for stream in container.streams)
        audio = next(stream for stream in container.streams if stream.type == "audio")
        assert audio.codec_context.name == "opus"
    assert kinds == ["audio", "video"]
    assert len(timestamps(output)) == 29
    assert probe_video(str(output)).duration_seconds == pytest.approx(1.0, abs=0.05)


def test_a_source_already_at_the_target_rate_is_left_alone_with_a_warning(tmp_path):
    source = make_cfr(tmp_path / "fast.mp4", frames=8, fps=60)
    output = tmp_path / "unchanged.mp4"
    synthesizer = RecordingSynthesizer()
    _, _, _, warnings = interpolated_job(source, output, synthesizer, interpolation="fps60")
    assert timestamps(output) == pytest.approx([index / 60 for index in range(8)], abs=0.001)
    assert synthesizer.calls == []
    assert any("already plays at 60 fps" in message for message in warnings)


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"fps_override": 30.0}, "FPS override"),
        ({"interpolation_order": "sideways"}, "before or after"),
    ],
)
def test_interpolation_refuses_combinations_it_cannot_time(tmp_path, options, message):
    source = make_vfr(tmp_path / "vfr.mp4")
    with pytest.raises(ValueError, match=message):
        interpolated_job(source, tmp_path / "refused.mp4", interpolation="x2", **options)
    assert not (tmp_path / "refused.mp4").exists()


def test_hdr_preservation_and_interpolation_are_refused_before_the_model_loads(tmp_path):
    source = make_vfr(tmp_path / "hlg.mp4", transfer=18)

    class RefusingEngine(IdentityEngine):
        def load_model(self, *_args):
            raise AssertionError("the model must not load")

    config = VideoJobConfig(
        str(source),
        "test",
        str(tmp_path / "hdr.mkv"),
        SimpleNamespace(scale=1, architecture="HAT"),
        "cpu",
        "fp32",
        64,
        4,
        False,
        container="mkv",
        video_codec="ffv1",
        hdr_mode="preserve",
        interpolation="x2",
    )
    with pytest.raises(ValueError, match="HDR preservation"):
        run_video_job(
            config, RefusingEngine(), threading.Event(), synthesizer=RecordingSynthesizer()
        )


def test_interpolation_without_its_model_is_refused(tmp_path):
    source = make_vfr(tmp_path / "vfr.mp4")
    with pytest.raises(ValueError, match="interpolation model"):
        run_video_job(
            VideoJobConfig(
                str(source),
                "test",
                str(tmp_path / "missing.mp4"),
                SimpleNamespace(scale=1),
                "cpu",
                "fp32",
                64,
                4,
                False,
                interpolation="x2",
            ),
            IdentityEngine(),
            threading.Event(),
        )


def test_interpolation_off_leaves_the_frame_by_frame_job_unchanged(tmp_path):
    source = make_vfr(tmp_path / "vfr.mp4")
    output = tmp_path / "plain.mp4"
    synthesizer = RecordingSynthesizer()
    _, engine, _, _ = interpolated_job(source, output, synthesizer)
    selected = list(decode_timed_frames(str(source)))
    assert timestamps(output) == pytest.approx(
        [float(frame.timestamp - selected[0].timestamp) for frame in selected], abs=0.001
    )
    assert engine.frames == len(selected)
    assert synthesizer.calls == []
