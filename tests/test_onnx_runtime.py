"""Exercise actual graph conversion and the descriptor boundary on CPU in CI."""

import json
from types import SimpleNamespace

import numpy as np
import pytest
import spandrel
import torch

from localsr.core.inference import InferenceEngine
from localsr.core.onnx_runtime import OnnxImageDescriptor, OnnxRoute

pytest.importorskip("onnx")
pytest.importorskip("onnxruntime")


def descriptor():
    class AuxiliaryNetwork(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.conv = torch.nn.Conv2d(3, 3, 1)
            with torch.no_grad():
                self.conv.weight.zero_()
                for c in range(3):
                    self.conv.weight[c, c, 0, 0] = 2
                self.conv.bias.fill_(-0.5)

        def forward(self, image):
            return self.conv(image), image.mean()

    model = AuxiliaryNetwork()
    return spandrel.ImageModelDescriptor(
        model,
        state_dict=model.state_dict(),
        architecture=SimpleNamespace(name="test"),
        purpose="Restoration",
        tags=[],
        supports_half=False,
        supports_bfloat16=False,
        scale=1,
        input_channels=3,
        output_channels=3,
        size_requirements=spandrel.SizeRequirements(minimum=8, multiple_of=8),
        call_fn=lambda model, image: model(image)[0],
    )


def test_conversion_preserves_padding_auxiliary_output_raw_validation_and_range():
    original = descriptor()
    converted = OnnxImageDescriptor(original, provider="CPUExecutionProvider")
    observations = []
    hook = converted.model.register_forward_hook(
        lambda model, args, output: observations.append(
            (tuple(args[0].shape), float(output.min()), float(output.max()))
        )
    )
    image = torch.linspace(0, 1, 3 * 5 * 7).reshape(1, 3, 5, 7)
    actual = converted(image)
    hook.remove()
    np.testing.assert_allclose(actual.numpy(), original(image).numpy(), atol=1e-7)
    assert actual.shape == (1, 3, 5, 7)
    assert observations == [((1, 3, 8, 8), -0.5, 1.5)]
    assert converted.execution_verified
    assert original.model is not converted.model


def test_shape_cache_is_bounded_and_cancellation_does_not_poison_the_next_call():
    import threading

    converted = OnnxImageDescriptor(descriptor(), provider="CPUExecutionProvider")
    for side in (8, 16, 24, 32, 40):
        converted(torch.zeros(1, 3, side, side))
    # An image's four tile shapes stay; the oldest beyond them goes.
    assert list(converted.model.sessions) == [(1, 3, side, side) for side in (16, 24, 32, 40)]
    cancel = threading.Event()
    cancel.set()
    converted.set_cancel_event(cancel)
    with pytest.raises(InterruptedError, match="Cancelled"):
        converted(torch.zeros(1, 3, 24, 24))
    converted.set_cancel_event(None)
    assert converted(torch.zeros(1, 3, 24, 24)).shape == (1, 3, 24, 24)


def test_cpu_and_onnx_gpu_never_share_a_cached_model(monkeypatch):
    from localsr.core import onnx_runtime

    monkeypatch.setattr(onnx_runtime, "directml_available", lambda: True)
    calls = []

    class Adapter:
        def load(self, path, device, precision, **kwargs):
            route = kwargs.get("onnx_route")
            model = SimpleNamespace(execution_provider=route.provider if route else "cpu")
            calls.append((str(device), precision, kwargs))
            return model, None

    engine = InferenceEngine(Adapter())
    info = SimpleNamespace(half_supported=True)
    engine.load_model("model", "cpu", "fp32", info)
    cpu = engine.active_model
    engine.load_model("model", "directml:0", "fp16", info)
    gpu = engine.active_model
    assert gpu is not cpu
    assert engine.active_backend_id == "directml:0"
    engine.load_model("model", "directml:0", "fp16", info)
    assert engine.active_model is gpu
    engine.load_model("model", "cpu", "fp32", info)
    assert engine.active_model is not gpu
    assert calls == [
        ("cpu", torch.float32, {}),
        (
            "cpu",
            torch.float32,
            {
                "onnx_route": OnnxRoute(
                    "DmlExecutionProvider",
                    0,
                    identity="directml:0",
                    label="directml:0",
                    persist=False,  # no adapter details: verdicts only for this process
                )
            },
        ),
        ("cpu", torch.float32, {}),
    ]
    engine.release_all()
    assert engine.active_backend_id is None


def test_packaged_directml_probe_checks_runtime_without_claiming_hardware(monkeypatch):
    import onnxruntime as ort

    from localsr.core.backend_validation import probe

    monkeypatch.setattr(ort, "get_available_providers", lambda: ["CPUExecutionProvider"])
    with pytest.raises(RuntimeError, match="lacks its ONNX execution provider"):
        probe("directml")
    monkeypatch.setattr(
        ort, "get_available_providers", lambda: ["DmlExecutionProvider", "CPUExecutionProvider"]
    )
    result = probe("directml")
    assert result["runtime_verified"]
    assert result["cpu_inference_verified"]
    assert not result["hardware_tested"]


def test_native_onnx_allocation_failure_releases_sessions_and_allows_retry(monkeypatch):
    from onnxruntime.capi.onnxruntime_pybind11_state import RuntimeException

    converted = OnnxImageDescriptor(descriptor(), provider="CPUExecutionProvider")
    image = torch.zeros(1, 3, 8, 8)
    converted(image)
    session = converted.model._session

    def out_of_memory(_image):
        raise RuntimeException("DirectML allocation failed: E_OUTOFMEMORY (0x8007000e)")

    monkeypatch.setattr(converted.model, "_session", out_of_memory)
    with pytest.raises(RuntimeError, match="Out of memory in the ONNX"):
        converted(image)
    assert not converted.model.sessions
    assert not converted.execution_verified
    monkeypatch.setattr(converted.model, "_session", session)
    assert converted(image).shape == image.shape
    assert converted.execution_verified


def active_config(converted):
    (state,) = converted.model.sessions.values()
    return state["config"][0]


def gated(monkeypatch, tmp_path, configs, broken=frozenset(), failing=frozenset()):
    """A CPU route that behaves like a GPU route under test.

    Configurations named in ``broken`` return a visibly different image and those in
    ``failing`` raise on their first run, so the gate's decisions can be checked
    without a GPU. Returns the descriptor and a list of reference (CPU) calls.
    """
    from localsr.core import onnx_runtime

    monkeypatch.setenv("LOCALSR_ONNX_GATE_CACHE", str(tmp_path))
    monkeypatch.setitem(
        onnx_runtime.ROUTE_CONFIGS,
        "CPUExecutionProvider",
        tuple((name, {}, False) for name in configs),
    )
    monkeypatch.setattr(onnx_runtime._OnnxImage, "_gated", property(lambda self: True))
    run = onnx_runtime._OnnxImage._run

    def run_configured(self, session, image):
        name = self._current["config"][0]  # recorded by track() below
        if name in failing:
            raise RuntimeError(f"configuration {name} crashed")
        output = run(self, session, image)
        return output + 0.25 if name in broken else output

    open_next = onnx_runtime._OnnxImage._open_next

    def track(self, state):
        open_next(self, state)
        self._current = state

    reference = onnx_runtime._OnnxImage._reference
    calls = []

    def counted(self, image):
        calls.append(tuple(image.shape))
        return reference(self, image)

    monkeypatch.setattr(onnx_runtime._OnnxImage, "_run", run_configured)
    monkeypatch.setattr(onnx_runtime._OnnxImage, "_open_next", track)
    monkeypatch.setattr(onnx_runtime._OnnxImage, "_reference", counted)
    return OnnxImageDescriptor(descriptor(), provider="CPUExecutionProvider"), calls


def test_gate_rejects_a_configuration_that_differs_and_uses_the_next(monkeypatch, tmp_path):
    original = descriptor()
    converted, calls = gated(monkeypatch, tmp_path, ("fast", "safe"), broken={"fast"})
    image = torch.linspace(0, 1, 3 * 8 * 8).reshape(1, 3, 8, 8)
    np.testing.assert_allclose(converted(image).numpy(), original(image).numpy(), atol=1e-6)
    assert active_config(converted) == "safe"
    assert len(calls) == 4  # pattern and tile, for each configuration tried
    verdicts = json.loads((tmp_path / "verdicts.json").read_text())
    assert sorted(v["accepted"] for v in verdicts.values()) == [False, True]
    # A new engine reuses both verdicts: no checks, straight to the accepted configuration.
    again, calls = gated(monkeypatch, tmp_path, ("fast", "safe"), broken={"fast"})
    np.testing.assert_allclose(again(image).numpy(), original(image).numpy(), atol=1e-6)
    assert active_config(again) == "safe" and calls == []


def test_gate_refuses_the_route_when_no_configuration_matches(monkeypatch, tmp_path):
    from localsr.core.onnx_runtime import GpuRouteRejected

    converted, calls = gated(monkeypatch, tmp_path, ("a", "b"), broken={"a", "b"})
    image = torch.rand(1, 3, 8, 8)
    with pytest.raises(GpuRouteRejected, match="Select the CPU"):
        converted(image)
    assert len(calls) == 4
    again, calls = gated(monkeypatch, tmp_path, ("a", "b"), broken={"a", "b"})
    with pytest.raises(GpuRouteRejected):
        again(image)
    assert calls == []  # refused from the stored verdicts, without running anything


def test_gate_moves_on_when_a_configuration_crashes_on_its_first_tile(monkeypatch, tmp_path):
    original = descriptor()
    converted, _ = gated(monkeypatch, tmp_path, ("crashes", "works"), failing={"crashes"})
    image = torch.rand(1, 3, 8, 8)
    np.testing.assert_allclose(converted(image).numpy(), original(image).numpy(), atol=1e-6)
    assert active_config(converted) == "works"


def test_gate_keeps_checking_while_the_reference_is_unstable(monkeypatch, tmp_path):
    from localsr.core import onnx_runtime

    converted, _ = gated(monkeypatch, tmp_path, ("only",))
    unstable = {"left": 2}  # both inputs of the first check: the pattern and the tile
    reference = onnx_runtime._OnnxImage._reference

    def sometimes_unstable(self, image):
        output = reference(self, image)
        if unstable["left"]:
            unstable["left"] -= 1
            output = output.copy()
            output.flat[0] = np.nan
        return output

    monkeypatch.setattr(onnx_runtime._OnnxImage, "_reference", sometimes_unstable)
    image = torch.rand(1, 3, 8, 8)
    converted(image)
    assert not (tmp_path / "verdicts.json").exists()  # nothing decided on an unstable tile
    converted(image)
    assert json.loads((tmp_path / "verdicts.json").read_text())


def test_a_lost_device_is_reported_as_such_and_releases_sessions(monkeypatch):
    from onnxruntime.capi.onnxruntime_pybind11_state import RuntimeException

    from localsr.core.onnx_runtime import GpuDeviceLost

    converted = OnnxImageDescriptor(descriptor(), provider="CPUExecutionProvider")
    image = torch.zeros(1, 3, 8, 8)
    converted(image)

    def removed(_image):
        raise RuntimeException("DML failed: DXGI_ERROR_DEVICE_REMOVED (0x887A0005)")

    monkeypatch.setattr(converted.model, "_session", removed)
    with pytest.raises(GpuDeviceLost, match="restarts with a fresh device"):
        converted(image)
    assert not converted.model.sessions


def test_onnx_tiles_like_the_cpu_and_reuses_every_shape_for_the_next_image(tmp_path, monkeypatch):
    import threading

    from localsr.core import onnx_runtime

    original = descriptor()
    converted = OnnxImageDescriptor(original, provider="CPUExecutionProvider")
    exports = []
    export = onnx_runtime._OnnxImage._export
    monkeypatch.setattr(
        onnx_runtime._OnnxImage,
        "_export",
        lambda self, image: exports.append(tuple(image.shape)) or export(self, image),
    )
    info = SimpleNamespace(
        half_supported=False,
        safe_memory=True,
        architecture="test",
        scale=1,
        out_channels=3,
        size_requirements_min=8,
        size_requirements_mult=8,
    )
    image = torch.rand(3, 110, 150)

    def render(model):
        class Adapter:
            def load(self, path, device, precision, **kwargs):
                return model, None

        writer = InferenceEngine(Adapter()).process_image(
            {"tensor": image},
            info,
            "model",
            "cpu",
            "fp32",
            64,
            8,
            threading.Event(),
            lambda *args: None,
            temporary_directory=str(tmp_path),
        )
        return writer.get_array().copy()

    onnx = render(converted)
    first = len(exports)
    assert 1 < first <= 4  # interior, right edge, bottom edge and corner tiles
    np.testing.assert_array_equal(render(converted), onnx)
    assert len(exports) == first  # the second image reuses every shape
    np.testing.assert_allclose(onnx, render(original), atol=1)  # the same tiling as the CPU


def test_a_flat_first_tile_cannot_pass_a_broken_configuration(monkeypatch, tmp_path):
    from localsr.core import onnx_runtime

    converted, _ = gated(monkeypatch, tmp_path, ("detail-broken", "sound"))
    run = onnx_runtime._OnnxImage._run

    def wrong_on_detail(self, session, image):
        # Correct on flat input, wrong wherever the image has detail.
        output = run(self, session, image)
        if self._current["config"][0] == "detail-broken" and float(image.std()) > 0.01:
            output = output + 0.3
        return output

    monkeypatch.setattr(onnx_runtime._OnnxImage, "_run", wrong_on_detail)
    statuses = []
    converted.set_status_callback(statuses.append)
    flat = torch.full((1, 3, 8, 8), 0.5)
    np.testing.assert_allclose(converted(flat).numpy(), descriptor()(flat).numpy(), atol=1e-6)
    # A flat tile decides nothing: it ran on the CPU, the user is told, the check goes on.
    assert not (tmp_path / "verdicts.json").exists()
    assert any("cannot show whether" in status for status in statuses)
    assert not converted.execution_verified
    converted(torch.rand(1, 3, 8, 8))
    assert active_config(converted) == "sound"


def test_worker_restarts_only_after_a_lost_device():
    from localsr.core.onnx_runtime import GpuDeviceLost, GpuRouteRejected
    from localsr.worker.server import _device_lost

    try:
        try:
            raise GpuDeviceLost("device removed")
        except GpuDeviceLost as lost:
            raise RuntimeError("tile failed") from lost
    except RuntimeError as wrapped:
        assert _device_lost(wrapped)
    assert not _device_lost(GpuRouteRejected("different results"))
    assert not _device_lost(RuntimeError("Out of memory in the ONNX inference engine."))


def test_a_large_model_keeps_two_sessions(monkeypatch):
    from localsr.core import onnx_runtime

    monkeypatch.setattr(onnx_runtime, "_LARGE_GRAPH_BYTES", 10)
    converted = OnnxImageDescriptor(descriptor(), provider="CPUExecutionProvider")
    for side in (8, 16, 24):
        converted(torch.zeros(1, 3, side, side))
    assert list(converted.model.sessions) == [(1, 3, 16, 16), (1, 3, 24, 24)]


def test_placement_counts_what_a_gpu_route_leaves_on_the_cpu():
    from localsr.core.onnx_runtime import _placement

    def subgraph(provider, *operations):
        nodes = [SimpleNamespace(op_type=operation) for operation in operations]
        return SimpleNamespace(ep_name=provider, get_nodes=lambda: nodes)

    session = SimpleNamespace(
        get_provider_graph_assignment_info=lambda: [
            subgraph("DmlExecutionProvider", "Conv", "LeakyRelu", "Conv"),
            subgraph("CPUExecutionProvider", "Shape", "Gather"),
            subgraph("CPUExecutionProvider", "MatMul"),
        ]
    )
    assert _placement(session, "DmlExecutionProvider") == {
        "nodes": 6,
        "cpu_nodes": 3,
        "cpu_subgraphs": 2,
        "cpu_heavy": ["MatMul"],
    }
    assert _placement(SimpleNamespace(), "DmlExecutionProvider") is None  # older ONNX Runtime


def test_out_of_memory_while_opening_is_not_a_verdict(monkeypatch, tmp_path):
    from localsr.core import onnx_runtime

    converted, _ = gated(monkeypatch, tmp_path, ("a", "b"))

    def no_memory(self, graph, config):
        raise RuntimeError("Failed to allocate memory for the graph")

    monkeypatch.setattr(onnx_runtime._OnnxImage, "_create", no_memory)
    with pytest.raises(RuntimeError, match="Out of memory in the ONNX"):
        converted(torch.rand(1, 3, 8, 8))
    assert not (tmp_path / "verdicts.json").exists()  # smaller tiles may still work


def test_a_lost_device_wins_over_a_cancellation_at_the_same_moment(monkeypatch):
    import threading

    from onnxruntime.capi.onnxruntime_pybind11_state import RuntimeException

    from localsr.core.onnx_runtime import GpuDeviceLost

    converted = OnnxImageDescriptor(descriptor(), provider="CPUExecutionProvider")
    image = torch.zeros(1, 3, 8, 8)
    converted(image)
    cancel = threading.Event()
    converted.set_cancel_event(cancel)

    def removed(_image):
        cancel.set()
        raise RuntimeException("DXGI_ERROR_DEVICE_REMOVED (0x887A0005)")

    monkeypatch.setattr(converted.model, "_session", removed)
    with pytest.raises(GpuDeviceLost):
        converted(image)
    assert not converted.model.sessions


def test_an_accepted_configuration_that_crashes_falls_back(monkeypatch, tmp_path):

    original = descriptor()
    image = torch.rand(1, 3, 8, 8)
    first, _ = gated(monkeypatch, tmp_path, ("default", "basic"))
    first(image)
    assert active_config(first) == "default"
    # The next day the accepted configuration crashes on its first tile.
    again, _ = gated(monkeypatch, tmp_path, ("default", "basic"), failing={"default"})
    np.testing.assert_allclose(again(image).numpy(), original(image).numpy(), atol=1e-6)
    assert active_config(again) == "basic"
    state = next(iter(again.model.sessions.values()))
    assert "graph" not in state  # the serialized graph goes once a configuration works


def test_verdicts_without_a_known_driver_last_only_for_this_process(tmp_path):
    from localsr.core.onnx_runtime import _Verdicts

    stored, temporary = _Verdicts(tmp_path), _Verdicts(tmp_path, persist=False)
    temporary.put("key", {"accepted": True})
    assert temporary.get("key")["accepted"]
    assert not (tmp_path / "verdicts.json").exists()
    stored.put("key", {"accepted": True})
    assert _Verdicts(tmp_path).get("key")["accepted"]


def test_openvino_luid_finds_the_dxgi_adapter_and_its_driver(monkeypatch):
    from localsr.core import openvino_devices, windows_adapters

    # OpenVINO writes the LUID's eight bytes in memory order: LowPart, then HighPart.
    monkeypatch.setattr(
        windows_adapters,
        "directml_adapters",
        lambda: [
            {"luid": "00000000000000aa", "driver_version": "1.0.0.0"},
            {"luid": "00005563264fcc20", "driver_version": "32.0.101.6987"},
        ],
    )
    assert openvino_devices._windows_driver("20cc4f2663550000") == "32.0.101.6987"
    assert openvino_devices._windows_driver("not hex") is None
