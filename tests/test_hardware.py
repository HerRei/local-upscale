from contextlib import nullcontext
from types import SimpleNamespace

from localsr.core import hardware


def test_discovers_rocm_and_intel_integrated_xpu(monkeypatch):
    gib = 1024**3
    monkeypatch.setattr(hardware, "_system_memory", lambda: (16 * gib, 8 * gib))
    monkeypatch.setattr(hardware.torch.backends.mps, "is_available", lambda: False)
    monkeypatch.setattr(hardware.torch.version, "hip", "6.4")

    fake_cuda = SimpleNamespace(
        is_available=lambda: True,
        device_count=lambda: 1,
        device=lambda _index: nullcontext(),
        mem_get_info=lambda _index: (6 * gib, 8 * gib),
        get_device_properties=lambda _index: SimpleNamespace(
            name="Test Radeon",
            major=9,
            minor=0,
        ),
    )
    fake_xpu = SimpleNamespace(
        is_available=lambda: True,
        device_count=lambda: 1,
        mem_get_info=lambda _index: (5 * gib, 8 * gib),
        get_device_properties=lambda _index: SimpleNamespace(
            name="Test Intel Graphics",
            has_fp16=True,
            is_integrated_gpu=True,
        ),
    )
    monkeypatch.setattr(hardware.torch, "cuda", fake_cuda)
    monkeypatch.setattr(hardware.torch, "xpu", fake_xpu)

    report = hardware.get_capability_report()
    rocm = next(device for device in report["devices"] if device["type"] == "rocm")
    xpu = next(device for device in report["devices"] if device["type"] == "xpu")

    assert rocm["id"] == "cuda:0"
    assert "ROCm" in rocm["name"]
    assert rocm["free_memory"] == 6 * gib
    assert xpu["id"] == "xpu:0"
    assert "Intel XPU" in xpu["name"]
    assert xpu["is_integrated"] is True
    assert xpu["supports_fp16"] is True

    snapshot = hardware.get_memory_snapshot("xpu:0")
    assert snapshot["device_total_memory"] == 8 * gib
    assert snapshot["device_free_memory"] == 5 * gib
