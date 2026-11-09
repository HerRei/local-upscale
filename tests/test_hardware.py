import sys
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from localsr.core import hardware


@pytest.fixture(autouse=True)
def isolate_directml_hardware(monkeypatch):
    from localsr.core import onnx_runtime

    # These are discovery tests, not probes of the CI host's physical adapter.
    monkeypatch.setattr(onnx_runtime, "directml_available", lambda: False)


def test_onnx_discovery_keeps_dxgi_ids_and_separates_shared_memory(monkeypatch):
    from localsr.core import onnx_runtime, windows_adapters

    gib = 1024**3
    monkeypatch.delenv("LOCALSR_SKIP_DIRECTML_PROBE", raising=False)
    monkeypatch.setattr(onnx_runtime, "directml_available", lambda: True)
    monkeypatch.setattr(
        windows_adapters,
        "directml_adapters",
        lambda: [
            {"index": 2, "name": "Intel UHD", "dedicated_memory": 128 * 1024**2},
            {"index": 5, "name": "Discrete GPU", "dedicated_memory": 6 * gib},
        ],
    )
    devices = []
    hardware._detect_directml(devices, 16 * gib, 8 * gib)
    assert [device["id"] for device in devices] == ["directml:2", "directml:5"]
    assert [device["is_integrated"] for device in devices] == [True, False]
    assert [device["total_memory"] for device in devices] == [16 * gib, 6 * gib]
    assert all(device["free_memory"] == 4 * gib for device in devices)
    assert all(not device["supports_fp16"] for device in devices)


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
        SimpleNamespace(
            is_available=lambda: True,
            device_count=lambda: 2,
            device_name=lambda index: ["Intel(R) Iris(R) Xe Graphics\x00", "NVIDIA GeForce RTX"][
                index
            ],
        ),
    )
    devices = []

    hardware._detect_directml(devices, 16 * gib, 8 * gib)

    assert [device["id"] for device in devices] == ["directml:0", "directml:1"]
    assert all(device["type"] == "directml" for device in devices)
    assert all(device["is_integrated"] is True for device in devices)
    assert devices[0]["name"] == "Intel(R) Iris(R) Xe Graphics (DirectML)"
    assert devices[1]["name"] == "NVIDIA GeForce RTX (DirectML)"


def test_directml_name_failure_keeps_adapter_ids_selectable(monkeypatch):
    def name(index):
        if index == 0:
            raise RuntimeError("Driver name lookup failed")
        return "Intel(R) UHD Graphics"

    monkeypatch.delenv("LOCALSR_SKIP_DIRECTML_PROBE", raising=False)
    monkeypatch.setitem(
        sys.modules,
        "torch_directml",
        SimpleNamespace(is_available=lambda: True, device_count=lambda: 2, device_name=name),
    )
    devices = []
    hardware._detect_directml(devices, 16 * 1024**3, 8 * 1024**3)
    assert [(device["id"], device["name"]) for device in devices] == [
        ("directml:0", "DirectML Device 0"),
        ("directml:1", "Intel(R) UHD Graphics (DirectML)"),
    ]


@pytest.mark.parametrize(
    "failure", [OSError("DirectML.dll missing"), RuntimeError("Driver failed")]
)
def test_directml_driver_failure_preserves_cpu_capabilities(monkeypatch, failure):
    def unavailable():
        raise failure

    monkeypatch.setattr(hardware.sys, "platform", "win32")
    monkeypatch.setattr(hardware, "_system_memory", lambda: (16 * 1024**3, 8 * 1024**3))
    monkeypatch.setattr(hardware, "_system_pressure_snapshot", lambda *_: {})
    monkeypatch.setattr(hardware.torch.backends.mps, "is_available", lambda: False)
    monkeypatch.setattr(hardware.torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(
        hardware.torch, "xpu", SimpleNamespace(is_available=lambda: False), raising=False
    )
    monkeypatch.delenv("LOCALSR_SKIP_DIRECTML_PROBE", raising=False)
    monkeypatch.setitem(sys.modules, "torch_directml", SimpleNamespace(is_available=unavailable))

    assert [d["id"] for d in hardware.get_capability_report()["devices"]] == ["cpu"]


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


def test_directml_uses_uma_and_counts_shared_memory_for_integrated_gpus(monkeypatch):
    from localsr.core import onnx_runtime, windows_adapters

    gib = 1024**3
    monkeypatch.delenv("LOCALSR_SKIP_DIRECTML_PROBE", raising=False)
    monkeypatch.setattr(onnx_runtime, "directml_available", lambda: True)
    adapters = [
        # An AMD APU with a 2 GiB BIOS carve-out: the old "< 512 MiB" guess called it discrete.
        {
            "index": 0,
            "name": "AMD Radeon 780M",
            "dedicated_memory": 2 * gib,
            "uma": True,
            "budget": 2 * gib,
            "usage": 0,
            "shared_budget": 7 * gib,
            "shared_usage": 0,
        },
        # Windows grants this process little right now: the budget follows it.
        {
            "index": 1,
            "name": "Intel UHD",
            "dedicated_memory": 128 * 1024**2,
            "uma": None,
            "budget": 3 * gib,
            "usage": 1 * gib,
            "shared_budget": 0,
            "shared_usage": 0,
        },
    ]
    monkeypatch.setattr(windows_adapters, "directml_adapters", lambda **_: adapters)
    devices = []
    hardware._detect_directml(devices, 16 * gib, 12 * gib)
    amd, intel = devices
    assert amd["is_integrated"] is True and amd["total_memory"] == 16 * gib
    assert amd["free_memory"] == 6 * gib  # half the available RAM, not the 2 GiB carve-out
    assert intel["is_integrated"] is True  # no UMA answer: the old guess decides
    assert intel["free_memory"] == int(2 * gib * 0.85)


def test_integrated_rocm_gpu_is_budgeted_from_shared_memory(monkeypatch):
    from contextlib import nullcontext

    gib = 1024**3
    monkeypatch.setattr(hardware, "_system_memory", lambda: (32 * gib, 20 * gib))
    monkeypatch.setattr(hardware.torch.backends.mps, "is_available", lambda: False)
    monkeypatch.setattr(hardware.torch.version, "hip", "7.2")
    monkeypatch.setattr(
        hardware.torch,
        "cuda",
        SimpleNamespace(
            is_available=lambda: True,
            device_count=lambda: 1,
            device=lambda _index: nullcontext(),
            mem_get_info=lambda _index: (400 * 1024**2, 512 * 1024**2),
            get_device_properties=lambda _index: SimpleNamespace(
                name="AMD Radeon 780M", major=11, minor=0, is_integrated=1
            ),
        ),
    )
    monkeypatch.setattr(
        hardware.torch, "xpu", SimpleNamespace(is_available=lambda: False), raising=False
    )
    report = hardware.get_capability_report()
    apu = next(device for device in report["devices"] if device["id"] == "cuda:0")
    assert apu["is_integrated"] is True
    assert apu["free_memory"] == 10 * gib  # not the 400 MiB the carve-out reports


def test_integrated_gpu_gets_no_allocator_cap_against_its_carve_out(monkeypatch):
    from localsr.core import inference

    calls = []
    fake = SimpleNamespace(
        get_device_properties=lambda _device: SimpleNamespace(is_integrated=1),
        mem_get_info=lambda _device: (400 * 1024**2, 512 * 1024**2),
        set_per_process_memory_fraction=lambda *args: calls.append(args),
    )
    monkeypatch.setattr(inference.torch, "cuda", fake)
    assert inference._apply_gpu_memory_limit(inference.torch.device("cuda", 0), True) is None
    assert calls == []


def test_openvino_gpus_are_offered_with_stable_ids(monkeypatch):
    from localsr.core import openvino_devices

    gib = 1024**3
    monkeypatch.setattr(
        openvino_devices,
        "gpu_devices",
        lambda: [
            {
                "key": "8680677d060000000002000000000000",
                "name": "Intel(R) Graphics (iGPU)",
                "integrated": True,
                "total_memory": 30 * gib,
                "max_allocation": 4 * gib,
                "luid": "20cc4f2663550000",
                "device_id": "0x7d67",
                "architecture": "GPU: vendor=0x8086 arch=v12.70.4",
                "plugin_version": "1.8.1",
                "ep_device": object(),
            }
        ],
    )
    devices = []
    hardware._detect_openvino(devices, 12 * gib)
    (device,) = devices
    assert device["id"] == "openvino:8680677d060000000002000000000000"
    assert device["type"] == "openvino" and device["is_integrated"]
    assert device["free_memory"] == 6 * gib and not device["supports_fp16"]


def test_openvino_discovery_failure_hides_nothing_else(monkeypatch):
    from localsr.core import openvino_devices

    def broken():
        raise OSError("plugin library missing")

    monkeypatch.setattr(openvino_devices, "gpu_devices", broken)
    devices = [{"id": "cpu"}]
    hardware._detect_openvino(devices, 1024**3)
    assert devices == [{"id": "cpu"}]


@pytest.mark.skipif(sys.platform != "win32", reason="real DXGI and D3D12 calls")
def test_dxgi_queries_work_on_the_windows_software_adapter():
    """Every Windows machine has WARP, Microsoft's software D3D12 adapter, so the raw COM
    calls (function-table slots, structure layouts) run against a real adapter here."""
    from localsr.core.windows_adapters import directml_adapters

    adapters = directml_adapters(include_software=True)
    warp = next(adapter for adapter in adapters if adapter["software"])
    assert warp["vendor_id"] == 0x1414
    assert len(warp["luid"]) == 16 and int(warp["luid"], 16) >= 0
    assert warp["uma"] is True  # WARP renders in system memory
    assert warp["budget"] > 0 and warp["usage"] >= 0
    assert warp["shared_budget"] >= 0 and warp["shared_usage"] >= 0
    version = warp["driver_version"]
    assert version is None or len(version.split(".")) == 4
    # Without include_software the WARP adapter is never offered.
    assert not any(adapter["software"] for adapter in directml_adapters())
    # A lookup without a Direct3D device still reads names, ids, memory and the driver.
    looked_up = next(
        adapter
        for adapter in directml_adapters(include_software=True, probe=False)
        if adapter["software"]
    )
    assert looked_up["luid"] == warp["luid"] and looked_up["uma"] is None
    assert looked_up["driver_version"] == warp["driver_version"]


def test_a_cpu_only_edition_names_the_download_for_its_gpu(monkeypatch, tmp_path):
    from localsr.core import windows_adapters

    cpu = [{"type": "cpu"}]
    monkeypatch.setattr(hardware.sys, "platform", "win32")
    adapters = [{"name": "Intel(R) UHD Graphics 620", "vendor_id": 0x8086}]
    monkeypatch.setattr(windows_adapters, "directml_adapters", lambda **_: adapters)
    assert hardware._gpu_suggestion(cpu) == {
        "name": "Intel(R) UHD Graphics 620",
        "download": "DirectML",
    }
    adapters[0] = {"name": "NVIDIA GeForce RTX 4060", "vendor_id": 0x10DE}
    assert hardware._gpu_suggestion(cpu)["download"] == "CUDA"
    # An edition that already uses a GPU suggests nothing.
    assert hardware._gpu_suggestion([{"type": "directml"}, *cpu]) is None

    from localsr.core import openvino_devices

    monkeypatch.setattr(hardware.sys, "platform", "linux")
    monkeypatch.setattr(hardware, "_DRM_ROOT", tmp_path)
    monkeypatch.setattr(openvino_devices, "plugin_registered", lambda: True)
    monkeypatch.setattr(openvino_devices, "intel_gpu_runtime_installed", lambda: False)
    (tmp_path / "card0" / "device").mkdir(parents=True)
    (tmp_path / "card0" / "device" / "vendor").write_text("0x8086\n")
    assert hardware._gpu_suggestion(cpu) == {"name": "Intel graphics", "download": "intel-driver"}
    # Intel's driver is already there: installing it again would not help.
    monkeypatch.setattr(openvino_devices, "intel_gpu_runtime_installed", lambda: True)
    assert hardware._gpu_suggestion(cpu) is None
    # An edition without OpenVINO cannot use Intel graphics at all.
    monkeypatch.setattr(openvino_devices, "intel_gpu_runtime_installed", lambda: False)
    monkeypatch.setattr(openvino_devices, "plugin_registered", lambda: False)
    assert hardware._gpu_suggestion(cpu) is None
    # A second, NVIDIA or AMD card wins over the integrated Intel graphics on card0.
    (tmp_path / "card1" / "device").mkdir(parents=True)
    (tmp_path / "card1" / "device" / "vendor").write_text("0x1002\n")
    assert hardware._gpu_suggestion(cpu) == {"name": "an AMD GPU", "download": "ROCm"}


def _npu(compiler="458781"):
    return {
        "key": "80d1d11eb73811eab3de0242ac130004",
        "name": "Intel(R) AI Boost",
        "kind": "NPU",
        "integrated": True,
        "total_memory": 0,
        "max_allocation": 0,
        "luid": "",
        "device_id": "",
        "architecture": "3720",
        "plugin_version": "1.8.1",
        "driver": "npu-driver:1779321600|npu-compiler:458781",
        "compiler": compiler,
        "ep_device": object(),
    }


def test_an_intel_npu_is_offered_as_its_own_device(monkeypatch):
    from localsr.core import openvino_devices

    gib = 1024**3
    monkeypatch.setattr(openvino_devices, "gpu_devices", lambda: [])
    monkeypatch.setattr(openvino_devices, "npu_devices", lambda: [_npu()])
    devices = []
    hardware._detect_openvino(devices, 12 * gib)
    (device,) = devices
    assert device["id"] == "openvino:80d1d11eb73811eab3de0242ac130004"
    assert device["type"] == "npu" and device["name"] == "Intel(R) AI Boost (NPU)"
    assert device["is_integrated"] and not device["supports_fp16"]
    assert device["free_memory"] == 6 * gib


def test_an_npu_without_its_compiler_is_not_offered_but_named_in_a_hint(monkeypatch):
    from localsr.core import openvino_devices

    monkeypatch.setattr(openvino_devices, "gpu_devices", lambda: [])
    monkeypatch.setattr(openvino_devices, "npu_devices", lambda: [_npu(compiler="0")])
    devices = []
    hardware._detect_openvino(devices, 8 * 1024**3)
    assert devices == []
    assert hardware._npu_hint() == {"name": "Intel(R) AI Boost", "missing": "compiler"}
    monkeypatch.setattr(openvino_devices, "npu_devices", lambda: [_npu()])
    assert hardware._npu_hint() is None
