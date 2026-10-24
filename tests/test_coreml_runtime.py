"""Core ML backend: fixed tile windows, the gate and fallbacks, and a real conversion."""

import json
import re
import threading
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import spandrel
import torch
import torch.nn.functional as F

from localsr.core import coreml_runtime
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
        window, top, left = _fixed_window(img, tile, 24)
        assert (top, left) == (4, 4)
        torch.testing.assert_close(window, regular_input(img, tile), rtol=0, atol=0)


def test_fixed_window_fills_edge_tiles_with_real_pixels():
    img = image(30, 40)
    for tile in generate_tiles(40, 30, 16, 4, 2):
        window, top, left = _fixed_window(img, tile, 32)
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
    window, top, left = _fixed_window(img, tile, 32)
    assert window.shape == (3, 32, 32) and (top, left) == (4, 4)
    torch.testing.assert_close(window[:, 4:14, 4:16], img, rtol=0, atol=0)
    # LocalSR's reflected halo at the border, then replicated fill.
    torch.testing.assert_close(window[:, 4:14, 0:4], img[:, :, 1:5].flip(-1), rtol=0, atol=0)
    torch.testing.assert_close(window[:, 4:14, 31], window[:, 4:14, 19], rtol=0, atol=0)


def test_fixed_window_refuses_a_tile_larger_than_the_window():
    tile = next(generate_tiles(64, 64, 32, 8, 1))
    assert _fixed_window(image(64, 64), tile, 40) is None


class FixedShapeModel:
    """A pointwise x2 model that only accepts one input shape."""

    def __init__(self, side):
        self.fixed_input_side = side
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


# Gate and fallback logic, with fake Core ML programs --------------------------


class FakeProgram:
    stores: dict = {}
    conversions = 0
    fail_conversion = False

    def __init__(self, root, architecture, identity, side):
        self.side = side
        self.key = (identity, side)
        self.compiled = SimpleNamespace(exists=lambda: True)
        self.verdicts = FakeProgram.stores.setdefault(self.key, {})
        self.compiled_ready = False

    def verdict(self, unit):
        return self.verdicts.get(unit)

    def store_verdict(self, unit, verdict):
        self.verdicts[unit] = verdict

    def ensure_compiled(self, source, input_channels):
        FakeProgram.conversions += 1
        if FakeProgram.fail_conversion:
            raise RuntimeError("unsupported op")


class FakeRunner:
    def __init__(self, unit, behaviour, reference):
        self.unit, self.behaviour, self.reference, self.calls = unit, behaviour, reference, 0

    def predict(self, inputs):
        self.calls += 1
        if self.behaviour == "raise":
            raise RuntimeError("ANE error")
        output = self.reference(torch.from_numpy(inputs["image"])).numpy()
        return {"restored": output if self.behaviour == "match" else np.zeros_like(output)}


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
    original = tiny_descriptor()

    def reference(image):
        with torch.no_grad():
            return original.model(image)

    def load(path, unit, timeout):
        behaviour = behaviours.get(unit, "match")
        if behaviour == "timeout":
            raise TimeoutError("compile timeout")
        runner = FakeRunner(unit, behaviour, reference)
        runners.append(runner)
        return runner

    class Compiled:
        def __truediv__(self, _name):
            return SimpleNamespace(exists=lambda: True)

    def program(root, architecture, identity, side):
        value = FakeProgram(root, architecture, identity, side)
        value.compiled = Compiled()
        return value

    monkeypatch.setattr(coreml_runtime, "_Program", program)
    monkeypatch.setattr(coreml_runtime, "_load_compiled", load)
    monkeypatch.setattr(
        coreml_runtime._CoreMLImage,
        "_time",
        staticmethod(lambda function, synchronize=None, repeats=2: 1.0),
    )
    return SimpleNamespace(behaviours=behaviours, runners=runners, original=original)


def run(descriptor, side_tile=16, halo=4):
    descriptor.prepare_tiles(side_tile, halo)
    return descriptor(image(20, 20)[None])


def padded_reference(original, sample, side):
    """What a fixed-shape program computes when called directly on a smaller input."""
    height, width = sample.shape[-2:]
    padded = F.pad(sample, (0, side - width, 0, side - height), mode="replicate")
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
    assert converted.fixed_input_side == 32
    verdict = FakeProgram.stores[("abc", 32)][NEURAL_ENGINE]
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
    verdicts = FakeProgram.stores[("def", 32)]
    assert not verdicts[NEURAL_ENGINE]["accepted"] and not verdicts[CORE_ML_GPU]["accepted"]
    assert converted.model.active_unit is None
    # Once ruled out, tiles go back to LocalSR's regular grid and the GPU.
    assert converted.fixed_input_side is None and converted.input_device is None


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
    verdict = FakeProgram.stores[("jk", 32)][NEURAL_ENGINE]
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
    assert set(FakeProgram.stores[("lm", 32)]) == {NEURAL_ENGINE, CORE_ML_GPU}


def test_runtime_errors_and_compile_timeouts_fall_back(fake_coreml):
    fake_coreml.behaviours.update({NEURAL_ENGINE: "timeout", CORE_ML_GPU: "raise"})
    converted = CoreMLImageDescriptor(
        fake_coreml.original, units=(NEURAL_ENGINE, CORE_ML_GPU), identity="no"
    )
    output = run(converted)
    torch.testing.assert_close(output, fake_coreml.original(image(20, 20)[None]))
    verdicts = FakeProgram.stores[("no", 32)]
    assert "compile timeout" in verdicts[NEURAL_ENGINE]["reason"]
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
    assert converted.fixed_input_side is None
    torch.testing.assert_close(run(converted), fake_coreml.original(image(20, 20)[None]))
    assert fake_coreml.runners == []


def test_realesrgan_keeps_pytorch_above_its_measured_size(fake_coreml):
    converted = CoreMLImageDescriptor(
        tiny_descriptor("ESRGAN"), units=(NEURAL_ENGINE,), identity="big"
    )
    converted.prepare_tiles(512, 32)  # 576 px
    assert converted.fixed_input_side is None
    converted = CoreMLImageDescriptor(
        tiny_descriptor("ESRGAN"), units=(NEURAL_ENGINE,), identity="ok"
    )
    converted.prepare_tiles(512, 16)  # 544 px
    assert converted.fixed_input_side == 544


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
    assert converted.model.active_unit in {NEURAL_ENGINE, CORE_ML_GPU}
