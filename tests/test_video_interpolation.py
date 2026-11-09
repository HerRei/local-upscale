"""Frame interpolation inside real video jobs: order, timing, audio and refusals."""

from __future__ import annotations

import threading
from fractions import Fraction
from pathlib import Path
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
    tail = float(selected[-1].timestamp - selected[0].timestamp)
    expected += [tail, tail + float(selected[-1].duration) / 2]
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
    assert engine.frames == 2 * count == len(timestamps(output))
    assert [index for index, _ in started] == list(range(2 * count))
    assert {total for _, total in started} == {2 * count}


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
    assert len(timestamps(output)) == 32
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


# The worker: request fields, the verified RIFE bundle, and engine routing.


class BundleStore:
    def __init__(self, bundle, installed=True):
        self.bundle, self.installed, self.hashed = bundle, installed, False

    def bundle_dir_for(self, model):
        assert model.model_id == "rife_4_26"
        return self.bundle

    def bundle_file_path(self, model, file):
        return self.bundle / file.filename

    def is_bundle_installed(self, model):
        self.hashed = True
        return self.installed


def worker_request(tmp_path, **fields):
    return {
        "video_path": "input.mp4",
        "model_path": "model.pth",
        "output_video_path": str(tmp_path / "output.mp4"),
        "device": "mps",
        "precision": "fp32",
        "tile_size": 64,
        "halo": 8,
        **fields,
    }


def patched_worker(monkeypatch, tmp_path, *, installed=True):
    from test_worker import DummyModelAdapter

    import localsr.video_models.rife.engine as rife_engine
    import localsr.worker.server as server_module

    loaded = []

    class FakeRife:
        def __init__(self, checkpoint, device_str, **_options):
            loaded.append((Path(checkpoint), device_str))

    captured = {}

    def fake_video_job(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            output_path="output.mp4", frames_processed=1, elapsed_seconds=1.0, inference_seconds=1.0
        )

    monkeypatch.setattr(rife_engine, "RifeSynthesizer", FakeRife)
    monkeypatch.setattr(server_module, "run_video_job", fake_video_job)
    monkeypatch.setattr(server_module, "send_message", lambda _message: None)
    server = server_module.WorkerServer()
    server.model_adapter = DummyModelAdapter()
    bundle = tmp_path / "rife_4_26"
    bundle.mkdir()
    server.model_store = BundleStore(bundle, installed)
    return server, bundle, loaded, captured


def test_worker_runs_interpolation_with_the_verified_rife_bundle(monkeypatch, tmp_path):
    server, bundle, loaded, captured = patched_worker(monkeypatch, tmp_path)
    server._run_video_job(
        "interpolate",
        worker_request(
            tmp_path,
            interpolation="fps60",
            interpolation_order="before_upscale",
            interpolation_model_id="rife_4_26",
            interpolation_bundle_dir=str(bundle),
        ),
    )
    assert server.model_store.hashed
    assert loaded == [(bundle / "flownet_v4.26.pkl", "mps")]
    config = captured["config"]
    assert (config.interpolation, config.interpolation_order) == ("fps60", "before_upscale")
    assert captured["synthesizer"] is not None


def test_worker_without_interpolation_never_loads_rife(monkeypatch, tmp_path):
    server, _bundle, loaded, captured = patched_worker(monkeypatch, tmp_path)
    server._run_video_job("plain", worker_request(tmp_path))
    assert loaded == []
    assert captured["synthesizer"] is None
    assert captured["config"].interpolation == "off"


def test_worker_refuses_an_interpolation_bundle_outside_the_catalog_path(monkeypatch, tmp_path):
    server, _bundle, loaded, _ = patched_worker(monkeypatch, tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    with pytest.raises(ValueError, match="does not match"):
        server._run_video_job(
            "untrusted",
            worker_request(
                tmp_path,
                interpolation="x2",
                interpolation_model_id="rife_4_26",
                interpolation_bundle_dir=str(elsewhere),
            ),
        )
    assert not server.model_store.hashed
    assert loaded == []


def test_worker_refuses_an_interpolation_model_that_fails_its_checksum(monkeypatch, tmp_path):
    server, bundle, loaded, _ = patched_worker(monkeypatch, tmp_path, installed=False)
    with pytest.raises(ValueError, match="SHA-256"):
        server._run_video_job(
            "corrupt",
            worker_request(
                tmp_path,
                interpolation="x2",
                interpolation_model_id="rife_4_26",
                interpolation_bundle_dir=str(bundle),
            ),
        )
    assert loaded == []


def test_worker_refuses_unknown_interpolation_models(monkeypatch, tmp_path):
    server, bundle, _, _ = patched_worker(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="trusted catalog"):
        server._run_video_job(
            "unknown",
            worker_request(
                tmp_path,
                interpolation="x2",
                interpolation_model_id="gmfss",
                interpolation_bundle_dir=str(bundle),
            ),
        )


def test_seedvr2_jobs_refuse_interpolation_in_this_release(monkeypatch, tmp_path):
    server, bundle, _, _ = patched_worker(monkeypatch, tmp_path)
    monkeypatch.setattr("localsr.core.video_engines.seedvr2_runtime_issue", lambda: None)
    with pytest.raises(ValueError, match="frame-by-frame"):
        server._run_video_job(
            "seedvr2",
            worker_request(
                tmp_path,
                model_kind="seedvr2",
                device="cuda:0",
                interpolation="x2",
                interpolation_bundle_dir=str(bundle),
            ),
        )


def test_the_engine_advertises_frame_interpolation():
    from localsr.worker.server import _engine_features

    assert "video_interpolation" in _engine_features()


def test_the_protocol_carries_interpolation_with_off_by_default():
    import json

    from localsr.protocol.messages import VideoJobRequest

    fields = dict(
        job_id="j",
        video_path="in.mp4",
        model_path="m.pth",
        output_video_path="out.mp4",
        container="mp4",
        crf=18,
        fps=None,
        device="cpu",
        tile_size=128,
        halo=16,
        precision="fp32",
        safe_memory=False,
    )
    data = json.loads(VideoJobRequest(**fields).to_json())["data"]
    assert data["interpolation"] == "off"
    assert data["interpolation_order"] == "after_upscale"
    assert data["interpolation_model_id"] is None
    assert data["interpolation_bundle_dir"] is None

    schema = json.loads(
        (Path(__file__).parents[1] / "protocol" / "worker-protocol.schema.json").read_text()
    )
    properties = schema["$defs"]["videoJobRequest"]["allOf"][1]["properties"]["data"]["allOf"][1][
        "properties"
    ]
    assert properties["interpolation"]["enum"] == ["off", "x2", "x4", "fps60", "fps120"]
    assert properties["interpolation"]["default"] == "off"
    assert properties["interpolation_order"]["enum"] == ["after_upscale", "before_upscale"]
    assert properties["interpolation_model_id"]["type"] == ["string", "null"]
    assert properties["interpolation_bundle_dir"]["type"] == ["string", "null"]


def test_progress_does_not_finish_before_synthesis(tmp_path):
    source = make_cfr(tmp_path / "source.webm", frames=3)
    progress = []

    class CheckingSynthesizer(RecordingSynthesizer):
        def synthesize(self, first, second, timesteps, cancel_event=None):
            assert not progress or progress[-1][0] < progress[-1][1]
            return super().synthesize(first, second, timesteps, cancel_event)

    run_video_job(
        VideoJobConfig(
            str(source),
            "test",
            str(tmp_path / "out.mkv"),
            SimpleNamespace(scale=1),
            "cpu",
            "fp32",
            64,
            4,
            False,
            interpolation="x4",
        ),
        CountingEngine(),
        threading.Event(),
        synthesizer=CheckingSynthesizer(),
        progress_cb=lambda done, total, elapsed: progress.append((done, total)),
    )
    assert progress[-1][0] == progress[-1][1]
