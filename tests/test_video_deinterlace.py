from fractions import Fraction
from types import SimpleNamespace

import numpy as np
import pytest

from localsr.core.steps import STEPS_BY_ID, validate_step_params
from localsr.core.video_chain import VIDEO_STEP_HANDLERS, StreamFrame, VideoStepContext
from localsr.core.video_io import TimedVideoFrame
from localsr.core.video_pipeline import VideoFixStage


def frames(dtype=np.uint8):
    maximum = np.iinfo(dtype).max
    result = []
    for i in range(5):
        pixels = np.zeros((32, 48, 3), dtype=dtype)
        pixels[::2, i * 3 : i * 3 + 8] = maximum - 123
        pixels[1::2, i * 3 + 4 : i * 3 + 12] = maximum - 123
        result.append(
            StreamFrame(
                TimedVideoFrame(i, pixels, Fraction(i, 25), Fraction(1, 25), Fraction(1, 25))
            )
        )
    return result


def run(mode, dtype=np.uint8):
    shared = SimpleNamespace(check_cancelled=lambda: None)
    stage = VideoFixStage("", None, step_id="deinterlace", params={"mode": mode, "parity": "tff"})
    return list(
        VIDEO_STEP_HANDLERS["deinterlace"](stage, VideoStepContext(shared, 0, False, False))(
            iter(frames(dtype))
        )
    )


def comb(items):
    return np.mean(
        [
            np.abs(np.diff(x.frame.rgb.astype(float), axis=0)).mean()
            - np.abs(x.frame.rgb[2:].astype(float) - x.frame.rgb[:-2].astype(float)).mean()
            for x in items
        ]
    )


@pytest.mark.parametrize("mode,count", [("frame", 5), ("field", 10)])
def test_filter_removes_combing_and_keeps_timing(mode, count):
    out = run(mode)
    assert len(out) == count
    assert comb(out) < comb(frames()) * 0.7
    assert all(a.frame.timestamp < b.frame.timestamp for a, b in zip(out, out[1:], strict=False))
    expected = [Fraction(i, 50 if mode == "field" else 25) for i in range(count)]
    assert [item.frame.timestamp for item in out] == expected


def test_sixteen_bit_is_preserved():
    out = run("frame", np.uint16)
    assert out[0].frame.rgb.dtype == np.uint16
    assert any(np.any(item.frame.rgb % 257) for item in out)


@pytest.mark.parametrize("params", [{"mode": "bad"}, {"parity": "bad"}])
def test_bad_params(params):
    with pytest.raises(ValueError):
        validate_step_params(STEPS_BY_ID["deinterlace"], params)


def test_dev_gate(monkeypatch):
    from localsr.worker.server import _engine_features

    monkeypatch.delenv("LOCALSR_DEV_STEPS", raising=False)
    assert "video_deinterlace" not in _engine_features([])
    monkeypatch.setenv("LOCALSR_DEV_STEPS", "1")
    assert "video_deinterlace" in _engine_features([])


def test_progressive_auto_is_unchanged_and_noted(tmp_path, monkeypatch):
    import threading
    from dataclasses import replace

    from test_video_interpolation import make_cfr

    from localsr.core import video_pipeline
    from localsr.core.inference import InferenceEngine
    from localsr.core.video_io import decode_timed_frames
    from localsr.core.video_pipeline import VideoJobConfig, run_video_job

    source = make_cfr(tmp_path / "in.mkv", frames=4)
    real_probe = video_pipeline.probe_video
    monkeypatch.setattr(
        video_pipeline,
        "probe_video",
        lambda path: replace(real_probe(path), field_order="progressive"),
    )
    engine = InferenceEngine(SimpleNamespace(load=lambda *a: None, release=lambda: None))
    config = VideoJobConfig(
        str(source),
        "",
        str(tmp_path / "out.mkv"),
        None,
        "cpu",
        "fp32",
        64,
        4,
        False,
        output_scale=1,
        video_codec="ffv1",
        container="mkv",
        preprocess_stages=(
            VideoFixStage("", None, step_id="deinterlace", params={"mode": "field"}),
        ),
    )
    run_video_job(config, engine, threading.Event())
    assert "Already progressive · kept as it is" in engine.take_notes()
    before = list(decode_timed_frames(str(source)))
    after = list(decode_timed_frames(str(tmp_path / "out.mkv")))
    assert len(before) == len(after)
    for a, b in zip(before, after, strict=True):
        np.testing.assert_array_equal(a.rgb, b.rgb)


def test_worker_field_job_doubles_rate(tmp_path, monkeypatch):
    from test_video_interpolation import make_cfr

    import localsr.worker.server as server_module
    from localsr.core.video_io import probe_video
    from localsr.worker.server import WorkerServer

    monkeypatch.setenv("LOCALSR_DEV_STEPS", "1")
    monkeypatch.setattr(server_module, "send_message", lambda message: None)
    source = make_cfr(tmp_path / "source.mkv", frames=5, fps=25)
    output = tmp_path / "out.mkv"
    server = WorkerServer()
    server._run_video_job(
        "deinterlace",
        {
            "video_path": str(source),
            "model_path": "",
            "output_video_path": str(output),
            "device": "cpu",
            "precision": "fp32",
            "tile_size": 64,
            "halo": 4,
            "safe_memory": False,
            "output_scale": 1,
            "video_codec": "ffv1",
            "container": "mkv",
            "stages": [{"step": "deinterlace", "params": {"mode": "field"}}],
        },
    )
    assert probe_video(str(output)).fps == pytest.approx(50)
    from localsr.core.video_io import decode_timed_frames

    assert len(list(decode_timed_frames(str(output)))) == 10


@pytest.mark.parametrize(
    "order,expected",
    [(0, "unknown"), (1, "progressive"), (2, "tff"), (3, "bff"), (4, "bff"), (5, "tff")],
)
def test_probe_reports_display_field_order(monkeypatch, order, expected):
    from localsr.core import video_io

    monkeypatch.setattr(video_io, "square_pixel_width", lambda stream, width: width)
    container = SimpleNamespace(duration=0, streams=[])
    stream = SimpleNamespace(
        average_rate=25,
        frames=5,
        width=48,
        height=32,
        codec_context=SimpleNamespace(color_trc=1, name="test", field_order=order),
    )
    assert video_io._video_probe(container, stream, None).field_order == expected
