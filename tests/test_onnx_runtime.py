"""Exercise actual graph conversion and the descriptor boundary on CPU in CI."""

import json
import os
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import numpy as np
import pytest
import spandrel
import torch

from localsr.core.inference import InferenceEngine
from localsr.core.onnx_runtime import OnnxImageDescriptor, OnnxRoute

if os.environ.get("CI"):
    # CI installs both on purpose; a missing one must fail, not skip the GPU check's tests.
    import onnx  # noqa: F401
    import onnxruntime  # noqa: F401
else:
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
    from localsr.core import onnx_runtime, windows_adapters

    monkeypatch.setattr(onnx_runtime, "directml_available", lambda: True)
    # No adapter details, wherever the test runs (a Windows runner has a software one).
    monkeypatch.setattr(windows_adapters, "directml_adapters", lambda **_: [])
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
                    device="directml:0",
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


def gated(
    monkeypatch,
    tmp_path,
    configs,
    broken=frozenset(),
    failing=frozenset(),
    device="",
    identity=None,
    persist=True,
    slight=frozenset(),
    kind="GPU",
):
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
    monkeypatch.setattr(
        onnx_runtime, "NPU_ROUTE_CONFIGS", tuple((name, {}, False) for name in configs)
    )
    monkeypatch.setattr(onnx_runtime._OnnxImage, "_gated", property(lambda self: True))
    run = onnx_runtime._OnnxImage._run

    def run_configured(self, session, image):
        name = self._current["config"][0]  # recorded by track() below
        if name in failing:
            raise RuntimeError(f"configuration {name} crashed")
        output = run(self, session, image)
        if name in slight:
            return output + 0.0045  # about 47 dB: what FP16 arithmetic leaves
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
    route = OnnxRoute(
        provider="CPUExecutionProvider",
        device=device,
        identity=device if identity is None else identity,
        persist=persist,
        kind=kind,
    )
    return OnnxImageDescriptor(descriptor(), route=route), calls


def test_gate_rejects_a_configuration_that_differs_and_uses_the_next(monkeypatch, tmp_path):
    original = descriptor()
    converted, calls = gated(monkeypatch, tmp_path, ("fast", "safe"), broken={"fast"})
    image = torch.linspace(0, 1, 3 * 8 * 8).reshape(1, 3, 8, 8)
    np.testing.assert_allclose(converted(image).numpy(), original(image).numpy(), atol=1e-6)
    assert active_config(converted) == "safe"
    # "fast" fails on the test pattern and is rejected without its tile; "safe" needs both.
    assert len(calls) == 3
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
    with pytest.raises(GpuRouteRejected, match="gives different results on .* Select the CPU"):
        converted(image)
    assert len(calls) == 2  # each configuration fails on the test pattern
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
    seen = {"calls": 0}
    reference = onnx_runtime._OnnxImage._reference

    def sometimes_unstable(self, image):
        # The first check's second CPU run, the real tile, is unstable; the pattern is not.
        seen["calls"] += 1
        output = reference(self, image)
        if seen["calls"] == 2:
            output = output.copy()
            output.flat[0] = np.nan
        return output

    monkeypatch.setattr(onnx_runtime._OnnxImage, "_reference", sometimes_unstable)
    image = torch.rand(1, 3, 8, 8)
    converted(image)
    assert not (tmp_path / "verdicts.json").exists()  # nothing decided on an unstable tile
    converted(image)
    assert json.loads((tmp_path / "verdicts.json").read_text())


def test_a_lost_device_is_reported_without_touching_its_sessions(monkeypatch):
    from onnxruntime.capi.onnxruntime_pybind11_state import RuntimeException

    from localsr.core.onnx_runtime import GpuDeviceLost

    converted = OnnxImageDescriptor(descriptor(), provider="CPUExecutionProvider")
    image = torch.zeros(1, 3, 8, 8)
    converted(image)

    def removed(_image):
        raise RuntimeException("DML failed: DXGI_ERROR_DEVICE_REMOVED (0x887A0005)")

    monkeypatch.setattr(converted.model, "_session", removed)
    sessions = dict(converted.model.sessions)
    with pytest.raises(GpuDeviceLost, match="restarts with a fresh device.*0x887A0005"):
        converted(image)
    # Freeing a lost device's sessions can hang; the worker exits instead.
    assert dict(converted.model.sessions) == sessions
    assert not converted.execution_verified


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
    assert any("too little detail" in status for status in statuses)
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
    assert not converted.execution_verified


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

    calls = []
    # OpenVINO writes the LUID's eight bytes in memory order: LowPart, then HighPart.
    monkeypatch.setattr(
        windows_adapters,
        "directml_adapters",
        lambda **options: (
            calls.append(options)
            or [
                {"luid": "00000000000000aa", "driver_version": "1.0.0.0"},
                {"luid": "00005563264fcc20", "driver_version": "32.0.101.6987"},
            ]
        ),
    )
    assert openvino_devices._windows_driver("20cc4f2663550000") == "32.0.101.6987"
    # A lookup creates no Direct3D device, which would wake a sleeping laptop GPU.
    assert calls == [{"probe": False}]
    assert openvino_devices._windows_driver("not hex") is None


def test_backend_probe_requires_a_bundled_openvino_plugin_to_load(monkeypatch):
    import importlib.util

    from localsr.core import backend_validation, openvino_devices

    result = {}
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    backend_validation._check_openvino_plugin(result)
    assert result == {}  # editions without the plugin are not affected
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(openvino_devices, "plugin_registered", lambda: False)
    with pytest.raises(RuntimeError, match="could not be registered"):
        backend_validation._check_openvino_plugin(result)

    import onnxruntime as ort

    def device(kind):
        return SimpleNamespace(
            ep_name="OpenVINOExecutionProvider",
            device=SimpleNamespace(type=f"OrtHardwareDeviceType.{kind}"),
        )

    monkeypatch.setattr(openvino_devices, "plugin_registered", lambda: True)
    monkeypatch.setattr(ort, "get_ep_devices", lambda: [device("CPU")], raising=False)
    backend_validation._check_openvino_plugin(result)  # no GPU on a build runner is fine
    assert result["openvino_devices"] == ["CPU"]
    monkeypatch.setattr(ort, "get_ep_devices", lambda: [], raising=False)
    backend_validation._check_openvino_plugin(result)  # no device on this machine: recorded
    assert result["openvino_devices"] == []


def test_linux_driver_identity_is_intels_gpu_runtime_and_its_compiler(tmp_path, monkeypatch):
    from localsr.core import openvino_devices

    vendors = tmp_path / "vendors"
    libraries = tmp_path / "lib64"
    vendors.mkdir()
    (libraries / "intel-opencl").mkdir(parents=True)
    # Intel's CPU OpenCL driver sorts first and must not stand in for the GPU driver.
    (libraries / "libintelocl.so").write_bytes(b"cpu")
    (vendors / "intel-cpu.icd").write_text(f"{libraries / 'libintelocl.so'}\n")
    runtime = libraries / "intel-opencl" / "libigdrcl.so"
    runtime.write_bytes(b"gpu runtime")
    (vendors / "intel.icd").write_text(f"{runtime}\n")
    monkeypatch.setattr(openvino_devices, "_OPENCL_VENDORS", vendors)
    monkeypatch.setattr(openvino_devices, "_LINUX_LIBRARY_DIRECTORIES", (str(libraries),))
    assert openvino_devices._linux_driver() is None  # no compiler found: no identity

    compiler = libraries / "libigc.so.2.41.15+0"
    compiler.write_bytes(b"compiler")
    (libraries / "libigc.so.2").symlink_to(compiler.name)
    (libraries / "libigdfcl.so.2.41.15+0").write_bytes(b"frontend")
    identity = openvino_devices._linux_driver()
    assert identity.startswith("libigdrcl.so:11:")
    assert "libigc.so.2.41.15+0:8:" in identity and "libintelocl" not in identity

    compiler.write_bytes(b"updated compiler")
    assert openvino_devices._linux_driver() != identity  # a compiler update counts

    # With a second compiler version installed, which one the driver loads is unknown.
    (libraries / "libigc.so.1.0.17").write_bytes(b"older compiler")
    assert openvino_devices._linux_driver() is None


def test_identical_directml_cards_keep_verdicts_only_for_this_process(monkeypatch):
    from localsr.core import inference, onnx_runtime, windows_adapters

    monkeypatch.setattr(onnx_runtime, "directml_available", lambda: True)
    card = {
        "name": "Radeon RX 7600",
        "vendor_id": 0x1002,
        "device_id": 0x7480,
        "subsystem_id": 0x12341002,
        "driver_version": "31.0.24027.1012",
    }
    monkeypatch.setattr(
        windows_adapters,
        "directml_adapters",
        lambda **_: [{**card, "index": 0}, {**card, "index": 1}],
    )
    assert inference._onnx_route("directml:1").persist is False
    monkeypatch.setattr(windows_adapters, "directml_adapters", lambda **_: [{**card, "index": 0}])
    assert inference._onnx_route("directml:0").persist is True


def test_route_labels_name_the_gpu_as_the_hardware_menu_does(monkeypatch):
    from localsr.core import inference, onnx_runtime, openvino_devices, windows_adapters

    monkeypatch.setattr(onnx_runtime, "directml_available", lambda: True)
    adapter = {
        "index": 0,
        "name": "Intel(R) UHD Graphics 620",
        "vendor_id": 0x8086,
        "device_id": 0x5917,
        "subsystem_id": 0,
        "driver_version": "31.0.101.2125",
    }
    monkeypatch.setattr(windows_adapters, "directml_adapters", lambda **_: [adapter])
    route = inference._onnx_route("directml:0")
    assert route.label == "Intel UHD Graphics 620 (DirectML)"
    # Stored verdicts and crash notes keep the driver's own name.
    assert route.identity.startswith("Intel(R) UHD Graphics 620|")

    entry = {
        "key": "8680677d060000000002000000000000",
        "kind": "GPU",
        "name": "Intel(R) UHD Graphics 620 (iGPU)",
        "device_id": "0x5917",
        "architecture": "GPU: vendor=0x8086 arch=v9.0.9",
        "plugin_version": "1.8.1",
        "driver": "31.0.101.2125",
        "ep_device": object(),
    }
    monkeypatch.setattr(openvino_devices, "device_for", lambda key: entry)
    route = inference._onnx_route("openvino:8680677d060000000002000000000000")
    assert route.label == "Intel UHD Graphics 620 (OpenVINO)"
    assert "Intel(R) UHD Graphics 620 (iGPU)" in route.identity


def test_a_model_unstable_on_both_test_patterns_runs_on_the_cpu(monkeypatch, tmp_path):
    from localsr.core import onnx_runtime

    original = descriptor()
    converted, _ = gated(monkeypatch, tmp_path, ("only",))
    reference = onnx_runtime._OnnxImage._reference
    patterns = {
        tuple(onnx_runtime._test_pattern((1, 3, 8, 8), smooth=smooth).flatten()[:5].tolist())
        for smooth in (False, True)
    }

    def unstable_on_patterns(self, image):
        output = reference(self, image)
        if tuple(image.flatten()[:5].tolist()) in patterns:
            output = output.copy()
            output.flat[0] = np.nan
        return output

    gpu_runs = []
    run = onnx_runtime._OnnxImage._run

    def counted_run(self, session, image):
        gpu_runs.append(1)
        return run(self, session, image)

    monkeypatch.setattr(onnx_runtime._OnnxImage, "_reference", unstable_on_patterns)
    monkeypatch.setattr(onnx_runtime._OnnxImage, "_run", counted_run)
    statuses = []
    converted.set_status_callback(statuses.append)
    image = torch.rand(1, 3, 8, 8)
    np.testing.assert_allclose(converted(image).numpy(), original(image).numpy(), atol=1e-6)
    first = len(gpu_runs)
    np.testing.assert_allclose(converted(image).numpy(), original(image).numpy(), atol=1e-6)
    assert len(gpu_runs) == first  # later tiles of the shape do not touch the GPU
    assert not (tmp_path / "verdicts.json").exists()
    assert sum("cannot check" in status for status in statuses) == 1
    assert not converted.execution_verified
    # The shape no longer holds a GPU session or its graph.
    (state,) = converted.model.sessions.values()
    assert "session" not in state and "graph" not in state


def test_a_cancelled_switch_to_the_next_configuration_is_tried_again(monkeypatch, tmp_path):
    from localsr.core import onnx_runtime

    original = descriptor()
    converted, _ = gated(monkeypatch, tmp_path, ("broken", "sound"), broken={"broken"})
    create = onnx_runtime._OnnxImage._create
    interrupted = []

    def cancelled_once(self, graph, config):
        if config[0] == "sound" and not interrupted:
            interrupted.append(config[0])
            raise InterruptedError("Cancelled")
        return create(self, graph, config)

    monkeypatch.setattr(onnx_runtime._OnnxImage, "_create", cancelled_once)
    # A flat tile caches the state while "broken" is still being checked; the next,
    # detailed tile rejects it, and the switch to "sound" is cancelled.
    converted(torch.full((1, 3, 8, 8), 0.5))
    assert converted.model.sessions
    image = torch.rand(1, 3, 8, 8)
    with pytest.raises(InterruptedError):
        converted(image)
    assert not converted.model.sessions  # the half-switched state is not kept
    np.testing.assert_allclose(converted(image).numpy(), original(image).numpy(), atol=1e-6)
    assert active_config(converted) == "sound"


def test_the_benchmark_flag_needs_every_tile_on_the_checked_gpu(monkeypatch, tmp_path):
    converted, _ = gated(monkeypatch, tmp_path, ("only",))
    converted(torch.rand(1, 3, 8, 8))
    assert converted.execution_verified
    converted.model.cpu_tiles = 1  # one tile of the run fell back to the CPU
    assert not converted.execution_verified
    # A benchmark starts counting afresh and keeps the checked sessions.
    sessions = dict(converted.model.sessions)
    converted.reset_execution_evidence()
    assert not converted.execution_verified and converted.model.cpu_tiles == 0
    converted(torch.rand(1, 3, 8, 8))
    assert converted.execution_verified
    assert dict(converted.model.sessions).keys() == sessions.keys()


def test_cancelling_during_the_check_stores_no_verdict(monkeypatch, tmp_path):
    import threading

    from onnxruntime.capi.onnxruntime_pybind11_state import Fail

    from localsr.core import onnx_runtime

    converted, _ = gated(monkeypatch, tmp_path, ("only",))
    cancel = threading.Event()
    converted.set_cancel_event(cancel)

    def terminated(self, session, image):
        cancel.set()  # the user cancels; ONNX Runtime stops the run with an ordinary error
        raise Fail(
            "[ONNXRuntimeError] : 1 : FAIL : Exiting due to terminate flag being set to true."
        )

    monkeypatch.setattr(onnx_runtime._OnnxImage, "_run", terminated)
    with pytest.raises(InterruptedError):
        converted(torch.rand(1, 3, 8, 8))
    assert not (tmp_path / "verdicts.json").exists()


def test_python_and_numpy_memory_errors_count_as_out_of_memory():
    from localsr.core.onnx_runtime import _is_memory_error

    class ArrayMemoryError(MemoryError):
        """Like NumPy's own subclass, which says "Unable to allocate ..."."""

    assert _is_memory_error(MemoryError())
    assert _is_memory_error(ArrayMemoryError("Unable to allocate 1.00 TiB for an array"))
    assert not _is_memory_error(ValueError("array is too big"))


def test_an_unreadable_profile_falls_back_to_the_graph_assignment(monkeypatch, tmp_path):
    from localsr.core import onnx_runtime

    original = descriptor()
    converted, _ = gated(monkeypatch, tmp_path, ("only",))
    monkeypatch.setattr(onnx_runtime._OnnxImage, "_close_profile", lambda self, state: None)
    image = torch.rand(1, 3, 8, 8)
    np.testing.assert_allclose(converted(image).numpy(), original(image).numpy(), atol=1e-6)
    verdicts = json.loads((tmp_path / "verdicts.json").read_text())
    assert [verdict["accepted"] for verdict in verdicts.values()] == [True]


def test_an_unreadable_driver_identity_keeps_the_gpu(monkeypatch):
    from localsr.core import openvino_devices

    def unreadable():
        raise PermissionError("/usr/lib64/libigc.so.2")

    monkeypatch.setattr(openvino_devices.sys, "platform", "linux")
    monkeypatch.setattr(openvino_devices, "_linux_driver", unreadable)
    assert openvino_devices._driver("") is None


def test_without_any_placement_evidence_nothing_is_decided(monkeypatch, tmp_path):
    from localsr.core import onnx_runtime

    original = descriptor()
    converted, _ = gated(monkeypatch, tmp_path, ("only",))
    close = onnx_runtime._OnnxImage._close_profile
    unreadable = {"left": 1}

    def first_profile_unreadable(self, state):
        if unreadable["left"]:
            unreadable["left"] -= 1
            state["session"].end_profiling()
            state.pop("trace").cleanup()
            return None
        return close(self, state)

    monkeypatch.setattr(onnx_runtime._OnnxImage, "_close_profile", first_profile_unreadable)
    monkeypatch.setattr(onnx_runtime, "_placement", lambda session, provider: None)
    image = torch.rand(1, 3, 8, 8)
    np.testing.assert_allclose(converted(image).numpy(), original(image).numpy(), atol=1e-6)
    # Nothing stored and nothing verified: the tile was the CPU's.
    assert not (tmp_path / "verdicts.json").exists()
    assert not converted.execution_verified
    # The configuration opened again with a fresh profile, which decides.
    np.testing.assert_allclose(converted(image).numpy(), original(image).numpy(), atol=1e-6)
    verdicts = json.loads((tmp_path / "verdicts.json").read_text())
    assert [verdict["accepted"] for verdict in verdicts.values()] == [True]


def test_a_profile_that_stays_unreadable_is_tried_once_more_then_the_cpu_runs(
    monkeypatch, tmp_path
):
    from localsr.core import onnx_runtime

    original = descriptor()
    converted, _ = gated(monkeypatch, tmp_path, ("only",))

    def unreadable(self, state):
        state["session"].end_profiling()
        state.pop("trace").cleanup()
        return None

    create = onnx_runtime._OnnxImage._create
    created = []

    def counted(self, graph, config):
        created.append(config[0])
        return create(self, graph, config)

    monkeypatch.setattr(onnx_runtime._OnnxImage, "_close_profile", unreadable)
    monkeypatch.setattr(onnx_runtime._OnnxImage, "_create", counted)
    monkeypatch.setattr(onnx_runtime, "_placement", lambda session, provider: None)
    image = torch.rand(1, 3, 8, 8)
    for _ in range(5):
        np.testing.assert_allclose(converted(image).numpy(), original(image).numpy(), atol=1e-6)
    assert len(created) == 2  # the first session and one fresh profile, not one per tile
    assert not (tmp_path / "verdicts.json").exists()
    assert not converted.execution_verified


def _recording_notes(monkeypatch, tmp_path):
    """Record how many GPU-call notes exist while sessions run and while the CPU works."""
    from localsr.core import onnx_runtime

    seen = {"gpu": [], "cpu": [], "create": []}

    class Session:
        def __init__(self, session):
            self.session = session

        def run(self, *args, **kwargs):
            seen["gpu"].append(len(list(tmp_path.glob("running-*.json"))))
            return self.session.run(*args, **kwargs)

        def __getattr__(self, name):
            return getattr(self.session, name)

    create = onnx_runtime._OnnxImage._create

    def recording_create(self, graph, config):
        seen["create"].append(len(list(tmp_path.glob("running-*.json"))))
        session, trace = create(self, graph, config)
        return Session(session), trace

    reference = onnx_runtime._OnnxImage._reference

    def recording_reference(self, image):
        seen["cpu"].append(len(list(tmp_path.glob("running-*.json"))))
        return reference(self, image)

    monkeypatch.setattr(onnx_runtime._OnnxImage, "_create", recording_create)
    monkeypatch.setattr(onnx_runtime._OnnxImage, "_reference", recording_reference)
    return seen


def test_a_gpu_that_crashed_the_engine_is_not_used_again(monkeypatch, tmp_path):
    from localsr.core import gpu_guard
    from localsr.core.onnx_runtime import GpuRouteRejected

    monkeypatch.setattr(gpu_guard, "_CRASHED", {})
    original = descriptor()
    image = torch.rand(1, 3, 8, 8)
    seen = _recording_notes(monkeypatch, tmp_path)
    converted, _ = gated(monkeypatch, tmp_path, ("a", "b"), broken={"a"})
    np.testing.assert_allclose(converted(image).numpy(), original(image).numpy(), atol=1e-6)
    checked_runs = len(seen["gpu"])
    # Later images on the accepted configuration are GPU calls like the first ones.
    converted(torch.rand(1, 3, 8, 8))
    assert len(seen["gpu"]) > checked_runs
    # A note exists exactly while the GPU works, never during the CPU's own work.
    assert seen["create"] == [1, 1]
    assert set(seen["gpu"]) == {1}
    assert seen["cpu"] and set(seen["cpu"]) == {0}
    assert list(tmp_path.glob("running-*.json")) == []
    key = converted.model._crash_key()
    notes = gpu_guard.CrashNotes(tmp_path)

    def died_during_a_gpu_call(pid, age=0.0):
        note = tmp_path / f"running-0123456789abcdef-{pid}.json"
        note.write_text(json.dumps({"key": key, "pid": pid}))
        os.utime(note, (time.time() - age, time.time() - age))
        return note

    # A recent note of another process may belong to a GPU call still running there.
    running = died_during_a_gpu_call(5151)
    gpu_guard.settle()
    assert running.exists() and notes.crashed(key) is None
    running.unlink()
    # An engine the app stopped itself (quit, cancel) left the note: no crash, removed.
    died_during_a_gpu_call(4242, age=2 * gpu_guard.STALE_NOTE_SECONDS)
    monkeypatch.setenv(gpu_guard.CRASHED_ENGINE_ENV, "999")
    gpu_guard.settle()
    assert list(tmp_path.glob("running-*.json")) == [] and notes.crashed(key) is None
    again, _ = gated(monkeypatch, tmp_path, ("a", "b"), broken={"a"})
    again(image)
    assert active_config(again) == "b"  # still on the GPU

    # The engine that died of a native fault left it: the GPU is refused from then on.
    died_during_a_gpu_call(4242)
    monkeypatch.setenv(gpu_guard.CRASHED_ENGINE_ENV, "4242")
    gpu_guard.settle()
    assert notes.crashed(key) == "engine"
    monkeypatch.setattr(gpu_guard, "_CRASHED", {})  # a new process reads the record
    refused, calls = gated(monkeypatch, tmp_path, ("a", "b"), broken={"a"})
    with pytest.raises(GpuRouteRejected, match="engine crashed on .* earlier"):
        refused(image)
    assert calls == []


def test_a_gpu_whose_preflight_process_crashed_is_refused(monkeypatch, tmp_path):
    from localsr.core import gpu_guard
    from localsr.core.onnx_runtime import GpuRouteRejected

    original = descriptor()
    image = torch.rand(1, 3, 8, 8)
    outcomes = []

    def preflight(device, cancel_event=None, status=None):
        outcomes.append(device)
        return "crashed" if device == "gpu:bad" else "passed"

    monkeypatch.setattr(gpu_guard, "run", preflight)
    monkeypatch.setattr(gpu_guard, "_PREFLIGHT_TRIED", set())
    monkeypatch.setattr(gpu_guard, "_CRASHED", {})
    real_write = gpu_guard._write_json

    def full_disk(path, value):
        if path.name.startswith("crash-"):
            raise OSError("disk full")
        real_write(path, value)

    monkeypatch.setattr(gpu_guard, "_write_json", full_disk)
    converted, calls = gated(monkeypatch, tmp_path, ("a",), device="gpu:bad")
    with pytest.raises(GpuRouteRejected, match="A test run on .* crashed"):
        converted(image)
    assert outcomes == ["gpu:bad"] and calls == []  # the engine itself ran nothing there
    # The record could not be stored, but this engine keeps refusing the GPU.
    again, calls = gated(monkeypatch, tmp_path, ("a",), device="gpu:bad")
    with pytest.raises(GpuRouteRejected):
        again(image)
    assert outcomes == ["gpu:bad"] and calls == []

    # An identical card (same identity) on another device gets its own preflight.
    twin, _ = gated(monkeypatch, tmp_path, ("a",), device="gpu:twin", identity="gpu:bad")
    twin(image)
    assert outcomes == ["gpu:bad", "gpu:twin"]

    # A GPU whose preflight finished runs, and later engines skip the preflight.
    converted, _ = gated(monkeypatch, tmp_path, ("a",), device="gpu:good")
    np.testing.assert_allclose(converted(image).numpy(), original(image).numpy(), atol=1e-6)
    monkeypatch.setattr(gpu_guard, "_PREFLIGHT_TRIED", set())
    again, _ = gated(monkeypatch, tmp_path, ("a",), device="gpu:good")
    again(image)
    assert outcomes == ["gpu:bad", "gpu:twin", "gpu:good"]


def test_the_preflight_counts_only_a_native_fault_during_a_gpu_call(monkeypatch):
    import threading

    from localsr.core import gpu_guard

    def child(code):
        monkeypatch.setattr(gpu_guard, "_command", lambda device: [sys.executable, "-c", code])

    note = (
        "import os, pathlib; pathlib.Path(os.environ['LOCALSR_ONNX_GATE_CACHE'], "
        "'running-0123456789abcdef.json').write_text('{}'); "
    )
    # A real native fault would make the system write a crash report for every test run;
    # SIGTERM stands in for one here (native_fault itself is tested on exit codes).
    stand_in = "import signal; os.kill(os.getpid(), signal.SIGTERM)"
    monkeypatch.setattr(
        gpu_guard, "native_fault", lambda code: code in (-signal.SIGTERM, signal.SIGTERM)
    )
    child(note + stand_in)
    assert gpu_guard.run("gpu:0") == "crashed"
    child("import os; " + stand_in)  # before any GPU call: startup, antivirus, imports
    assert gpu_guard.run("gpu:0") == "unknown"
    child(note + "raise SystemExit(1)")  # a plain failure, not a fault
    assert gpu_guard.run("gpu:0") == "unknown"
    if os.name != "nt":
        child(note + "import signal; os.kill(os.getpid(), signal.SIGKILL)")  # the OOM killer
        assert gpu_guard.run("gpu:0") == "unknown"
    child("raise SystemExit(0)")
    assert gpu_guard.run("gpu:0") == "passed"
    child(f"raise SystemExit({gpu_guard.UNTESTED})")
    assert gpu_guard.run("gpu:0") == "unknown"
    child("import time; time.sleep(30)")
    monkeypatch.setattr(gpu_guard, "TIMEOUT_SECONDS", 0.5)
    assert gpu_guard.run("gpu:0") == "timeout"
    cancelled = threading.Event()
    cancelled.set()
    child("raise SystemExit(0)")
    with pytest.raises(InterruptedError):
        gpu_guard.run("gpu:0", cancelled)
    # Cancelled at the moment the child finishes: the result is not used.
    cancelled = threading.Event()
    popen = subprocess.Popen

    class FinishingWhileCancelled(popen):
        def poll(self):
            code = super().poll()
            if code is not None:
                cancelled.set()
            return code

    monkeypatch.setattr(gpu_guard.subprocess, "Popen", FinishingWhileCancelled)
    with pytest.raises(InterruptedError):
        gpu_guard.run("gpu:0", cancelled)


def test_native_faults_are_told_apart_from_kills_and_failures():
    from localsr.core.gpu_guard import native_fault

    if os.name == "nt":
        faults, others = (0xC0000005, 0xC0000409, 3), (0, 1, 2, 75)
    else:
        faults = (-signal.SIGSEGV, -signal.SIGABRT, -signal.SIGBUS, -signal.SIGILL)
        others = (0, 1, 2, 75, -signal.SIGKILL, -signal.SIGTERM)
    assert all(native_fault(code) for code in faults)
    assert not any(native_fault(code) for code in others)
    assert not native_fault(None)


@pytest.mark.skipif(os.name == "nt", reason="on Windows the engine's job object ends it")
def test_the_preflight_child_ends_itself_when_it_hangs():
    from pathlib import Path

    # The child's own limit, for a call that blocks or a parent that is gone: here 1 s,
    # while its route lookup hangs for a minute.
    code = (
        "import time; from localsr.core import gpu_guard, inference; "
        "gpu_guard.TIMEOUT_SECONDS = -29; "
        "inference._onnx_route = lambda device: time.sleep(60); "
        "raise SystemExit(gpu_guard.child_main(['gpu:0']))"
    )
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    started = time.monotonic()
    child = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, timeout=120)
    assert child.returncode == -signal.SIGALRM and time.monotonic() - started < 50


@pytest.mark.skipif(os.name != "nt", reason="the job object is Windows only")
def test_a_killed_engine_takes_its_preflight_child_along(tmp_path):
    from pathlib import Path

    # A stand-in engine runs a preflight whose child writes its process ID and hangs.
    pid_file = tmp_path / "child.pid"
    child = (
        f"import os, time; open({str(pid_file)!r}, 'w').write(str(os.getpid())); time.sleep(120)"
    )
    engine = (
        "import sys; from localsr.core import gpu_guard; "
        f"gpu_guard._command = lambda device: [sys.executable, '-c', {child!r}]; "
        "gpu_guard.run('gpu:0')"
    )
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    process = subprocess.Popen([sys.executable, "-c", engine], env=env)
    deadline = time.monotonic() + 60
    while not pid_file.exists() and time.monotonic() < deadline:
        time.sleep(0.2)
    child_pid = int(pid_file.read_text())
    process.kill()  # what the app does on quit or an overdue cancel
    process.wait()

    def alive(pid):
        # Bytes: the listing is in the console's code page, not UTF-8.
        listing = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True)
        return str(pid).encode() in listing.stdout

    deadline = time.monotonic() + 10
    while alive(child_pid) and time.monotonic() < deadline:
        time.sleep(0.2)
    assert not alive(child_pid)


def test_the_engine_starts_the_preflight_child_from_its_command_line():
    from pathlib import Path

    from localsr.core import gpu_guard

    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    child = subprocess.run(
        gpu_guard._command("unknown:0"), env=env, stdin=subprocess.DEVNULL, timeout=120
    )
    # The engine's server would exit 0 on the closed input; only the preflight says it
    # could not test an unknown device.
    assert child.returncode == gpu_guard.UNTESTED


def test_crash_records_survive_locked_notes_full_disks_and_other_writers(monkeypatch, tmp_path):
    from pathlib import Path

    from localsr.core import gpu_guard

    monkeypatch.setattr(gpu_guard, "_CRASHED", {})
    monkeypatch.setenv("LOCALSR_ONNX_GATE_CACHE", str(tmp_path))
    notes = gpu_guard.CrashNotes(tmp_path)
    locked = tmp_path / "running-aaaaaaaaaaaaaaaa-77.json"
    crashed = tmp_path / "running-bbbbbbbbbbbbbbbb-4242.json"
    locked.write_text(json.dumps({"key": "gpu A", "pid": 77}))
    crashed.write_text(json.dumps({"key": "gpu B", "pid": 4242}))
    os.utime(locked, (0, 0))  # stale
    unlink = Path.unlink

    def sharing_violation(self, missing_ok=False):
        if self == locked:
            raise PermissionError("in use")
        unlink(self, missing_ok=missing_ok)

    def listing(*entries):
        return lambda self: [{"key": key, "pid": pid, "path": path} for key, pid, path in entries]

    # A note that cannot be removed does not stop settling before the crashed engine's note.
    monkeypatch.setattr(Path, "unlink", sharing_violation)
    monkeypatch.setattr(
        gpu_guard.CrashNotes, "running", listing(("gpu A", 77, locked), ("gpu B", 4242, crashed))
    )
    monkeypatch.setenv(gpu_guard.CRASHED_ENGINE_ENV, "4242")
    gpu_guard.settle()
    monkeypatch.setattr(gpu_guard, "_CRASHED", {})
    assert notes.crashed("gpu B") == "engine" and not crashed.exists()

    # Each GPU has its own record: another process recording another GPU keeps both.
    assert gpu_guard.CrashNotes(tmp_path).mark("gpu C", "preflight")
    assert notes.crashed("gpu B") == "engine" and notes.crashed("gpu C") == "preflight"

    # A record that cannot be stored keeps its note as the evidence.
    crashed.write_text(json.dumps({"key": "gpu D", "pid": 4242}))
    monkeypatch.setattr(gpu_guard.CrashNotes, "running", listing(("gpu D", 4242, crashed)))

    def full_disk(path, value):
        raise OSError("disk full")

    monkeypatch.setattr(gpu_guard, "_write_json", full_disk)
    monkeypatch.setenv(gpu_guard.CRASHED_ENGINE_ENV, "4242")
    gpu_guard.settle()
    assert crashed.exists()


def test_a_stored_preflight_pass_needs_a_gpu_that_can_be_told_apart(monkeypatch, tmp_path):
    from localsr.core import gpu_guard

    outcomes = []

    def preflight(device, cancel_event=None, status=None):
        outcomes.append(device)
        return "passed"

    monkeypatch.setattr(gpu_guard, "run", preflight)
    monkeypatch.setattr(gpu_guard, "_PREFLIGHT_TRIED", set())
    monkeypatch.setattr(gpu_guard, "_CRASHED", {})
    image = torch.rand(1, 3, 8, 8)
    first, _ = gated(monkeypatch, tmp_path, ("a",), device="gpu:0")
    first(image)
    assert (tmp_path / "preflight.json").exists()
    # The same identity and device, now one of two identical cards: tested again.
    monkeypatch.setattr(gpu_guard, "_PREFLIGHT_TRIED", set())
    twin, _ = gated(monkeypatch, tmp_path, ("a",), device="gpu:0", persist=False)
    twin(image)
    assert outcomes == ["gpu:0", "gpu:0"]


def test_timed_out_preflight_refuses_gpu_for_process_only(monkeypatch, tmp_path):
    from localsr.core import gpu_guard
    from localsr.core.onnx_runtime import GpuRouteRejected

    monkeypatch.setattr(gpu_guard, "run", lambda *args: "timeout")
    monkeypatch.setattr(gpu_guard, "_PREFLIGHT_TRIED", set())
    monkeypatch.setattr(gpu_guard, "_PREFLIGHT_TIMEOUTS", set())
    monkeypatch.setattr(gpu_guard, "_CRASHED", {})
    for _ in range(2):
        converted, calls = gated(monkeypatch, tmp_path, ("a",), device="gpu:timeout")
        with pytest.raises(
            GpuRouteRejected,
            match=r"did not finish a test run within five minutes, so LocalSR does not use that GPU for now\. Select the CPU, or restart LocalSR to try again\.",
        ):
            converted(torch.rand(1, 3, 8, 8))
        assert calls == []
    assert not list(tmp_path.glob("crash-*.json"))


def _ep_device(kind, ep_name="OpenVINOExecutionProvider", **metadata):
    return SimpleNamespace(
        ep_name=ep_name,
        device=SimpleNamespace(type=f"OrtHardwareDeviceType.{kind}"),
        ep_metadata={"ov_device": kind, **metadata},
    )


def test_npus_are_found_apart_from_gpus_with_a_driver_identity(monkeypatch):
    import onnxruntime as ort

    from localsr.core import openvino_devices

    npu = _ep_device(
        "NPU",
        DEVICE_UUID="80D1D11EB73811EAB3DE0242AC130004",
        FULL_DEVICE_NAME="Intel(R) AI Boost",
        DEVICE_ARCHITECTURE="3720",
        NPU_DRIVER_VERSION="1779321600",
        NPU_COMPILER_VERSION="458781",
        version="1.8.1",
    )
    gpu = _ep_device("GPU", DEVICE_UUID="8680677d060000000002000000000000")
    auto = _ep_device("NPU", ep_name="OpenVINOExecutionProvider.AUTO", DEVICE_UUID="ff")
    monkeypatch.setattr(openvino_devices, "plugin_registered", lambda: True)
    monkeypatch.setattr(openvino_devices, "_driver", lambda luid: None)
    monkeypatch.setattr(ort, "get_ep_devices", lambda: [gpu, npu, auto], raising=False)
    (found,) = openvino_devices.npu_devices()
    assert found["key"] == "80d1d11eb73811eab3de0242ac130004"
    assert found["kind"] == "NPU" and found["name"] == "Intel(R) AI Boost"
    assert found["compiler"] == "458781"
    assert found["driver"] == "npu-driver:1779321600|npu-compiler:458781"
    assert found["ep_device"] is npu
    assert [device["key"] for device in openvino_devices.gpu_devices()] == [
        "8680677d060000000002000000000000"
    ]
    assert openvino_devices.device_for("80D1D11EB73811EAB3DE0242AC130004")["kind"] == "NPU"
    # Without a driver version the device still runs; its verdicts are just not stored.
    npu.ep_metadata.pop("NPU_DRIVER_VERSION")
    assert openvino_devices.npu_devices()[0]["driver"] is None


def test_an_npu_device_id_becomes_an_openvino_route_that_knows_it_is_an_npu(monkeypatch):
    from localsr.core import inference, openvino_devices

    entry = {
        "key": "80d1d11eb73811eab3de0242ac130004",
        "kind": "NPU",
        "name": "Intel(R) AI Boost",
        "device_id": "",
        "architecture": "3720",
        "plugin_version": "1.8.1",
        "driver": "npu-driver:1779321600|npu-compiler:458781",
        "ep_device": object(),
    }
    monkeypatch.setattr(openvino_devices, "device_for", lambda key: entry)
    route = inference._onnx_route("openvino:80d1d11eb73811eab3de0242ac130004")
    assert route.provider == "OpenVINOExecutionProvider" and route.kind == "NPU"
    assert route.label == "Intel AI Boost (NPU)"
    assert "npu-compiler:458781" in route.identity and route.persist
    entry["driver"] = None
    assert not inference._onnx_route("openvino:80d1d11eb73811eab3de0242ac130004").persist


def test_an_npu_route_accepts_the_fp16_difference_a_gpu_route_refuses(monkeypatch, tmp_path):
    from localsr.core.onnx_runtime import GpuRouteRejected

    image = torch.rand(1, 3, 8, 8)
    npu, _ = gated(monkeypatch, tmp_path / "npu", ("fp16",), slight={"fp16"}, kind="NPU")
    npu(image)
    assert active_config(npu) == "fp16"
    gpu, _ = gated(monkeypatch, tmp_path / "gpu", ("fp16",), slight={"fp16"})
    with pytest.raises(GpuRouteRejected):
        gpu(image)


def test_npu_sessions_compile_once_into_the_check_cache(monkeypatch, tmp_path):
    from localsr.core import onnx_runtime
    from localsr.core.onnx_runtime import OnnxRoute

    monkeypatch.setenv("LOCALSR_ONNX_GATE_CACHE", str(tmp_path))
    npu = OnnxRoute("OpenVINOExecutionProvider", ep_device=object(), kind="NPU")
    options = onnx_runtime._provider_options(npu, {})
    assert json.loads(options["load_config"]) == {"NPU": {"CACHE_DIR": str(tmp_path / "npu-cache")}}
    assert [config[0] for config in onnx_runtime._route_configs(npu)] == ["npu-fp16"]
    gpu = OnnxRoute("OpenVINOExecutionProvider", ep_device=object())
    assert onnx_runtime._provider_options(gpu, {"load_config": "x"}) == {"load_config": "x"}
    assert (
        onnx_runtime._route_configs(gpu) == onnx_runtime.ROUTE_CONFIGS["OpenVINOExecutionProvider"]
    )


def test_a_model_the_npu_cannot_run_falls_back_to_the_cpu_and_says_so(monkeypatch):
    from localsr.core import inference
    from localsr.core.model_adapter import NormalizedModelInfo
    from localsr.core.onnx_runtime import NPU_ARCHITECTURES, OnnxRoute

    npu = OnnxRoute(
        "OpenVINOExecutionProvider", ep_device=object(), kind="NPU", label="Intel(R) AI Boost (NPU)"
    )
    monkeypatch.setattr(inference, "_onnx_route", lambda device: npu)
    loads = []

    class _Adapter:
        def load(self, path, device, precision, **kwargs):
            loads.append((device.type, kwargs.get("onnx_route")))
            return object(), None

    engine = inference.InferenceEngine(_Adapter())
    messages = []
    engine.status_callback = messages.append
    monkeypatch.setattr(engine, "_attach_status", lambda model: None)
    supported = sorted(NPU_ARCHITECTURES)[0]
    for architecture in ("HAT", supported):
        # A half-capable model: a saved FP16 must still not reach the CPU fallback.
        info = NormalizedModelInfo(architecture, 4, 3, 3, True, True, 1, 1, "m.pth", [])
        engine.load_model(f"{architecture}.pth", "openvino:80d1", "fp16", info)
    assert loads[0] == ("cpu", None) and loads[1] == ("cpu", npu)
    assert engine._loaded_models["HAT.pth"][2] == torch.float32
    assert messages == ["HAT does not run on Intel(R) AI Boost (NPU), so it runs on the CPU."]


def test_desktop_names_the_same_npu_architectures_as_the_worker():
    import re
    from pathlib import Path

    from localsr.core.model_catalog import CATALOG_BY_FILENAME
    from localsr.core.onnx_runtime import NPU_ARCHITECTURES

    source = (
        Path(__file__).resolve().parents[1] / "desktop" / "src" / "lib" / "neuralEngine.ts"
    ).read_text(encoding="utf-8")
    listed = set(
        re.findall(r"'(\w+)'", re.search(r"NPU_ARCHITECTURES = new Set\(\[(.*?)\]\)", source)[1])
    )
    # The catalog names two Spandrel architectures differently.
    catalog_name = {"ESRGAN": "RealESRGAN", "PLKSR": "RealPLKSR"}
    expected = {catalog_name.get(name, name) for name in NPU_ARCHITECTURES}
    assert listed == expected
    assert expected <= {model.architecture for model in CATALOG_BY_FILENAME.values()}


def test_openvino_discovery_that_crashed_the_engine_is_skipped_from_then_on(monkeypatch, tmp_path):
    # Device discovery loads Intel's GPU and NPU drivers (Level Zero) inside the engine. A
    # driver that crashes there would crash every start; after one crash the app goes on
    # without OpenVINO devices.
    from localsr.core import gpu_guard, openvino_devices

    monkeypatch.setenv(gpu_guard.GATE_CACHE_ENV, str(tmp_path))
    monkeypatch.setattr(gpu_guard, "_CRASHED", {})
    openvino_devices.plugin_registered.cache_clear()
    gpu_guard.CrashNotes(tmp_path).mark(openvino_devices.discovery_key(), "engine")
    try:
        assert openvino_devices.plugin_registered() is False
        assert "crashed" in openvino_devices.registration_error
        assert openvino_devices.npu_devices() == [] and openvino_devices.gpu_devices() == []
    finally:
        openvino_devices.plugin_registered.cache_clear()


def test_an_npu_runs_one_window_shape_so_each_model_compiles_once():
    # Every input shape is a separate NPU compile (seconds to minutes, and a cached program
    # as large as the model). Edge tiles therefore come as full windows of real pixels.
    from localsr.core.onnx_runtime import OnnxImageDescriptor, OnnxRoute

    npu = OnnxImageDescriptor(
        descriptor(), route=OnnxRoute("OpenVINOExecutionProvider", ep_device=object(), kind="NPU")
    )
    assert npu.fixed_input_shape is None
    npu.prepare_tiles(256, 16)
    assert npu.fixed_input_shape == (288, 288)
    npu.prepare_tiles(128, 16)  # smaller tiles after running out of memory
    assert npu.fixed_input_shape == (160, 160)
    gpu = OnnxImageDescriptor(
        descriptor(), route=OnnxRoute("OpenVINOExecutionProvider", ep_device=object())
    )
    gpu.prepare_tiles(256, 16)
    assert gpu.fixed_input_shape is None


def test_the_npu_compile_cache_keeps_the_newest_programs_within_its_limit(tmp_path):
    from localsr.core.onnx_runtime import _trim_npu_cache

    cache = tmp_path / "npu-cache"
    cache.mkdir()
    for index, size in enumerate((300, 200, 100)):
        blob = cache / f"{index}.blob"
        blob.write_bytes(b"x" * size)
        os.utime(blob, (1_000 + index, 1_000 + index))
    _trim_npu_cache(cache, limit=350)
    assert sorted(path.name for path in cache.iterdir()) == ["1.blob", "2.blob"]
    _trim_npu_cache(tmp_path / "missing", limit=1)  # nothing cached yet: nothing to do


def test_the_npu_cache_is_trimmed_again_after_a_compile_adds_to_it(monkeypatch, tmp_path):
    import onnxruntime as ort

    from localsr.core import onnx_runtime
    from localsr.core.onnx_runtime import OnnxImageDescriptor, OnnxRoute

    monkeypatch.setenv("LOCALSR_ONNX_GATE_CACHE", str(tmp_path))
    monkeypatch.setattr(onnx_runtime, "NPU_CACHE_LIMIT", 250)
    cache = tmp_path / "npu-cache"
    cache.mkdir()
    old = cache / "old.blob"
    old.write_bytes(b"x" * 200)
    os.utime(old, (1_000, 1_000))

    class _Session:
        def __init__(self, graph, sess_options=None, **kwargs):
            (cache / "new.blob").write_bytes(b"y" * 200)  # what compiling leaves behind

        def disable_fallback(self):
            pass

        def get_providers(self):
            return ["OpenVINOExecutionProvider"]

    monkeypatch.setattr(ort, "InferenceSession", _Session)
    monkeypatch.setattr(
        ort.SessionOptions, "add_provider_for_devices", lambda *args: None, raising=False
    )
    converted = OnnxImageDescriptor(
        descriptor(), route=OnnxRoute("OpenVINOExecutionProvider", ep_device=object(), kind="NPU")
    )
    session, trace = converted.model._create(b"graph", ("npu-fp16", {}, "all"))
    trace.cleanup()
    assert sorted(path.name for path in cache.iterdir()) == ["new.blob"]


def test_a_session_that_never_opens_stops_the_job_with_a_sentence_and_stores_nothing(
    monkeypatch, tmp_path
):
    import threading
    import time

    from localsr.core import onnx_runtime, preparation
    from localsr.core.onnx_runtime import GpuRouteRejected

    monkeypatch.setattr(onnx_runtime, "ONNX_PREPARE_SECONDS", 0.3)
    monkeypatch.setattr(preparation, "WAIT_SLICE_SECONDS", 0.02)
    monkeypatch.setattr(preparation, "STATUS_INTERVAL_SECONDS", 0.1)
    preparation._abandoned.clear()
    converted, _ = gated(monkeypatch, tmp_path, ("a", "b"))
    release = threading.Event()
    monkeypatch.setattr(
        onnx_runtime._OnnxImage, "_create", lambda self, graph, config: release.wait()
    )
    statuses = []
    converted.set_status_callback(statuses.append)
    started = time.monotonic()
    with pytest.raises(GpuRouteRejected, match="did not finish preparing"):
        converted(torch.rand(1, 3, 8, 8))
    assert time.monotonic() - started < 2.0
    # The second configuration is not tried, and no rejection is stored.
    assert not (tmp_path / "verdicts.json").exists()
    assert any("Stopping the job in" in status for status in statuses)
    release.set()
    deadline = time.monotonic() + 2
    while preparation.busy_slot("onnx") and time.monotonic() < deadline:
        time.sleep(0.01)


def test_cancelling_while_a_session_opens_returns_at_once(monkeypatch, tmp_path):
    import threading
    import time

    from localsr.core import onnx_runtime, preparation

    monkeypatch.setattr(preparation, "WAIT_SLICE_SECONDS", 0.02)
    preparation._abandoned.clear()
    converted, _ = gated(monkeypatch, tmp_path, ("a",))
    release, cancel = threading.Event(), threading.Event()
    converted.set_cancel_event(cancel)
    monkeypatch.setattr(
        onnx_runtime._OnnxImage, "_create", lambda self, graph, config: release.wait()
    )
    threading.Timer(0.2, cancel.set).start()
    started = time.monotonic()
    with pytest.raises(InterruptedError):
        converted(torch.rand(1, 3, 8, 8))
    assert time.monotonic() - started < 2.0
    assert not (tmp_path / "verdicts.json").exists()
    release.set()
    deadline = time.monotonic() + 2
    while preparation.busy_slot("onnx") and time.monotonic() < deadline:
        time.sleep(0.01)


def test_the_preflight_reports_how_long_it_has_run_and_when_it_gives_up(monkeypatch):
    from localsr.core import gpu_guard, preparation

    monkeypatch.setattr(
        gpu_guard, "_command", lambda device: [sys.executable, "-c", "import time; time.sleep(30)"]
    )
    monkeypatch.setattr(gpu_guard, "TIMEOUT_SECONDS", 0.6)
    monkeypatch.setattr(preparation, "STATUS_INTERVAL_SECONDS", 0.2)
    reports = []
    assert gpu_guard.run("gpu:0", status=lambda elapsed, left: reports.append((elapsed, left))) == (
        "timeout"
    )
    assert len(reports) >= 2
    assert reports[0][0] == 0 and all(left >= 0 for _, left in reports)
