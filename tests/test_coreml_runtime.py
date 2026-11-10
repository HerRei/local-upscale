"""Core ML backend: fixed tile windows, the gate and fallbacks, and a real conversion."""

import importlib.util
import json
import re
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import spandrel
import torch
import torch.nn.functional as F

from localsr.core import coreml_runtime, preparation
from localsr.core.coreml_runtime import (
    COMPUTE_UNITS,
    CORE_ML_GPU,
    NEURAL_ENGINE,
    CoreMLImageDescriptor,
    coreml_available,
)
from localsr.core.inference import InferenceEngine, _fixed_window
from localsr.core.tiling import generate_tiles
from localsr.protocol.messages import EngineStatus, JobRequest


def image(height=30, width=40):
    y, x = torch.meshgrid(torch.linspace(0, 1, height), torch.linspace(0, 1, width), indexing="ij")
    return torch.stack([x, y, (x * 3 + y * 5) % 1])


def regular_input(img, tile):
    """The halo-padded input LocalSR builds on its regular grid."""
    region = img[
        :, tile.halo_y : tile.halo_y + tile.halo_h, tile.halo_x : tile.halo_x + tile.halo_w
    ]
    pads = (tile.pad_left, tile.pad_right, tile.pad_top, tile.pad_bottom)
    return F.pad(region, pads, mode="reflect") if any(pads) else region


def test_fixed_window_matches_the_regular_grid_for_full_tiles():
    img = image(64, 64)
    for tile in generate_tiles(64, 64, 16, 4, 2):
        window, top, left = _fixed_window(img, tile, (24, 24))
        assert (top, left) == (4, 4)
        torch.testing.assert_close(window, regular_input(img, tile), rtol=0, atol=0)


def test_fixed_window_fills_edge_tiles_with_real_pixels():
    img = image(30, 40)
    for tile in generate_tiles(40, 30, 16, 4, 2):
        window, top, left = _fixed_window(img, tile, (32, 32))
        assert window.shape == (3, 32, 32)
        core = window[:, top : top + tile.core_h, left : left + tile.core_w]
        expected = img[
            :, tile.core_y : tile.core_y + tile.core_h, tile.core_x : tile.core_x + tile.core_w
        ]
        torch.testing.assert_close(core, expected, rtol=0, atol=0)
        # The core keeps at least the halo of context on every side.
        assert top >= 4 and left >= 4
        assert 32 - top - tile.core_h >= 4 and 32 - left - tile.core_w >= 4


def test_fixed_window_pads_an_image_smaller_than_the_window():
    img = image(10, 12)
    tile = next(generate_tiles(12, 10, 16, 4, 1))
    window, top, left = _fixed_window(img, tile, (32, 32))
    assert window.shape == (3, 32, 32) and (top, left) == (4, 4)
    torch.testing.assert_close(window[:, 4:14, 4:16], img, rtol=0, atol=0)
    # LocalSR's reflected halo at the border, then replicated fill.
    torch.testing.assert_close(window[:, 4:14, 0:4], img[:, :, 1:5].flip(-1), rtol=0, atol=0)
    torch.testing.assert_close(window[:, 4:14, 31], window[:, 4:14, 19], rtol=0, atol=0)


def test_fixed_window_refuses_a_tile_larger_than_the_window():
    tile = next(generate_tiles(64, 64, 32, 8, 1))
    assert _fixed_window(image(64, 64), tile, (40, 40)) is None


class FixedShapeModel:
    """A pointwise x2 model that only accepts one input shape."""

    def __init__(self, side):
        self.fixed_input_shape = (side, side)
        self.input_device = torch.device("cpu")
        self.shapes = []

    def __call__(self, tile):
        self.shapes.append(tuple(tile.shape[-2:]))
        return F.interpolate(tile, scale_factor=2, mode="nearest")


def test_engine_feeds_fixed_shape_models_one_shape_and_stitches_exactly():
    img = image(30, 40)
    model = FixedShapeModel(32)
    engine = InferenceEngine(model_adapter=None)
    engine._loaded_models["fake"] = (model, torch.device("cpu"), torch.float32)
    engine._active_path = "fake"
    engine._sync_legacy_fields()
    info = SimpleNamespace(
        architecture="Fake",
        scale=2,
        out_channels=3,
        size_requirements_mult=1,
        size_requirements_min=0,
    )
    output = engine.process_frame(
        img,
        info,
        tile_size=16,
        halo=4,
        cancel_event=threading.Event(),
        progress_callback=lambda *_: None,
        safe_memory=False,
        float_output=True,
    )
    assert set(model.shapes) == {(32, 32)}
    expected = F.interpolate(img[None], scale_factor=2, mode="nearest")[0].numpy()
    np.testing.assert_allclose(output, expected, atol=0)


class WholeFrameModel(FixedShapeModel):
    """A fixed-shape model that also takes a whole frame per call."""

    def __init__(self):
        super().__init__(32)
        self.prepared = []

    def whole_frame_shape(self, height, width, halo):
        return (-(-(height + 2 * halo) // 16) * 16, -(-(width + 2 * halo) // 16) * 16)

    def prepare_tiles(self, tile_size, halo, frame=None):
        self.prepared.append(frame)
        if frame is not None:
            self.fixed_input_shape = self.whole_frame_shape(*frame, halo)


def test_whole_frame_mode_runs_a_video_frame_in_one_call():
    img = image(30, 40)
    model = WholeFrameModel()
    engine = InferenceEngine(model_adapter=None)
    engine._loaded_models["fake"] = (model, torch.device("cpu"), torch.float32)
    engine._active_path = "fake"
    engine._sync_legacy_fields()
    info = SimpleNamespace(
        architecture="Fake",
        scale=2,
        out_channels=3,
        size_requirements_mult=1,
        size_requirements_min=0,
    )
    output = engine.process_frame(
        img,
        info,
        tile_size=16,
        halo=4,
        cancel_event=threading.Event(),
        progress_callback=lambda *_: None,
        safe_memory=False,
        float_output=True,
        whole_frame=True,
    )
    assert model.shapes == [(48, 48)] and model.prepared == [(30, 40)]
    expected = F.interpolate(img[None], scale_factor=2, mode="nearest")[0].numpy()
    np.testing.assert_allclose(output, expected, atol=0)
    # Video frames come back with interleaved pixels; the CHW result is a view.
    assert output.transpose(1, 2, 0).flags.c_contiguous


def test_prefetch_keeps_order_and_reraises_the_producers_error():
    from localsr.core.video_pipeline import _prefetch

    threads = []

    def items():
        threads.append(threading.current_thread().name)
        yield from range(5)
        raise ValueError("decoder failed")

    received = []
    with pytest.raises(ValueError, match="decoder failed"):
        for item in _prefetch(items(), 2):
            received.append(item)
    assert received == [0, 1, 2, 3, 4]
    assert threads == ["localsr-video-prefetch"]


def test_prefetch_stops_its_producer_when_the_consumer_stops():
    from localsr.core.video_pipeline import _prefetch

    produced, closed = [], threading.Event()

    def items():
        try:
            for index in range(1000):
                produced.append(index)
                yield index
        finally:
            closed.set()

    frames = _prefetch(items(), 2)
    assert next(frames) == 0
    frames.close()
    assert closed.is_set() and len(produced) < 10


def test_automatic_video_format_prefers_the_system_hardware_encoder():
    from localsr.core.media_bridge import automatic_video_codec
    from localsr.core.system_codecs import SystemCodecsError

    class System:
        def encoder_for(self, codec, *, hdr=False, container="mp4"):
            if container != "mp4":
                raise SystemCodecsError("MP4 only")
            return "hevc_videotoolbox"

    bridge = SimpleNamespace(system=System())
    assert automatic_video_codec(bridge, "mp4", "tone_map") == "hevc"
    assert automatic_video_codec(bridge, "mkv", "tone_map") == "av1"
    assert automatic_video_codec(bridge, "mp4", "preserve") == "av1"
    assert automatic_video_codec(SimpleNamespace(system=None), "mp4", "tone_map") == "av1"
    assert automatic_video_codec(None, "mp4", "tone_map") == "av1"


# Gate and fallback logic, with fake Core ML programs --------------------------


class FakeProgram:
    stores: dict = {}
    conversions = 0
    fail_conversion = False

    def __init__(self, root, architecture, identity, shape):
        self.shape = shape
        self.key = (identity, shape)
        self.compiled = SimpleNamespace(exists=lambda: True)
        self.verdicts = FakeProgram.stores.setdefault(self.key, {})
        self.compiled_ready = False

    def verdict(self, unit):
        return self.verdicts.get(unit)

    def store_verdict(self, unit, verdict):
        self.verdicts[unit] = verdict

    def clear_timeouts(self, units):
        for unit in units:
            if self.verdicts.get(unit, {}).get("timed_out"):
                del self.verdicts[unit]

    def ensure_compiled(self, source, input_channels):
        FakeProgram.conversions += 1
        if FakeProgram.fail_conversion:
            raise RuntimeError("unsupported op")


class FakeRunner:
    def __init__(self, unit, behaviour, reference):
        self.unit, self.behaviour, self.reference, self.calls = unit, behaviour, reference, 0

    release = threading.Event()

    def predict(self, inputs):
        self.calls += 1
        if self.behaviour == "hang-predict":
            FakeRunner.release.wait()
        if self.behaviour == "raise":
            raise RuntimeError("ANE error")
        output = self.reference(torch.from_numpy(inputs["image"])).numpy()
        if self.behaviour == "hang-predict":
            return {"restored": output}
        # "flat-only": right on a tile without detail, wrong on real content, like an
        # FP16 program that overflows only where the picture has structure.
        matches = self.behaviour == "match" or (
            self.behaviour == "flat-only" and float(np.std(inputs["image"])) < 0.01
        )
        return {"restored": output if matches else np.zeros_like(output)}


def tiny_descriptor(name="SPAN"):
    torch.manual_seed(0)
    model = torch.nn.Sequential(torch.nn.Conv2d(3, 12, 3, padding=1), torch.nn.PixelShuffle(2))
    with torch.no_grad():
        model[0].weight.mul_(0.1)
    return spandrel.ImageModelDescriptor(
        model.eval(),
        state_dict=model.state_dict(),
        architecture=SimpleNamespace(name=name),
        purpose="SR",
        tags=[],
        supports_half=True,
        supports_bfloat16=True,
        scale=2,
        input_channels=3,
        output_channels=3,
    )


@pytest.fixture
def fake_coreml(monkeypatch):
    FakeProgram.stores = {}
    FakeProgram.conversions = 0
    FakeProgram.fail_conversion = False
    behaviours = {}
    runners = []
    loads = []
    hang = threading.Event()
    original = tiny_descriptor()

    def reference(image):
        with torch.no_grad():
            return original.model(image)

    def load(path, unit):
        behaviour = behaviours.get(unit, "match")
        loads.append(unit)
        if behaviour == "hang":
            # A compile that does not return until the test lets it.
            hang.wait()
        if behaviour == "memory":
            raise MemoryError("Core ML could not allocate memory")
        runner = FakeRunner(unit, behaviour, reference)
        runners.append(runner)
        return runner

    class Compiled:
        def __truediv__(self, _name):
            return SimpleNamespace(exists=lambda: True)

    def program(root, architecture, identity, shape):
        value = FakeProgram(root, architecture, identity, shape)
        value.compiled = Compiled()
        return value

    monkeypatch.setattr(coreml_runtime, "_Program", program)
    monkeypatch.setattr(coreml_runtime, "_load_compiled", load)
    monkeypatch.setattr(
        coreml_runtime._CoreMLImage,
        "_time",
        staticmethod(lambda function, synchronize=None, repeats=2: 1.0),
    )
    preparation._abandoned.clear()
    yield SimpleNamespace(
        behaviours=behaviours, runners=runners, original=original, loads=loads, hang=hang
    )
    hang.set()
    deadline = time.monotonic() + 2
    while preparation.busy_slot("coreml") and time.monotonic() < deadline:
        time.sleep(0.01)


def run(descriptor, side_tile=16, halo=4):
    descriptor.prepare_tiles(side_tile, halo)
    return descriptor(image(20, 20)[None])


def padded_reference(original, sample, shape):
    """What a fixed-shape program computes when called directly on a smaller input."""
    shape = shape if isinstance(shape, tuple) else (shape, shape)
    height, width = sample.shape[-2:]
    padded = F.pad(sample, (0, shape[1] - width, 0, shape[0] - height), mode="replicate")
    return original(padded)[..., : height * original.scale, : width * original.scale]


def test_matching_core_ml_is_accepted_stored_and_reused(fake_coreml):
    messages = []
    converted = CoreMLImageDescriptor(
        fake_coreml.original, units=(NEURAL_ENGINE, CORE_ML_GPU), identity="abc"
    )
    converted.set_status_callback(messages.append)
    first = run(converted)
    second = run(converted)
    expected = padded_reference(fake_coreml.original, image(20, 20)[None], 32)
    torch.testing.assert_close(first, expected)
    torch.testing.assert_close(second, expected)
    assert converted.model.active_unit == NEURAL_ENGINE
    assert converted.fixed_input_shape == (32, 32)
    verdict = FakeProgram.stores[("abc", (32, 32))][NEURAL_ENGINE]
    assert verdict["accepted"] and verdict["psnr"] is None  # identical output
    assert any("runs on the Neural Engine" in message for message in messages)
    # A second wrapper (a later job) trusts the stored verdict: no second gate.
    again = CoreMLImageDescriptor(fake_coreml.original, units=(NEURAL_ENGINE,), identity="abc")
    run(again)
    assert fake_coreml.runners[-1].calls == 1


def test_mismatch_falls_through_to_the_next_unit_then_pytorch(fake_coreml):
    fake_coreml.behaviours.update({NEURAL_ENGINE: "garbage", CORE_ML_GPU: "garbage"})
    converted = CoreMLImageDescriptor(
        fake_coreml.original, units=(NEURAL_ENGINE, CORE_ML_GPU), identity="def"
    )
    output = run(converted)
    torch.testing.assert_close(output, fake_coreml.original(image(20, 20)[None]))
    verdicts = FakeProgram.stores[("def", (32, 32))]
    assert not verdicts[NEURAL_ENGINE]["accepted"] and not verdicts[CORE_ML_GPU]["accepted"]
    assert converted.model.active_unit is None
    # Once ruled out, tiles go back to LocalSR's regular grid and the GPU.
    assert converted.fixed_input_shape is None and converted.input_device is None


def test_a_tile_without_detail_does_not_decide_the_gate(fake_coreml):
    # RealPLKSR HFA2k in FP16 matches FP32 at 64-76 dB on blank tiles but returns
    # non-finite or 6-15 dB output on manga pages (#97). The verdict is kept for later
    # jobs, so a blank first tile (page margin, sky) must not accept the program.
    fake_coreml.behaviours[CORE_ML_GPU] = "flat-only"
    converted = CoreMLImageDescriptor(fake_coreml.original, units=(CORE_ML_GPU,), identity="flat")
    converted.prepare_tiles(16, 4)
    blank = torch.full((1, 3, 20, 20), 0.97)
    torch.testing.assert_close(converted(blank), padded_reference(fake_coreml.original, blank, 32))
    assert not FakeProgram.stores.get(("flat", (32, 32)))
    detailed = image(20, 20)[None]
    torch.testing.assert_close(converted(detailed), fake_coreml.original(detailed))
    assert not FakeProgram.stores[("flat", (32, 32))][CORE_ML_GPU]["accepted"]
    assert converted.model.active_unit is None


def test_gpu_unit_is_used_when_the_neural_engine_does_not_match(fake_coreml):
    fake_coreml.behaviours[NEURAL_ENGINE] = "garbage"
    converted = CoreMLImageDescriptor(
        fake_coreml.original, units=(NEURAL_ENGINE, CORE_ML_GPU), identity="ghi"
    )
    run(converted)
    assert converted.model.active_unit == CORE_ML_GPU


def test_slower_core_ml_keeps_pytorch(fake_coreml, monkeypatch):
    timings = iter([100.0, 10.0])  # Core ML, then PyTorch
    monkeypatch.setattr(
        coreml_runtime._CoreMLImage,
        "_time",
        staticmethod(lambda function, synchronize=None, repeats=2: next(timings)),
    )
    converted = CoreMLImageDescriptor(fake_coreml.original, units=(NEURAL_ENGINE,), identity="jk")
    run(converted)
    verdict = FakeProgram.stores[("jk", (32, 32))][NEURAL_ENGINE]
    assert verdict == {**verdict, "accepted": False, "reason": "slower than PyTorch"}
    assert converted.model.active_unit is None


def test_conversion_failure_rules_out_every_unit_once(fake_coreml, monkeypatch):
    FakeProgram.fail_conversion = True

    class Missing:
        def __truediv__(self, _name):
            return SimpleNamespace(exists=lambda: False)

    original_program = coreml_runtime._Program

    def program(*args):
        value = original_program(*args)
        value.compiled = Missing()
        return value

    monkeypatch.setattr(coreml_runtime, "_Program", program)
    converted = CoreMLImageDescriptor(
        fake_coreml.original, units=(NEURAL_ENGINE, CORE_ML_GPU), identity="lm"
    )
    output = run(converted)
    torch.testing.assert_close(output, fake_coreml.original(image(20, 20)[None]))
    assert FakeProgram.conversions == 1
    assert set(FakeProgram.stores[("lm", (32, 32))]) == {NEURAL_ENGINE, CORE_ML_GPU}


def test_runtime_errors_and_compile_timeouts_fall_back(fake_coreml):
    fake_coreml.behaviours.update({NEURAL_ENGINE: "raise", CORE_ML_GPU: "raise"})
    converted = CoreMLImageDescriptor(
        fake_coreml.original, units=(NEURAL_ENGINE, CORE_ML_GPU), identity="no"
    )
    output = run(converted)
    torch.testing.assert_close(output, fake_coreml.original(image(20, 20)[None]))
    verdicts = FakeProgram.stores[("no", (32, 32))]
    assert "ANE error" in verdicts[CORE_ML_GPU]["reason"]


def test_tiles_larger_than_the_prepared_shape_use_pytorch(fake_coreml):
    converted = CoreMLImageDescriptor(fake_coreml.original, units=(NEURAL_ENGINE,), identity="pq")
    converted.prepare_tiles(8, 2)  # 12 px rounds up to a 16 px program
    output = converted(image(20, 20)[None])
    torch.testing.assert_close(output, fake_coreml.original(image(20, 20)[None]))
    assert fake_coreml.runners == []


def test_a_broken_coremltools_falls_back_instead_of_failing(fake_coreml, monkeypatch):
    def broken(*_args):
        raise ImportError("libcoremlpython missing")

    monkeypatch.setattr(coreml_runtime, "_Program", broken)
    converted = CoreMLImageDescriptor(fake_coreml.original, units=(NEURAL_ENGINE,), identity="b")
    assert converted.fixed_input_shape is None
    torch.testing.assert_close(run(converted), fake_coreml.original(image(20, 20)[None]))
    assert fake_coreml.runners == []


def test_realesrgan_keeps_pytorch_above_its_measured_size(fake_coreml):
    converted = CoreMLImageDescriptor(
        tiny_descriptor("ESRGAN"), units=(NEURAL_ENGINE,), identity="big"
    )
    converted.prepare_tiles(512, 32)  # 576 px
    assert converted.fixed_input_shape is None
    converted = CoreMLImageDescriptor(
        tiny_descriptor("ESRGAN"), units=(NEURAL_ENGINE,), identity="ok"
    )
    converted.prepare_tiles(512, 16)  # 544 px
    assert converted.fixed_input_shape == (544, 544)


def test_spans_whole_frames_get_their_own_rectangular_program(fake_coreml):
    converted = CoreMLImageDescriptor(fake_coreml.original, units=(NEURAL_ENGINE,), identity="wf")
    assert converted.whole_frame_shape(20, 30, 4) == (32, 48)
    assert converted.whole_frame_shape(2000, 2000, 16) is None  # beyond SPAN's limit
    converted.prepare_tiles(16, 4, frame=(20, 30))
    assert converted.fixed_input_shape == (32, 48)
    sample = image(20, 30)[None]
    torch.testing.assert_close(
        converted(sample), padded_reference(fake_coreml.original, sample, (32, 48))
    )
    esrgan = CoreMLImageDescriptor(tiny_descriptor("ESRGAN"), units=(NEURAL_ENGINE,), identity="e")
    assert esrgan.whole_frame_shape(20, 30, 4) is None


def test_whole_frame_compile_needs_free_disk_and_otherwise_falls_back_to_tiles(
    fake_coreml, monkeypatch
):
    """A 1080p SPAN compile took ~6.6 GB of temporary space (QA 0.3.8 F5)."""
    converted = CoreMLImageDescriptor(fake_coreml.original, units=(NEURAL_ENGINE,), identity="wf")
    messages = []
    converted.model.status_callback = messages.append
    state = converted.model._state((1088, 1920))
    state["program"].compiled = type(
        "Missing", (), {"__truediv__": lambda self, name: SimpleNamespace(exists=lambda: False)}
    )()
    free = {"bytes": 4 * 1024**3}
    monkeypatch.setattr(
        coreml_runtime.shutil,
        "disk_usage",
        lambda path: SimpleNamespace(total=0, used=0, free=free["bytes"]),
    )
    assert converted.whole_frame_shape(1080, 1920, 0) is None
    assert any("free disk space" in message for message in messages)
    free["bytes"] = 40 * 1024**3
    assert converted.whole_frame_shape(1080, 1920, 0) == (1088, 1920)
    # A program that is already compiled needs no space.
    free["bytes"] = 1
    state["program"].compiled = type(
        "Done", (), {"__truediv__": lambda self, name: SimpleNamespace(exists=lambda: True)}
    )()
    assert converted.whole_frame_shape(1080, 1920, 0) == (1088, 1920)


def test_cancellation_stops_before_core_ml_runs(fake_coreml):
    converted = CoreMLImageDescriptor(fake_coreml.original, units=(NEURAL_ENGINE,), identity="rs")
    cancelled = threading.Event()
    cancelled.set()
    converted.set_cancel_event(cancelled)
    with pytest.raises(InterruptedError):
        run(converted)


def test_engine_uses_core_ml_only_for_listed_architectures_on_mps(monkeypatch):
    calls = []

    class Adapter:
        def load(self, path, device, precision, **options):
            calls.append(options)
            return SimpleNamespace(), None

    monkeypatch.setattr(coreml_runtime, "COREML_UNITS_OVERRIDE", {"SPAN": (NEURAL_ENGINE,)})
    monkeypatch.setattr(coreml_runtime, "coreml_available", lambda: True)
    monkeypatch.setattr(
        "localsr.core.inference._resolve_torch_device", lambda _id: torch.device("mps")
    )
    monkeypatch.setattr("localsr.core.inference._apply_gpu_memory_limit", lambda *_: None)
    engine = InferenceEngine(Adapter())
    info = SimpleNamespace(architecture="SPAN", half_supported=False)
    engine.load_model("a.pth", "mps", "fp32", info)
    engine.load_model("b.pth", "mps", "fp32", SimpleNamespace(architecture="HAT", half_supported=0))
    engine.neural_engine_enabled = False
    engine.load_model("c.pth", "mps", "fp32", info)
    assert calls == [{"coreml_units": (NEURAL_ENGINE,)}, {}, {}]
    # Turning the setting off between jobs reloads a cached model without Core ML.
    engine.load_model("a.pth", "mps", "fp32", info)
    assert calls[-1] == {}


def test_desktop_names_the_same_core_ml_architectures_as_the_worker():
    from localsr.core.model_catalog import CATALOG_BY_FILENAME

    source = (
        Path(__file__).resolve().parents[1] / "desktop" / "src" / "lib" / "neuralEngine.ts"
    ).read_text(encoding="utf-8")

    def listed(name):
        return set(re.findall(r"'(\w+)'", re.search(rf"{name} = new Set\(\[(.*?)\]\)", source)[1]))

    # The catalog names two Spandrel architectures differently.
    catalog_name = {"ESRGAN": "RealESRGAN", "PLKSR": "RealPLKSR"}

    def expected(unit):
        return {catalog_name.get(a, a) for a, units in COMPUTE_UNITS.items() if units[0] == unit}

    assert listed("NEURAL_ENGINE_ARCHITECTURES") == expected(NEURAL_ENGINE)
    assert listed("CORE_ML_GPU_ARCHITECTURES") == expected(CORE_ML_GPU)
    catalog = {model.architecture for model in CATALOG_BY_FILENAME.values()}
    assert expected(NEURAL_ENGINE) | expected(CORE_ML_GPU) <= catalog


def test_protocol_carries_the_setting_and_the_status_message():
    assert JobRequest.__dataclass_fields__["neural_engine"].default is True
    message = json.loads(EngineStatus(job_id="j", message="SPAN runs on X.").to_json())
    assert message == {
        "type": "engine_status",
        "data": {"job_id": "j", "message": "SPAN runs on X."},
    }


# A real conversion on Apple silicon --------------------------------------------


@pytest.mark.skipif(not coreml_available(), reason="needs Core ML on Apple silicon")
def test_real_conversion_runs_and_matches_pytorch(tmp_path, monkeypatch):
    from spandrel.architectures.SPAN import SPAN

    monkeypatch.setenv(coreml_runtime.CACHE_ENV, str(tmp_path))
    # Decide on accuracy alone; tiny CPU models make timing meaningless.
    monkeypatch.setattr(
        coreml_runtime._CoreMLImage,
        "_time",
        staticmethod(lambda function, synchronize=None, repeats=2: 1.0),
    )
    torch.manual_seed(0)
    network = SPAN(num_in_ch=3, num_out_ch=3, feature_channels=8, upscale=2).eval()
    with torch.no_grad():
        # Untrained SPAN weights produce values far outside [0, 1], which the
        # gate treats as an unstable reference; shrink them like a real model.
        for parameter in network.parameters():
            parameter.mul_(0.3)
    original = spandrel.ModelLoader().load_from_state_dict(network.state_dict()).eval()
    assert original.architecture.name == "SPAN"
    converted = CoreMLImageDescriptor(original, units=(NEURAL_ENGINE, CORE_ML_GPU), identity="t")
    sample = image(24, 24)[None]
    converted.prepare_tiles(16, 4)
    output = converted(sample)
    expected = padded_reference(original, sample, 32)
    assert output.shape == expected.shape == (1, 3, 48, 48)
    error = float(torch.mean((output.clamp(0, 1) - expected.clamp(0, 1)) ** 2))
    assert error < 1e-4
    verdicts = json.loads(next(tmp_path.glob("SPAN-*/verdicts.json")).read_text())
    assert verdicts[next(iter(verdicts))]["accepted"]
    if importlib.util.find_spec("CoreML") is not None:
        # Predictions go through PyObjC, which releases the GIL.
        state = next(iter(converted.model.states.values()))
        assert isinstance(state["runner"], coreml_runtime._NativeRunner)
    assert converted.model.active_unit in {NEURAL_ENGINE, CORE_ML_GPU}


def test_gate_times_fallback_on_fallback_device(fake_coreml, monkeypatch):
    devices = []
    converted = CoreMLImageDescriptor(fake_coreml.original, units=(NEURAL_ENGINE,), identity="sync")
    converted.model.fallback_device = torch.device("mps")
    monkeypatch.setattr(
        converted.model, "_run_fallback", lambda tensor: fake_coreml.original(tensor)
    )
    monkeypatch.setattr(
        coreml_runtime._CoreMLImage,
        "_time",
        staticmethod(
            lambda function, synchronize=None, repeats=2: devices.append(synchronize) or 1.0
        ),
    )
    run(converted)
    assert devices == [None, torch.device("mps")]


def test_batch_lanes_gate_both_units_at_one_shape_and_own_their_buffers(fake_coreml, monkeypatch):
    monkeypatch.setattr(coreml_runtime, "_NativeRunner", FakeRunner)
    descriptor = CoreMLImageDescriptor(
        fake_coreml.original, units=(NEURAL_ENGINE, CORE_ML_GPU), identity="batch"
    )
    lanes = descriptor.batch_lanes(image(32, 32)[None], (32, 32), gpu=True)
    assert len(lanes) == 4 and len({id(runner) for runner in lanes}) == 4
    assert [runner.unit for runner in lanes] == [NEURAL_ENGINE] * 2 + [CORE_ML_GPU] * 2
    verdicts = FakeProgram.stores[("batch", (32, 32))]
    assert all(verdicts[unit]["accepted"] for unit in (NEURAL_ENGINE, CORE_ML_GPU))
    assert descriptor.batch_lanes(image(32, 32)[None], (32, 32), gpu=True) == lanes
    battery = descriptor.batch_lanes(image(32, 32)[None], (32, 32), gpu=False)
    assert battery == lanes[:2]
    descriptor.batch_lanes(image(48, 48)[None], (48, 48), gpu=False)
    assert FakeProgram.stores[("batch", (48, 48))][NEURAL_ENGINE]["accepted"]


@pytest.mark.parametrize("unit", [NEURAL_ENGINE, CORE_ML_GPU])
def test_batch_lanes_never_use_a_unit_rejected_by_the_gate(fake_coreml, monkeypatch, unit):
    monkeypatch.setattr(coreml_runtime, "_NativeRunner", FakeRunner)
    fake_coreml.behaviours[unit] = "wrong"
    descriptor = CoreMLImageDescriptor(
        fake_coreml.original, units=(NEURAL_ENGINE, CORE_ML_GPU), identity="reject-batch"
    )
    lanes = descriptor.batch_lanes(image(32, 32)[None], (32, 32), gpu=True)
    assert len(lanes) == (0 if unit == NEURAL_ENGINE else 2)
    assert not FakeProgram.stores[("reject-batch", (32, 32))][unit]["accepted"]


def test_batch_requires_native_gil_releasing_runners(fake_coreml):
    descriptor = CoreMLImageDescriptor(
        fake_coreml.original, units=(NEURAL_ENGINE, CORE_ML_GPU), identity="no-native"
    )
    assert descriptor.batch_lanes(image(32, 32)[None], (32, 32), gpu=True) == []


def test_batch_lanes_say_which_units_share_the_work(fake_coreml, monkeypatch):
    monkeypatch.setattr(coreml_runtime, "_NativeRunner", FakeRunner)
    descriptor = CoreMLImageDescriptor(
        fake_coreml.original, units=(NEURAL_ENGINE, CORE_ML_GPU), identity="status"
    )
    messages = []
    descriptor.set_status_callback(messages.append)
    descriptor.batch_lanes(image(32, 32)[None], (32, 32), gpu=True)
    assert messages[-1] == "SPAN runs on the Neural Engine and the GPU (Core ML)."
    descriptor.batch_lanes(image(32, 32)[None], (32, 32), gpu=False)
    assert messages[-1] == "SPAN runs on the Neural Engine."


def test_batch_lanes_keep_the_normal_path_when_the_gate_is_inconclusive(fake_coreml, monkeypatch):
    monkeypatch.setattr(coreml_runtime, "_NativeRunner", FakeRunner)
    descriptor = CoreMLImageDescriptor(
        fake_coreml.original, units=(NEURAL_ENGINE, CORE_ML_GPU), identity="unstable"
    )
    # A reference that leaves the normal value range cannot decide the gate.
    descriptor.model.fallback_call = lambda _model, value: torch.full_like(value, 9.0)
    assert descriptor.batch_lanes(image(32, 32)[None], (32, 32), gpu=True) == []
    assert NEURAL_ENGINE not in FakeProgram.stores[("unstable", (32, 32))]


def test_a_unit_with_a_stored_rejection_is_never_loaded_as_a_lane(fake_coreml, monkeypatch):
    monkeypatch.setattr(coreml_runtime, "_NativeRunner", FakeRunner)
    FakeProgram.stores[("stored", (32, 32))] = {CORE_ML_GPU: {"accepted": False}}
    descriptor = CoreMLImageDescriptor(
        fake_coreml.original, units=(NEURAL_ENGINE, CORE_ML_GPU), identity="stored"
    )
    lanes = descriptor.batch_lanes(image(32, 32)[None], (32, 32), gpu=True)
    assert [runner.unit for runner in lanes] == [NEURAL_ENGINE] * 2
    assert CORE_ML_GPU not in {runner.unit for runner in fake_coreml.runners}
    # A gate that rejects a unit leaves no runner of it in the lanes either.
    fake_coreml.behaviours[NEURAL_ENGINE] = "wrong"
    fresh = CoreMLImageDescriptor(
        fake_coreml.original, units=(NEURAL_ENGINE, CORE_ML_GPU), identity="fresh-reject"
    )
    assert fresh.batch_lanes(image(32, 32)[None], (32, 32), gpu=True) == []


def test_a_forgotten_batch_unit_gets_fresh_runners(fake_coreml, monkeypatch):
    monkeypatch.setattr(coreml_runtime, "_NativeRunner", FakeRunner)
    descriptor = CoreMLImageDescriptor(
        fake_coreml.original, units=(NEURAL_ENGINE, CORE_ML_GPU), identity="forget"
    )
    first = descriptor.batch_lanes(image(32, 32)[None], (32, 32), gpu=True)
    descriptor.forget_batch_unit(CORE_ML_GPU)
    second = descriptor.batch_lanes(image(32, 32)[None], (32, 32), gpu=True)
    assert second[:2] == first[:2]
    assert not {id(runner) for runner in second[2:]} & {id(runner) for runner in first[2:]}
    assert [runner.unit for runner in second[2:]] == [CORE_ML_GPU] * 2


def test_old_cached_verdict_is_not_trusted(tmp_path, monkeypatch):
    monkeypatch.setitem(
        __import__("sys").modules, "coremltools", SimpleNamespace(__version__="test")
    )
    program = coreml_runtime._Program(tmp_path, "SPAN", "abc", (32, 32))
    program.directory.mkdir(parents=True)
    program.verdicts_path.write_text(
        json.dumps({program._verdict_key(CORE_ML_GPU): {"accepted": True}})
    )
    assert program.verdict(CORE_ML_GPU) is None


def test_uniform_coloured_tile_does_not_decide_gate(fake_coreml):
    converted = CoreMLImageDescriptor(fake_coreml.original, units=(CORE_ML_GPU,), identity="colour")
    converted.prepare_tiles(16, 4)
    tile = torch.tensor([1.0, 0.8, 0.6]).view(1, 3, 1, 1).expand(1, 3, 20, 20)
    torch.testing.assert_close(converted(tile), padded_reference(fake_coreml.original, tile, 32))
    assert not FakeProgram.stores.get(("colour", (32, 32)))


def test_automatic_video_format_never_picks_a_codec_the_mov_container_refuses():
    from localsr.core.media_bridge import automatic_video_codec
    from localsr.core.media_codecs import MOV_CODECS

    for bridge in (None, SimpleNamespace(system=None)):
        for hdr_mode in ("reject", "tone_map", "preserve"):
            assert automatic_video_codec(bridge, "mov", hdr_mode) in MOV_CODECS


MPS_OUT_OF_MEMORY = (
    "MPS backend out of memory (MPS allocated 7.02 GiB, other allocations 1.00 GiB, "
    "max allowed 7.34 GiB). Tried to allocate 1.00 GiB on private pool."
)


def test_a_reference_out_of_memory_stores_no_rejection_and_falls_back(fake_coreml, monkeypatch):
    converted = CoreMLImageDescriptor(
        fake_coreml.original, units=(NEURAL_ENGINE, CORE_ML_GPU), identity="oom"
    )
    real = coreml_runtime._CoreMLImage._run_fallback
    calls = []

    def fallback(self, padded):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError(MPS_OUT_OF_MEMORY)
        return real(self, padded)

    monkeypatch.setattr(coreml_runtime._CoreMLImage, "_run_fallback", fallback)
    output = run(converted)
    torch.testing.assert_close(output, fake_coreml.original(image(20, 20)[None]))
    assert not FakeProgram.stores.get(("oom", (32, 32)))
    assert converted.model.active_unit is None


def test_an_accuracy_failure_is_still_stored(fake_coreml):
    fake_coreml.behaviours.update({NEURAL_ENGINE: "garbage"})
    converted = CoreMLImageDescriptor(fake_coreml.original, units=(NEURAL_ENGINE,), identity="acc")
    run(converted)
    assert not FakeProgram.stores[("acc", (32, 32))][NEURAL_ENGINE]["accepted"]


def test_a_stored_out_of_memory_rejection_is_ignored_when_read(tmp_path, monkeypatch):
    monkeypatch.setitem(
        __import__("sys").modules, "coremltools", SimpleNamespace(__version__="test")
    )
    program = coreml_runtime._Program(tmp_path, "SPAN", "abc", (32, 32))
    program.store_verdict(NEURAL_ENGINE, {"accepted": False, "reason": MPS_OUT_OF_MEMORY})
    program.store_verdict(CORE_ML_GPU, {"accepted": False, "reason": "did not match PyTorch"})
    assert program.verdict(NEURAL_ENGINE) is None
    assert program.verdict(CORE_ML_GPU)["accepted"] is False


# A compile that does not finish -------------------------------------------------


def wait_for_idle_slot():
    deadline = time.monotonic() + 2
    while preparation.busy_slot("coreml") and time.monotonic() < deadline:
        time.sleep(0.01)


@pytest.fixture
def short_budget(monkeypatch):
    monkeypatch.setattr(coreml_runtime, "PREPARE_BUDGET_SECONDS", 0.4)
    monkeypatch.setattr(preparation, "WAIT_SLICE_SECONDS", 0.02)
    monkeypatch.setattr(preparation, "STATUS_INTERVAL_SECONDS", 0.1)


def test_a_compile_that_never_returns_is_stored_and_the_next_job_skips_the_unit(
    fake_coreml, short_budget
):
    fake_coreml.behaviours[NEURAL_ENGINE] = "hang"
    first = CoreMLImageDescriptor(
        fake_coreml.original, units=(NEURAL_ENGINE, CORE_ML_GPU), identity="hang"
    )
    started = time.monotonic()
    output = run(first)
    assert time.monotonic() - started < 2.0
    torch.testing.assert_close(output, fake_coreml.original(image(20, 20)[None]))
    verdict = FakeProgram.stores[("hang", (32, 32))][NEURAL_ENGINE]
    assert verdict["timed_out"] and not verdict["accepted"] and verdict["retry_after"]
    # One budget for the whole route: the GPU unit is not tried after the budget is gone.
    assert fake_coreml.loads == [NEURAL_ENGINE]

    second = CoreMLImageDescriptor(
        fake_coreml.original, units=(NEURAL_ENGINE, CORE_ML_GPU), identity="hang"
    )
    fake_coreml.loads.clear()
    started = time.monotonic()
    run(second)
    assert time.monotonic() - started < 0.3
    # The stored timeout skips the Neural Engine at once. The abandoned compile still runs
    # and is the one background compile, so the GPU unit waits for the next job.
    assert fake_coreml.loads == []


def test_the_budget_is_shared_by_every_unit_of_a_job(fake_coreml, short_budget):
    fake_coreml.behaviours.update({NEURAL_ENGINE: "hang", CORE_ML_GPU: "hang"})
    converted = CoreMLImageDescriptor(
        fake_coreml.original, units=(NEURAL_ENGINE, CORE_ML_GPU), identity="shared"
    )
    started = time.monotonic()
    run(converted)
    assert time.monotonic() - started < 0.4 * 1.8
    assert fake_coreml.loads == [NEURAL_ENGINE]
    # The route is given up for the rest of this job; the next tile does not wait again.
    started = time.monotonic()
    converted(image(20, 20)[None])
    assert time.monotonic() - started < 0.1


def test_a_hung_conversion_is_bounded_and_stored_for_every_unit(
    fake_coreml, short_budget, monkeypatch
):
    release = threading.Event()
    monkeypatch.setattr(
        FakeProgram, "ensure_compiled", lambda self, source, channels: release.wait()
    )

    class Missing:
        def __truediv__(self, _name):
            return SimpleNamespace(exists=lambda: False)

    original_program = coreml_runtime._Program

    def program(*args):
        value = original_program(*args)
        value.compiled = Missing()
        return value

    monkeypatch.setattr(coreml_runtime, "_Program", program)
    converted = CoreMLImageDescriptor(
        fake_coreml.original, units=(NEURAL_ENGINE, CORE_ML_GPU), identity="conv"
    )
    started = time.monotonic()
    output = run(converted)
    assert time.monotonic() - started < 2.0
    torch.testing.assert_close(output, fake_coreml.original(image(20, 20)[None]))
    verdicts = FakeProgram.stores[("conv", (32, 32))]
    assert verdicts[NEURAL_ENGINE]["timed_out"] and verdicts[CORE_ML_GPU]["timed_out"]
    release.set()


def test_cancelling_while_core_ml_prepares_returns_at_once_and_the_engine_stays_usable(
    fake_coreml,
):
    fake_coreml.behaviours[NEURAL_ENGINE] = "hang"
    converted = CoreMLImageDescriptor(fake_coreml.original, units=(NEURAL_ENGINE,), identity="cx")
    cancel = threading.Event()
    converted.set_cancel_event(cancel)
    threading.Timer(0.2, cancel.set).start()
    started = time.monotonic()
    with pytest.raises(InterruptedError):
        run(converted)
    assert time.monotonic() - started < 2.0
    # Nothing is stored: the user, not the program, ended the wait.
    assert not FakeProgram.stores.get(("cx", (32, 32)))
    fake_coreml.hang.set()
    deadline = time.monotonic() + 2
    while preparation.busy_slot("coreml") and time.monotonic() < deadline:
        time.sleep(0.01)
    # The same engine runs the next job.
    fake_coreml.behaviours[NEURAL_ENGINE] = "match"
    again = CoreMLImageDescriptor(fake_coreml.original, units=(NEURAL_ENGINE,), identity="cx2")
    run(again)
    assert again.model.active_unit == NEURAL_ENGINE


def test_an_out_of_memory_load_stores_nothing(fake_coreml):
    fake_coreml.behaviours[NEURAL_ENGINE] = "memory"
    converted = CoreMLImageDescriptor(fake_coreml.original, units=(NEURAL_ENGINE,), identity="mem")
    output = run(converted)
    torch.testing.assert_close(output, fake_coreml.original(image(20, 20)[None]))
    assert not FakeProgram.stores.get(("mem", (32, 32)))


def test_a_compile_left_running_by_an_earlier_job_is_not_joined_by_a_second(
    fake_coreml, short_budget
):
    fake_coreml.behaviours[NEURAL_ENGINE] = "hang"
    run(CoreMLImageDescriptor(fake_coreml.original, units=(NEURAL_ENGINE,), identity="a1"))
    fake_coreml.behaviours[NEURAL_ENGINE] = "match"
    fake_coreml.loads.clear()
    started = time.monotonic()
    other = CoreMLImageDescriptor(fake_coreml.original, units=(NEURAL_ENGINE,), identity="a2")
    messages = []
    other.set_status_callback(messages.append)
    run(other)
    assert time.monotonic() - started < 0.3
    assert fake_coreml.loads == []
    # Not the program's fault, so nothing is stored for it.
    assert not FakeProgram.stores.get(("a2", (32, 32)))
    assert messages[-1] == (
        "Continuing on the CPU: Core ML is still preparing an earlier model in the background."
    )


def test_the_wait_says_what_happens_and_when_the_route_is_given_up(fake_coreml, short_budget):
    fake_coreml.behaviours[NEURAL_ENGINE] = "hang"
    converted = CoreMLImageDescriptor(fake_coreml.original, units=(NEURAL_ENGINE,), identity="msg")
    messages, notes = [], []
    converted.set_status_callback(messages.append)
    converted.set_note_callback(notes.append)
    run(converted)
    waiting = [m for m in messages if m.startswith("Preparing SPAN")]
    assert len(waiting) >= 3
    assert waiting[0] == (
        "Preparing SPAN for the Neural Engine (first run of this model and tile size). "
        "0 s so far. Continuing on the CPU in 0 s if it is not ready."
    )
    assert not any("can take a minute" in message for message in messages)
    expected = "Continuing on the CPU: Core ML was not ready in time."
    assert messages[-1] == expected and notes == [expected]


def test_the_fallback_is_named_after_the_device_it_runs_on(fake_coreml):
    converted = CoreMLImageDescriptor(fake_coreml.original, units=(NEURAL_ENGINE,), identity="dev")
    assert converted.model._fallback_label() == "the CPU"
    converted.model.fallback_device = torch.device("mps")
    assert converted.model._fallback_label() == "the Apple GPU"


@pytest.fixture
def real_program(tmp_path, monkeypatch):
    monkeypatch.setitem(
        __import__("sys").modules, "coremltools", SimpleNamespace(__version__="test")
    )
    return coreml_runtime._Program(tmp_path, "PLKSR", "abc", (400, 400))


def test_a_stored_timeout_holds_for_a_week_and_for_this_app_version(real_program, monkeypatch):
    now = datetime.now(UTC)
    real_program.store_verdict(CORE_ML_GPU, coreml_runtime.timeout_verdict(120.0, now))
    stored = real_program.verdict(CORE_ML_GPU)
    assert stored["timed_out"] and not stored["accepted"]
    # The wording must not be taken for a resource failure and ignored.
    assert not coreml_runtime._is_resource_failure(stored["reason"])
    later = coreml_runtime.TIMEOUT_RETRY_SECONDS
    monkeypatch.setattr(coreml_runtime, "_now", lambda: now + timedelta(seconds=later - 60))
    assert real_program.verdict(CORE_ML_GPU) is not None
    monkeypatch.setattr(coreml_runtime, "_now", lambda: now + timedelta(seconds=later + 60))
    assert real_program.verdict(CORE_ML_GPU) is None
    monkeypatch.setattr(coreml_runtime, "_now", lambda: now)
    monkeypatch.setattr(coreml_runtime, "APP_VERSION", "9.9.9")
    assert real_program.verdict(CORE_ML_GPU) is None


def test_a_compile_that_finishes_late_lifts_its_stored_timeout(real_program):
    real_program.store_verdict(CORE_ML_GPU, coreml_runtime.timeout_verdict(120.0))
    real_program.store_verdict(NEURAL_ENGINE, {"accepted": False, "reason": "did not match"})
    real_program.clear_timeouts((CORE_ML_GPU, NEURAL_ENGINE))
    assert real_program.verdict(CORE_ML_GPU) is None
    # A rejection for a wrong result stays.
    assert real_program.verdict(NEURAL_ENGINE)["accepted"] is False


def test_conversion_does_not_load_the_program_it_has_just_made(tmp_path, monkeypatch):
    """ct.convert loads its result on every compute unit unless told not to, which
    compiles a second time for the Neural Engine, without a bound and for as long as
    that compiler takes on a large 17x17 kernel."""
    calls = {}

    class Converted:
        def save(self, path):
            Path(path).mkdir()

    def convert(traced, **options):
        calls.update(options)
        return Converted()

    def compile_model(package):
        target = Path(package).with_suffix(".mlmodelc")
        target.mkdir()
        (target / "coremldata.bin").write_bytes(b"")
        return str(target)

    fake = SimpleNamespace(
        __version__="test",
        convert=convert,
        TensorType=lambda **options: options,
        precision=SimpleNamespace(FLOAT16="fp16"),
        target=SimpleNamespace(macOS14="macOS14"),
        models=SimpleNamespace(utils=SimpleNamespace(compile_model=compile_model)),
    )
    monkeypatch.setitem(__import__("sys").modules, "coremltools", fake)
    program = coreml_runtime._Program(tmp_path, "SPAN", "abc", (16, 16))
    program.ensure_compiled(tiny_descriptor().model.eval(), 3)
    assert calls["skip_model_load"] is True
    assert (program.compiled / "coremldata.bin").exists()


def test_a_compile_that_finishes_in_the_background_is_used_by_the_next_job(
    fake_coreml, short_budget
):
    fake_coreml.behaviours[NEURAL_ENGINE] = "hang"
    run(CoreMLImageDescriptor(fake_coreml.original, units=(NEURAL_ENGINE,), identity="late"))
    assert FakeProgram.stores[("late", (32, 32))][NEURAL_ENGINE]["timed_out"]
    # The compile ends; loading again is quick, so the system kept the result.
    fake_coreml.behaviours[NEURAL_ENGINE] = "match"
    fake_coreml.hang.set()
    wait_for_idle_slot()
    assert NEURAL_ENGINE not in FakeProgram.stores[("late", (32, 32))]
    again = CoreMLImageDescriptor(fake_coreml.original, units=(NEURAL_ENGINE,), identity="late")
    run(again)
    assert again.model.active_unit == NEURAL_ENGINE


def test_a_first_prediction_that_never_returns_is_bounded_like_the_load(fake_coreml, short_budget):
    fake_coreml.behaviours[NEURAL_ENGINE] = "hang-predict"
    FakeRunner.release.clear()
    converted = CoreMLImageDescriptor(fake_coreml.original, units=(NEURAL_ENGINE,), identity="fp")
    notes = []
    converted.set_note_callback(notes.append)
    started = time.monotonic()
    output = run(converted)
    assert time.monotonic() - started < 2.0
    torch.testing.assert_close(output, fake_coreml.original(image(20, 20)[None]))
    assert FakeProgram.stores[("fp", (32, 32))][NEURAL_ENGINE]["timed_out"]
    assert notes == ["Continuing on the CPU: Core ML was not ready in time."]
    FakeRunner.release.set()
