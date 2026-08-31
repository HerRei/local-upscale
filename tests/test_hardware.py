import sys
from contextlib import nullcontext
from types import SimpleNamespace

from localsr.core import hardware


def test_mps_and_cpu_capabilities_always_describe_shared_memory(monkeypatch):
    gib = 1024**3
    monkeypatch.setattr(hardware.sys, "platform", "darwin")
    monkeypatch.setattr(hardware, "_system_memory", lambda: (16 * gib, 8 * gib))
    monkeypatch.setattr(hardware, "_mps_memory", lambda _total, _available: (12 * gib, 6 * gib))
    monkeypatch.setattr(hardware, "_system_pressure_snapshot", lambda _total, _available: {})
    monkeypatch.setattr(hardware.torch.backends.mps, "is_available", lambda: True)
    monkeypatch.setattr(hardware.torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(
        hardware.torch,
        "xpu",
        SimpleNamespace(is_available=lambda: False),
        raising=False,
    )

    devices = hardware.get_capability_report()["devices"]

    assert next(device for device in devices if device["id"] == "mps")["is_integrated"] is True
    assert next(device for device in devices if device["id"] == "cpu")["is_integrated"] is False
    assert all(isinstance(device["is_integrated"], bool) for device in devices)


def test_directml_probe_can_be_explicitly_skipped_for_hardwareless_packaging_vm(
    monkeypatch,
):
    fake_directml = SimpleNamespace(
        is_available=lambda: (_ for _ in ()).throw(
            AssertionError("DirectML hardware probe should have been skipped")
        )
    )
    monkeypatch.setitem(sys.modules, "torch_directml", fake_directml)
    monkeypatch.setenv("LOCALSR_SKIP_DIRECTML_PROBE", "1")
    devices = []

    hardware._detect_directml(devices, 16 * 1024**3, 8 * 1024**3)

    assert devices == []


def test_discovers_each_available_directml_adapter(monkeypatch):
    gib = 1024**3
    monkeypatch.delenv("LOCALSR_SKIP_DIRECTML_PROBE", raising=False)
    monkeypatch.setitem(
        sys.modules,
        "torch_directml",
        SimpleNamespace(is_available=lambda: True, device_count=lambda: 2),
    )
    devices = []

    hardware._detect_directml(devices, 16 * gib, 8 * gib)

    assert [device["id"] for device in devices] == ["directml:0", "directml:1"]
    assert all(device["type"] == "directml" for device in devices)
    assert all(device["is_integrated"] is True for device in devices)


def test_capability_report_does_not_advertise_unimplemented_qnn(monkeypatch):
    gib = 1024**3
    monkeypatch.setattr(hardware.sys, "platform", "win32")
    monkeypatch.setattr(hardware, "_system_memory", lambda: (16 * gib, 8 * gib))
    monkeypatch.setattr(hardware, "_system_pressure_snapshot", lambda _total, _available: {})
    monkeypatch.setattr(hardware.torch.backends.mps, "is_available", lambda: False)
    monkeypatch.setattr(hardware.torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(
        hardware.torch,
        "xpu",
        SimpleNamespace(is_available=lambda: False),
        raising=False,
    )
    monkeypatch.setenv("LOCALSR_SKIP_DIRECTML_PROBE", "1")
    monkeypatch.setitem(
        sys.modules,
        "onnxruntime",
        SimpleNamespace(get_available_providers=lambda: ["QNNExecutionProvider"]),
    )

    report = hardware.get_capability_report()

    assert all(not device["type"].startswith("qnn") for device in report["devices"])


def test_discovers_rocm_and_intel_integrated_xpu(monkeypatch):
    gib = 1024**3
    monkeypatch.setattr(hardware, "_system_memory", lambda: (16 * gib, 8 * gib))
    monkeypatch.setattr(
        hardware,
        "_system_pressure_snapshot",
        lambda _total, _available: {
            "system_memory_pressure_percent": 50.0,
            "system_memory_pressure_level": "low",
            "system_compressed_memory": 0,
            "system_swap_total": 0,
            "system_swap_used": 0,
        },
    )
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
    monkeypatch.setattr(hardware.torch, "xpu", fake_xpu, raising=False)

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


def test_macos_pressure_and_swap_parsers():
    assert hardware._parse_macos_pressure("System-wide memory free percentage: 71%\n") == 29.0
    assert hardware._parse_macos_pressure("not available") is None
    assert hardware._parse_macos_swap("total = 2.00G  used = 384.50M  free = 1.62G") == (
        2 * 1024**3,
        int(384.5 * 1024**2),
    )
    assert hardware._pressure_level(74.9) == "low"
    assert hardware._pressure_level(75.0) == "moderate"
    assert hardware._pressure_level(90.0) == "high"


def test_mps_snapshot_exposes_allocator_and_pressure(monkeypatch):
    gib = 1024**3
    monkeypatch.setattr(hardware, "_system_memory", lambda: (16 * gib, 5 * gib))
    monkeypatch.setattr(hardware.torch.backends.mps, "is_available", lambda: True)
    monkeypatch.setattr(hardware.torch.mps, "current_allocated_memory", lambda: 1 * gib)
    monkeypatch.setattr(hardware.torch.mps, "driver_allocated_memory", lambda: 2 * gib)
    monkeypatch.setattr(
        hardware.torch.mps, "recommended_max_memory", lambda: 10 * gib, raising=False
    )
    monkeypatch.setattr(
        hardware,
        "_system_pressure_snapshot",
        lambda _total, _available: {
            "system_memory_pressure_percent": 82.0,
            "system_memory_pressure_level": "moderate",
            "system_compressed_memory": 3 * gib,
            "system_swap_total": 4 * gib,
            "system_swap_used": 1 * gib,
        },
    )

    snapshot = hardware.get_memory_snapshot("mps")

    assert snapshot["mps_tensor_allocated_memory"] == 1 * gib
    assert snapshot["mps_driver_allocated_memory"] == 2 * gib
    assert snapshot["mps_recommended_max_memory"] == 10 * gib
    assert snapshot["system_memory_pressure_level"] == "moderate"
    assert snapshot["system_compressed_memory"] == 3 * gib


def test_discovers_nvidia_cuda_and_intel_discrete_xpu(monkeypatch):
    gib = 1024**3
    monkeypatch.setattr(hardware, "_system_memory", lambda: (32 * gib, 20 * gib))
    monkeypatch.setattr(
        hardware,
        "_system_pressure_snapshot",
        lambda _total, _available: {
            "system_memory_pressure_percent": 30.0,
            "system_memory_pressure_level": "low",
            "system_compressed_memory": 0,
            "system_swap_total": 0,
            "system_swap_used": 0,
        },
    )
    monkeypatch.setattr(hardware.torch.backends.mps, "is_available", lambda: False)
    monkeypatch.setattr(hardware.torch.version, "hip", None)

    fake_cuda = SimpleNamespace(
        is_available=lambda: True,
        device_count=lambda: 2,
        device=lambda _index: nullcontext(),
        mem_get_info=lambda index: ((10 - index) * gib, 12 * gib),
        get_device_properties=lambda index: SimpleNamespace(
            name=f"Test GeForce {index}",
            major=8 if index == 0 else 5,
            minor=9 if index == 0 else 0,
        ),
    )
    fake_xpu = SimpleNamespace(
        is_available=lambda: True,
        device_count=lambda: 1,
        mem_get_info=lambda _index: (14 * gib, 16 * gib),
        get_device_properties=lambda _index: SimpleNamespace(
            name="Test Arc A770",
            has_fp16=True,
            is_integrated_gpu=False,
        ),
    )
    monkeypatch.setattr(hardware.torch, "cuda", fake_cuda)
    monkeypatch.setattr(hardware.torch, "xpu", fake_xpu, raising=False)

    report = hardware.get_capability_report()
    cuda_devices = [device for device in report["devices"] if device["type"] == "cuda"]
    xpu = next(device for device in report["devices"] if device["type"] == "xpu")

    assert [device["id"] for device in cuda_devices] == ["cuda:0", "cuda:1"]
    assert all("CUDA" in device["name"] for device in cuda_devices)
    # Compute capability 8.9 supports fp16; 5.0 predates fp16 support.
    assert cuda_devices[0]["supports_fp16"] is True
    assert cuda_devices[1]["supports_fp16"] is False
    assert cuda_devices[0]["free_memory"] == 10 * gib
    assert xpu["name"] == "Test Arc A770 (Intel XPU, discrete)"
    assert xpu["is_integrated"] is False
    # The CPU fallback is always enumerated last.
    assert report["devices"][-1]["id"] == "cpu"
