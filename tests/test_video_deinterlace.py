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


BAR_SPEED = 6


def moving_bar(moment, width, height):
    """Grey with a bright bar that is BAR_SPEED pixels further right at every moment."""
    rgb = np.full((height, width, 3), 40, np.uint8)
    left = 8 + moment * BAR_SPEED
    rgb[:, left : left + 12] = 230
    return rgb


def interlaced_clip(
    path,
    order,
    *,
    codec="mpeg2video",
    container="matroska",
    frames=8,
    size=(160, 128),
    stream_order=None,
):
    """A 25 fps clip of two moments per frame, the earlier one in the field ``order`` names
    first, with every frame flagged so; ``order=None`` writes progressive frames."""
    import av

    width, height = size
    with av.open(str(path), "w", format=container) as output:
        stream = output.add_stream(codec, rate=25)
        stream.width, stream.height, stream.pix_fmt = width, height, "yuv420p"
        if codec == "mpeg2video" and order is not None:
            stream.codec_context.options = {"flags": "+ilme+ildct"}
        if stream_order is not None:
            stream.codec_context.field_order = stream_order
        # PyAV cannot set a frame's field flags; setfield does.
        flags = av.filter.Graph()
        source = flags.add_buffer(
            width=width, height=height, format="yuv420p", time_base=Fraction(1, 25)
        )
        setfield = flags.add("setfield", order or "prog")
        sink = flags.add("buffersink")
        source.link_to(setfield)
        setfield.link_to(sink)
        flags.configure()
        early, late = (slice(0, None, 2), slice(1, None, 2))
        if order == "bff":
            early, late = late, early
        for index in range(frames):
            woven = moving_bar(2 * index, width, height)
            if order is not None:
                woven[late] = moving_bar(2 * index + 1, width, height)[late]
            frame = av.VideoFrame.from_ndarray(woven, format="rgb24").reformat(format="yuv420p")
            frame.pts, frame.time_base = index, Fraction(1, 25)
            flags.push(frame)
            for packet in stream.encode(flags.pull()):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)
    return path


def dv_clip(path, frames=6):
    """PAL DV in AVI: the stream gives no field order, every frame is flagged bottom first."""
    return interlaced_clip(
        path, "bff", codec="dvvideo", container="avi", frames=frames, size=(720, 576)
    )


def deinterlaced(tmp_path, monkeypatch, source, params, name="out.mkv"):
    import localsr.worker.server as server_module
    from localsr.core.video_io import decode_timed_frames
    from localsr.worker.server import WorkerServer

    monkeypatch.setenv("LOCALSR_DEV_STEPS", "1")
    monkeypatch.setattr(server_module, "send_message", lambda message: None)
    output = tmp_path / name
    WorkerServer()._run_video_job(
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
            "stages": [{"step": "deinterlace", "params": params}],
        },
    )
    return list(decode_timed_frames(str(output)))


def bar_positions(frames):
    """The bar's centre in each frame, from the columns brighter than the background."""
    positions = []
    for frame in frames:
        columns = frame.rgb.astype(float).mean(axis=(0, 2))
        weights = np.clip(columns - 135, 0, None)
        positions.append(float((np.arange(columns.size) * weights).sum() / weights.sum()))
    return np.array(positions)


def test_field_mode_on_a_flagged_clip_gives_every_field_its_own_moment(tmp_path, monkeypatch):
    source = interlaced_clip(tmp_path / "25i.mkv", "tff")
    frames = deinterlaced(tmp_path, monkeypatch, source, {"mode": "field"})
    assert len(frames) == 16
    start = frames[0].timestamp
    assert [frame.timestamp - start for frame in frames] == [Fraction(i, 50) for i in range(16)]
    assert frames[-1].duration == Fraction(1, 50)
    pixels = np.stack([frame.rgb.astype(float) for frame in frames])
    steps = np.abs(np.diff(pixels, axis=0)).mean(axis=(1, 2, 3))
    assert steps.min() >= steps.max() / 2, steps.round(2).tolist()


@pytest.mark.parametrize("clip", ["dv_says_unknown", "mkv_says_tb"])
def test_auto_parity_follows_the_frames_own_flag(tmp_path, monkeypatch, clip):
    if clip == "dv_says_unknown":
        source, order = dv_clip(tmp_path / "dv.avi"), "bff"
    else:
        # Stream value 4 ("tb") reads as bottom first; every frame says top first.
        source, order = interlaced_clip(tmp_path / "tb.mkv", "tff", stream_order=4), "tff"
    steps = np.diff(bar_positions(deinterlaced(tmp_path, monkeypatch, source, {"mode": "field"})))
    assert (steps > BAR_SPEED / 2).all(), steps.round(1).tolist()
    # The measure sees a wrong order: a hand-set one always runs, and the bar jumps back.
    other = "tff" if order == "bff" else "bff"
    wrong = deinterlaced(
        tmp_path, monkeypatch, source, {"mode": "field", "parity": other}, name="wrong.mkv"
    )
    assert (np.diff(bar_positions(wrong)) < 0).any()


def test_probe_reports_flagged_frames_as_interlaced(tmp_path):
    from localsr.core.video_io import probe_video, probe_video_preview

    clips = {
        "bff": dv_clip(tmp_path / "dv.avi", frames=2),
        "tff": interlaced_clip(tmp_path / "tb.mkv", "tff", frames=2, stream_order=4),
        "progressive": interlaced_clip(tmp_path / "progressive.mkv", None, frames=2),
    }
    for expected, path in clips.items():
        assert probe_video(str(path)).field_order == expected, path.name
        assert probe_video_preview(str(path), 64)[0].field_order == expected, path.name


def test_field_mode_doubles_every_frame_of_a_partly_flagged_clip(tmp_path):
    import av

    from localsr.core.video_io import decode_timed_frames
    from localsr.core.video_normalization import Deinterlace

    source = tmp_path / "mixed.mkv"
    with av.open(str(source), "w", format="matroska") as output:
        stream = output.add_stream("ffv1", rate=25)
        stream.width, stream.height, stream.pix_fmt = 64, 48, "yuv420p"
        for index in range(8):
            # FFV1 keeps each frame's own flag; frames 3 and 4 are progressive.
            flags = av.filter.Graph()
            buffer = flags.add_buffer(
                width=64, height=48, format="yuv420p", time_base=Fraction(1, 25)
            )
            setfield = flags.add("setfield", "prog" if index in (3, 4) else "tff")
            sink = flags.add("buffersink")
            buffer.link_to(setfield)
            setfield.link_to(sink)
            flags.configure()
            frame = av.VideoFrame.from_ndarray(moving_bar(index, 64, 48), format="rgb24")
            frame = frame.reformat(format="yuv420p")
            frame.pts, frame.time_base = index, Fraction(1, 25)
            flags.push(frame)
            for packet in stream.encode(flags.pull()):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)
    frames = list(decode_timed_frames(str(source), deinterlace=Deinterlace("field")))
    assert [frame.index for frame in frames] == [i // 2 for i in range(16)]
    assert [frame.timestamp for frame in frames] == [Fraction(i, 50) for i in range(16)]


def test_field_mode_trims_count_source_frames(tmp_path):
    from localsr.core.video_io import decode_timed_frames
    from localsr.core.video_normalization import Deinterlace

    source = interlaced_clip(tmp_path / "25i.mkv", "tff")
    frames = list(decode_timed_frames(str(source), 2, 4, deinterlace=Deinterlace("field")))
    assert [frame.index for frame in frames] == [2, 2, 3, 3, 4, 4]
    assert [frame.timestamp for frame in frames] == [Fraction(i, 50) for i in range(4, 10)]
    assert all(frame.duration == Fraction(1, 50) for frame in frames)
    assert all(frame.deinterlaced_by_step for frame in frames)
    steps = np.diff(bar_positions(frames))
    assert (steps > BAR_SPEED / 2).all(), steps.round(1).tolist()


@pytest.mark.parametrize(
    "name,filters,codec,expected",
    [
        # The ffmpeg tool writes "tb" for these top-first frames, and "bt" for the DV.
        (
            "x264.mkv",
            "tinterlace=interleave_top",
            ["libx264", "-flags", "+ilme+ildct", "-x264opts", "tff=1"],
            "tff",
        ),
        ("dv.mov", "tinterlace=interleave_top,fieldorder=bff", ["dvvideo"], "bff"),
    ],
)
def test_probe_trusts_the_frames_of_files_the_ffmpeg_tool_wrote(
    tmp_path, name, filters, codec, expected
):
    import shutil
    import subprocess

    from localsr.core.video_io import probe_video

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("fixture generation needs ffmpeg; production uses PyAV")
    listed = "".join(
        subprocess.run(
            [ffmpeg, "-hide_banner", kind], capture_output=True, text=True, timeout=10
        ).stdout
        for kind in ("-encoders", "-filters")
    )
    if "libx264" not in listed or "tinterlace" not in listed:
        pytest.skip("this ffmpeg lacks libx264 or tinterlace")
    path = tmp_path / name
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=720x576:rate=50",
            "-frames:v",
            "4",
            "-vf",
            filters,
            "-c:v",
            *codec,
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        timeout=30,
    )
    assert probe_video(str(path)).field_order == expected
