"""Exercise actual graph conversion and the descriptor boundary on CPU in CI."""

from types import SimpleNamespace

import numpy as np
import pytest
import spandrel
import torch

from localsr.core.inference import InferenceEngine
from localsr.core.onnx_runtime import OnnxImageDescriptor

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
    for side in (8, 16, 24):
        converted(torch.zeros(1, 3, side, side))
    assert list(converted.model.sessions) == [(1, 3, 16, 16), (1, 3, 24, 24)]
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
            model = SimpleNamespace(execution_provider=kwargs.get("onnx_device_index", "cpu"))
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
        ("cpu", torch.float32, {"onnx_device_index": 0}),
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
