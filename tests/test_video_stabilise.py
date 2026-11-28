from fractions import Fraction
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from localsr.core.steps import STEPS_BY_ID, parameter_for
from localsr.core.video_chain import VIDEO_STEP_HANDLERS, StreamFrame, VideoStepContext
from localsr.core.video_io import TimedVideoFrame
from localsr.core.video_pipeline import VideoFixStage

FPS = 30
MAGENTA = (255, 0, 255)


def texture(height, width, seed=0, low=40, high=215):
    """A grey-dominated still with fine and coarse detail; no pixel is magenta or black."""
    rng = np.random.default_rng(seed)
    fine = cv2.GaussianBlur(rng.random((height, width), dtype=np.float32), (0, 0), 1.5)
    coarse = cv2.GaussianBlur(rng.random((height, width), dtype=np.float32), (0, 0), 9)
    grey = 0.6 * (fine - fine.mean()) / fine.std() + (coarse - coarse.mean()) / coarse.std()
    grey = (grey - grey.min()) / (grey.max() - grey.min())
    tint = rng.uniform(-8, 8, size=3).astype(np.float32)
    rgb = low + (high - low) * grey[..., None] + tint
    return np.clip(np.rint(rgb), low, high).astype(np.uint8)


def shaky(still, count=60, height=360, width=480, max_shift=12, seed=1, border=None):
    """Frames cut from ``still`` at a random offset of up to ``max_shift`` px each."""
    rng = np.random.default_rng(seed)
    offsets = rng.integers(-max_shift, max_shift + 1, size=(count, 2))
    result = []
    for index, (dx, dy) in enumerate(offsets):
        top, left = max_shift + dy, max_shift + dx
        rgb = still[top : top + height, left : left + width].copy()
        if border is not None:
            rgb[0, :] = rgb[-1, :] = rgb[:, 0] = rgb[:, -1] = border
        timing = TimedVideoFrame(
            index, rgb, Fraction(index, FPS), Fraction(1, FPS), Fraction(1, FPS)
        )
        result.append(StreamFrame(timing))
    return result, offsets


def run(items, smoothing="medium", fps=FPS):
    shared = SimpleNamespace(check_cancelled=lambda: None, fps=float(fps))
    stage = VideoFixStage("", None, step_id="stabilise", params={"smoothing": smoothing})
    handler = VIDEO_STEP_HANDLERS["stabilise"](stage, VideoStepContext(shared, 0, False, False))
    return list(handler(iter(items)))


def frame_shifts(items):
    """Frame-to-frame translation by phase correlation, in pixels."""
    greys = [cv2.cvtColor(item.frame.rgb, cv2.COLOR_RGB2GRAY).astype(np.float32) for item in items]
    window = cv2.createHanningWindow(greys[0].shape[::-1], cv2.CV_32F)
    return np.array(
        [cv2.phaseCorrelate(a, b, window)[0] for a, b in zip(greys, greys[1:], strict=False)]
    )


def spread(shifts):
    return float(np.sqrt(shifts[:, 0].var() + shifts[:, 1].var()))


def test_stabilise_is_a_video_geometry_step_after_deinterlace():
    step = STEPS_BY_ID["stabilise"]
    assert step.media == ("video",)
    assert step.phase == "geometry" and step.in_image_pipeline
    assert step.order > STEPS_BY_ID["deinterlace"].order
    assert not step.needs_model and step.model_task is None
    assert step.dev_only
    assert step.required_feature == "video_stabilise"
    assert (step.label, step.stage_label) == ("Stabilise", "Stabilising")
    smoothing = parameter_for("stabilise", "smoothing")
    assert (smoothing.kind, smoothing.default, smoothing.choices) == (
        "choice",
        "medium",
        ("light", "medium", "strong"),
    )
    assert [param.name for param in step.parameters] == ["smoothing"]


def test_the_result_moves_less():
    items, _ = shaky(texture(384, 504))
    before = spread(frame_shifts(items))
    medium = spread(frame_shifts(run(items, "medium")))
    strong = spread(frame_shifts(run(items, "strong")))
    assert before > 5
    assert medium <= before / 2
    assert strong <= medium


@pytest.mark.parametrize("smoothing", ["light", "medium", "strong"])
def test_no_edge_of_the_source_shows(smoothing):
    items, _ = shaky(texture(384, 504), border=MAGENTA)
    out = run(items, smoothing)
    assert len(out) == len(items)
    for item in out:
        rgb = item.frame.rgb.astype(np.int16)
        red, green, blue = rgb[..., 0], rgb[..., 1], rgb[..., 2]
        assert not np.any((red - green > 60) & (blue - green > 60)), item.frame.index
        assert not np.any(rgb.max(axis=2) < 25), item.frame.index


@pytest.mark.parametrize("dtype", [np.uint8, np.uint16, np.float32])
def test_size_count_and_times_are_kept(dtype):
    items, _ = shaky(texture(384, 504))
    if dtype == np.uint16:
        items = [x.with_pixels(x.frame.rgb.astype(np.uint16) * 257 + 100) for x in items]
    elif dtype == np.float32:
        items = [x.with_pixels(x.frame.rgb.astype(np.float32) / 255) for x in items]
    out = run(items)
    assert len(out) == len(items)
    for before, after in zip(items, out, strict=True):
        assert after.frame.rgb.shape == before.frame.rgb.shape
        assert after.frame.rgb.dtype == dtype
        assert (after.frame.index, after.frame.timestamp, after.frame.duration) == (
            before.frame.index,
            before.frame.timestamp,
            before.frame.duration,
        )
        assert after.frame.time_base == before.frame.time_base
    if dtype == np.uint16:
        assert any(np.any(item.frame.rgb % 257 != 100) for item in out)
    if dtype == np.float32:
        assert all(0 <= item.frame.rgb.min() and item.frame.rgb.max() <= 1 for item in out)
    assert spread(frame_shifts(out)) < spread(frame_shifts(items)) / 2


def bilinear_enlargement(rgb, zoom):
    height, width = rgb.shape[:2]
    cy, cx = (height - 1) / 2, (width - 1) / 2
    y = cy + (np.arange(height) - cy) / zoom
    x = cx + (np.arange(width) - cx) / zoom
    y0, x0 = np.floor(y).astype(int), np.floor(x).astype(int)
    fy, fx = (y - y0)[:, None, None], (x - x0)[None, :, None]
    src = rgb.astype(np.float64)
    top = src[y0][:, x0] * (1 - fx) + src[y0][:, x0 + 1] * fx
    bottom = src[y0 + 1][:, x0] * (1 - fx) + src[y0 + 1][:, x0 + 1] * fx
    return top * (1 - fy) + bottom * fy


def test_a_still_clip_only_gets_larger():
    still = cv2.GaussianBlur(texture(360, 480, seed=4), (0, 0), 3)
    items = [
        StreamFrame(
            TimedVideoFrame(i, still.copy(), Fraction(i, FPS), Fraction(1, FPS), Fraction(1, FPS))
        )
        for i in range(20)
    ]
    expected = bilinear_enlargement(still, 1 / (1 - 2 * 0.05))
    for item in run(items, "medium"):
        assert np.abs(item.frame.rgb.astype(np.float64) - expected).max() <= 2


def test_a_cut_starts_a_new_path():
    first, _ = shaky(texture(384, 504, seed=5, low=30, high=110), count=30, seed=7)
    second, _ = shaky(texture(384, 504, seed=6, low=140, high=225), count=30, seed=8)
    together = run(first + second)
    apart = run(first) + run(second)
    assert len(together) == 60
    for joined, alone in zip(together, apart, strict=True):
        np.testing.assert_array_equal(joined.frame.rgb, alone.frame.rgb)


def test_hdr_preserve_is_refused(tmp_path, monkeypatch):
    import threading
    from dataclasses import replace

    from test_video_interpolation import make_cfr

    from localsr.core import video_pipeline
    from localsr.core.video_pipeline import VideoJobConfig, run_video_job

    source = make_cfr(tmp_path / "in.mkv", frames=2)
    real_probe = video_pipeline.probe_video
    monkeypatch.setattr(
        video_pipeline, "probe_video", lambda path: replace(real_probe(path), hdr_format="PQ")
    )
    monkeypatch.setattr(video_pipeline, "validate_hdr_encoder", lambda *args: None)
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
        hdr_mode="preserve",
        preprocess_stages=(
            VideoFixStage("", None, step_id="stabilise", params={"smoothing": "medium"}),
        ),
    )
    with pytest.raises(ValueError, match="Stabilise is not yet supported with HDR preservation."):
        run_video_job(config, SimpleNamespace(), threading.Event())  # type: ignore[arg-type]


def write_clip(path, items):
    import av

    height, width = items[0].frame.rgb.shape[:2]
    with av.open(str(path), "w") as container:
        stream = container.add_stream("libvpx-vp9", rate=FPS)
        stream.width, stream.height, stream.pix_fmt = width, height, "yuv420p"
        stream.options = {"lossless": "1", "lag-in-frames": "0"}
        for item in items:
            frame = av.VideoFrame.from_ndarray(item.frame.rgb, format="rgb24")
            frame.pts, frame.time_base = item.frame.index, Fraction(1, FPS)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return path


def test_it_runs_before_the_fixes_and_after_deinterlace(tmp_path, monkeypatch):
    import threading

    from localsr.core import video_chain
    from localsr.core.inference import InferenceEngine
    from localsr.core.video_io import decode_timed_frames
    from localsr.core.video_pipeline import VideoJobConfig, run_video_job

    items, _ = shaky(texture(384, 504), count=24)
    source = write_clip(tmp_path / "shaky.webm", items)
    seen: dict[str, list[np.ndarray]] = {"deinterlace": [], "fix": []}

    def recorder(name):
        def build(_stage, _ctx):
            def _stage_fn(frames):
                for item in frames:
                    seen[name].append(item.frame.rgb.copy())
                    yield item

            return _stage_fn

        return build

    monkeypatch.setitem(video_chain.VIDEO_STEP_HANDLERS, "deinterlace", recorder("deinterlace"))
    monkeypatch.setitem(video_chain.VIDEO_STEP_HANDLERS, "custom_fix", recorder("fix"))
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
        # Listed against the run order: the geometry steps still run first, in their order.
        preprocess_stages=(
            VideoFixStage("", None, step_id="custom_fix"),
            VideoFixStage("", None, step_id="stabilise", params={"smoothing": "medium"}),
            VideoFixStage("", None, step_id="deinterlace", params={"parity": "tff"}),
        ),
    )
    engine = InferenceEngine(SimpleNamespace(load=lambda *a: None, release=lambda: None))
    run_video_job(config, engine, threading.Event())
    decoded = [StreamFrame(frame) for frame in decode_timed_frames(str(source))]
    steadied = run(decoded, "medium")
    assert len(seen["deinterlace"]) == len(seen["fix"]) == len(decoded) == 24
    for raw, received in zip(decoded, seen["deinterlace"], strict=True):
        np.testing.assert_array_equal(received, raw.frame.rgb)
    for expected, received in zip(steadied, seen["fix"], strict=True):
        np.testing.assert_array_equal(received, expected.frame.rgb)


def turned(still, count=40, height=360, width=480, max_turn=1.5, max_shift=6, seed=3):
    rng = np.random.default_rng(seed)
    turns = rng.uniform(-max_turn, max_turn, count)
    shifts = rng.uniform(-max_shift, max_shift, (count, 2))
    margin_y = (still.shape[0] - height) / 2
    margin_x = (still.shape[1] - width) / 2
    centre = ((still.shape[1] - 1) / 2, (still.shape[0] - 1) / 2)
    result = []
    for index, (turn, (dx, dy)) in enumerate(zip(turns, shifts, strict=True)):
        matrix = cv2.getRotationMatrix2D(centre, float(turn), 1.0)
        matrix[:, 2] -= (margin_x + dx, margin_y + dy)
        rgb = cv2.warpAffine(still, matrix, (width, height), flags=cv2.INTER_CUBIC)
        timing = TimedVideoFrame(
            index, rgb, Fraction(index, FPS), Fraction(1, FPS), Fraction(1, FPS)
        )
        result.append(StreamFrame(timing))
    return result


def frame_turns(items):
    """Frame-to-frame turn in degrees, measured by intensity alignment (ECC)."""
    greys = [
        cv2.resize(cv2.cvtColor(item.frame.rgb, cv2.COLOR_RGB2GRAY), None, fx=0.5, fy=0.5).astype(
            np.float32
        )
        for item in items
    ]
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 50, 1e-5)
    result = []
    for a, b in zip(greys, greys[1:], strict=False):
        warp = np.eye(2, 3, dtype=np.float32)
        _, warp = cv2.findTransformECC(a, b, warp, cv2.MOTION_EUCLIDEAN, criteria, None, 5)
        result.append(np.degrees(np.arctan2(warp[1, 0], warp[0, 0])))
    return np.array(result)


def test_a_turn_is_evened_out():
    items = turned(texture(440, 560, seed=9))
    out = run(items, "medium")
    before = frame_turns(items).std()
    after = frame_turns(out).std()
    assert before > 0.8
    assert after <= before / 2
    assert spread(frame_shifts(out)) < spread(frame_shifts(items)) / 2


def test_a_flat_clip_is_only_enlarged():
    from localsr.core.stabilise import measure_motion, tracking_grey

    flat = np.full((360, 480, 3), 90, dtype=np.uint8)
    grey = tracking_grey(flat)
    assert measure_motion(grey, grey, (480, 360)) is None
    items = [
        StreamFrame(TimedVideoFrame(i, flat, Fraction(i, FPS), Fraction(1, FPS), Fraction(1, FPS)))
        for i in range(5)
    ]
    for item in run(items, "strong"):
        np.testing.assert_array_equal(item.frame.rgb, flat)


@pytest.mark.parametrize("budget_frames,held", [(None, 16), (5, 5)])
def test_the_look_ahead_is_bounded(monkeypatch, budget_frames, held):
    from localsr.core import stabilise

    items, _ = shaky(texture(384, 504), count=40)
    if budget_frames is not None:
        frame_bytes = items[0].frame.rgb.nbytes
        monkeypatch.setattr(stabilise, "LOOKAHEAD_BUDGET_BYTES", budget_frames * frame_bytes)
    pulled = 0

    def source():
        nonlocal pulled
        for item in items:
            pulled += 1
            yield item

    shared = SimpleNamespace(check_cancelled=lambda: None, fps=float(FPS))
    stage = VideoFixStage("", None, step_id="stabilise", params={"smoothing": "medium"})
    handler = VIDEO_STEP_HANDLERS["stabilise"](stage, VideoStepContext(shared, 0, False, False))
    most = 0
    for emitted, _item in enumerate(handler(source())):
        most = max(most, pulled - emitted)
    assert most == held


def test_cancel_stops_reading():
    items, _ = shaky(texture(384, 504), count=40)
    calls = 0

    def check_cancelled():
        nonlocal calls
        calls += 1
        if calls > 20:
            raise InterruptedError("video job cancelled")

    shared = SimpleNamespace(check_cancelled=check_cancelled, fps=float(FPS))
    stage = VideoFixStage("", None, step_id="stabilise", params={"smoothing": "light"})
    handler = VIDEO_STEP_HANDLERS["stabilise"](stage, VideoStepContext(shared, 0, False, False))
    consumed = iter(items)
    out = []
    with pytest.raises(InterruptedError):
        for item in handler(consumed):
            out.append(item)
    assert 0 < len(out) < 20
    # The 21st check raises right after the 21st frame was read; nothing more is read.
    assert len(list(consumed)) == len(items) - 21


def test_dev_gate(monkeypatch):
    from localsr.worker.server import _engine_features

    monkeypatch.delenv("LOCALSR_DEV_STEPS", raising=False)
    assert "video_stabilise" not in _engine_features([])
    monkeypatch.setenv("LOCALSR_DEV_STEPS", "1")
    assert "video_stabilise" in _engine_features([])


def test_the_worker_runs_the_step(tmp_path, monkeypatch):
    import localsr.worker.server as server_module
    from localsr.core.video_io import decode_timed_frames, probe_video
    from localsr.worker.server import WorkerServer

    monkeypatch.setenv("LOCALSR_DEV_STEPS", "1")
    monkeypatch.setattr(server_module, "send_message", lambda message: None)
    items, _ = shaky(texture(384, 504), count=30)
    source = write_clip(tmp_path / "shaky.webm", items)
    output = tmp_path / "out.mkv"
    WorkerServer()._run_video_job(
        "stabilise",
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
            "stages": [{"step": "stabilise", "params": {"smoothing": "medium"}}],
        },
    )
    probe = probe_video(str(output))
    assert (probe.width, probe.height, probe.fps) == (480, 360, pytest.approx(FPS))
    before = list(decode_timed_frames(str(source)))
    after = list(decode_timed_frames(str(output)))
    assert [f.timestamp for f in after] == [f.timestamp for f in before]
    assert spread(frame_shifts([StreamFrame(f) for f in after])) < (
        spread(frame_shifts([StreamFrame(f) for f in before])) / 2
    )
