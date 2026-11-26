"""Tests for LocalSR Benchmark v3 (all-device, preset models, correctness gate, CPU threads, pre-AVX2)."""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from localsr.core import hardware
from localsr.image_models.pulse import PULSE


def test_cpu_supports_avx2_detection_and_pre_avx2_fallback(monkeypatch):
    from localsr.core import hardware

    monkeypatch.setattr(hardware.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(torch.backends.cpu, "get_cpu_capability", lambda: "AVX2")
    assert hardware.cpu_supports_avx2() is True
    assert (
        hardware.cpu_memory_format() == torch.contiguous_format
        or hardware.cpu_memory_format()
        in (
            torch.contiguous_format,
            torch.channels_last,
        )
    )

    monkeypatch.setattr(torch.backends.cpu, "get_cpu_capability", lambda: "DEFAULT")
    assert hardware.cpu_supports_avx2() is False
    assert hardware.cpu_memory_format() == torch.contiguous_format

    monkeypatch.setattr(hardware.platform, "machine", lambda: "arm64")
    assert hardware.cpu_supports_avx2() is False
    assert hardware.cpu_memory_format() == torch.contiguous_format


def run_model_on_cpu_for_test(model: torch.nn.Module, tensor: torch.Tensor) -> np.ndarray:
    """Run a raw model on CPU using the safe memory format (NCHW on pre-AVX2)."""
    fmt = hardware.cpu_memory_format()
    model = model.to(device=torch.device("cpu"), dtype=torch.float32)
    model.eval()
    inp = tensor.unsqueeze(0).to(device=torch.device("cpu"), dtype=torch.float32)
    inp = inp.contiguous(memory_format=fmt)
    with torch.no_grad():
        out = model(inp)
    return (out.squeeze(0).clamp(0, 1) * 255.0).round().byte().numpy()


def test_pre_avx2_pulse_runs_in_nchw_without_channels_last(monkeypatch):
    from localsr.core import hardware
    from localsr.core.benchmark_v3 import V3_SCENES, _scene_tensor

    monkeypatch.setattr(hardware.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(torch.backends.cpu, "get_cpu_capability", lambda: "DEFAULT")

    model = PULSE(dim=16, scale=4, n_blocks=2).eval()
    tensor = _scene_tensor(V3_SCENES[0])[:, :64, :64]
    output = run_model_on_cpu_for_test(model, tensor)
    assert output.shape == (3, 256, 256)
    assert output.dtype == np.uint8


def test_resolve_preset_models_uses_quick_and_best_without_downloading(tmp_path):
    from localsr.core.benchmark_v3 import resolve_benchmark_preset_models
    from localsr.core.model_catalog import CATALOG_BY_ID, ModelStore

    store = ModelStore(tmp_path)
    quick = CATALOG_BY_ID["localsr_quick_x4_v1"]
    best = CATALOG_BY_ID["realplksr_nomoswebphoto_x4"]

    # Fake only Quick as installed on disk (bypassing checksum via monkeypatch isn't needed if we test store)
    assert quick.model_id == "localsr_quick_x4_v1"
    assert best.model_id == "realplksr_nomoswebphoto_x4"

    class FakeStore:
        def __init__(self, installed_ids):
            self.installed_ids = set(installed_ids)

        def is_installed(self, model):
            return model.model_id in self.installed_ids

        def path_for(self, model):
            return tmp_path / model.filename

    available, missing = resolve_benchmark_preset_models(store=FakeStore({quick.model_id}))
    assert [item["model"].model_id for item in available] == [quick.model_id]
    assert [item["model_id"] for item in missing] == [best.model_id]

    available_none, missing_both = resolve_benchmark_preset_models(store=FakeStore(set()))
    assert available_none == []
    assert [item["model_id"] for item in missing_both] == [quick.model_id, best.model_id]
    assert store.root == tmp_path


def test_enumerate_benchmark_devices_splits_coreml_and_mps_and_puts_cpu_first():
    from localsr.core.benchmark_v3 import enumerate_benchmark_devices

    report = {
        "devices": [
            {"id": "mps", "type": "mps", "name": "Apple GPU (Metal)", "neural_engine": True},
            {"id": "directml:0", "type": "directml", "name": "Intel UHD 620 (DirectML)"},
            {"id": "openvino:0", "type": "openvino", "name": "Intel UHD 620 (OpenVINO)"},
            {"id": "cuda:0", "type": "cuda", "name": "NVIDIA RTX 4060 (CUDA)"},
            {"id": "cpu", "type": "cpu", "name": "CPU"},
        ]
    }
    devices = enumerate_benchmark_devices(report)
    ids = [d["id"] for d in devices]
    assert ids[0] == "cpu"
    assert "neural-engine" in ids
    assert "mps" in ids
    assert "directml:0" in ids
    assert "openvino:0" in ids
    assert "cuda:0" in ids
    ne = next(d for d in devices if d["id"] == "neural-engine")
    assert ne["base_device_id"] == "mps"
    assert ne["neural_engine"] is True
    mps = next(d for d in devices if d["id"] == "mps")
    assert mps["neural_engine"] is False


def test_psnr_thresholds_reuse_coreml_and_onnx_gates():
    from localsr.core.benchmark_v3 import compute_psnr, psnr_threshold_for_device

    assert psnr_threshold_for_device("coreml") == 45.0
    assert psnr_threshold_for_device("directml") == 50.0
    assert psnr_threshold_for_device("openvino") == 50.0
    assert psnr_threshold_for_device("npu") == 45.0
    assert psnr_threshold_for_device("cuda") == 45.0
    assert psnr_threshold_for_device("mps") == 45.0

    ref = np.full((3, 16, 16), 0.5, dtype=np.float32)
    assert compute_psnr(ref, ref.copy()) == float("inf")
    corrupted = np.zeros_like(ref)
    assert compute_psnr(ref, corrupted) < 10.0


def test_cpu_thread_probe_picks_faster_of_physical_and_logical(monkeypatch):
    from localsr.core import benchmark_v3

    seen_threads: list[int] = []
    current = {"n": 8}

    monkeypatch.setattr(benchmark_v3, "cpu_thread_counts", lambda: (4, 8))
    monkeypatch.setattr(torch, "get_num_threads", lambda: current["n"])
    monkeypatch.setattr(
        torch,
        "set_num_threads",
        lambda n: (current.__setitem__("n", n), seen_threads.append(n)),
    )

    ticks = {"t": 0.0}

    def fake_clock():
        # Physical (4 threads): 0.1s per call; logical (8 threads): 0.3s per call.
        step = 0.1 if current["n"] == 4 else 0.3
        ticks["t"] += step
        return ticks["t"]

    class FakeEngine:
        def process_frame(self, **_kwargs):
            return np.zeros((3, 64, 64), dtype=np.uint8)

    result = benchmark_v3.probe_cpu_threads(
        engine=FakeEngine(),
        model_info=SimpleNamespace(scale=4, architecture="PULSE"),
        model_id="localsr_quick_x4_v1",
        model_name="LocalSR Photo S",
        scene=benchmark_v3.V3_SCENES[0],
        cancel_event=threading.Event(),
        clock=fake_clock,
        probe_seconds=0.5,
        min_iterations=3,
    )
    assert result["physical_cores"] == 4
    assert result["logical_cores"] == 8
    assert result["recommended_threads"] == 4
    assert result["tested"] is True
    assert result["physical_seconds_per_input_mp"] < result["logical_seconds_per_input_mp"]
    assert 4 in seen_threads and 8 in seen_threads


def test_run_benchmark_v3_gates_correctness_and_recommends_cpu_when_igpu_slower(
    monkeypatch, tmp_path
):
    from localsr.core import benchmark_v3
    from localsr.core.model_catalog import CATALOG_BY_ID

    quick = CATALOG_BY_ID["localsr_quick_x4_v1"]
    quick_path = tmp_path / quick.filename
    quick_path.write_bytes(b"fake")

    monkeypatch.setattr(
        benchmark_v3,
        "resolve_benchmark_preset_models",
        lambda **_kw: (
            [
                {
                    "preset": "quick",
                    "preset_label": "Quick",
                    "model": quick,
                    "model_path": str(quick_path),
                }
            ],
            [
                {
                    "preset": "best",
                    "preset_label": "Best Quality",
                    "model_id": "realplksr_nomoswebphoto_x4",
                    "model_name": "RealPLKSR 4x NomosWebPhoto — Best",
                }
            ],
        ),
    )
    monkeypatch.setattr(
        benchmark_v3,
        "enumerate_benchmark_devices",
        lambda *_a: [
            {
                "id": "cpu",
                "type": "cpu",
                "name": "CPU",
                "base_device_id": "cpu",
                "neural_engine": False,
            },
            {
                "id": "directml:0",
                "type": "directml",
                "name": "Intel UHD 620 (DirectML)",
                "base_device_id": "directml:0",
                "neural_engine": False,
            },
            {
                "id": "cuda:0",
                "type": "cuda",
                "name": "Broken GPU (CUDA)",
                "base_device_id": "cuda:0",
                "neural_engine": False,
            },
        ],
    )
    monkeypatch.setattr(benchmark_v3, "cpu_thread_counts", lambda: (4, 8))
    monkeypatch.setattr(benchmark_v3, "_cool_down", lambda *_a, **_kw: "nominal")
    monkeypatch.setattr(benchmark_v3, "_thermal_state", lambda *_a: "nominal")
    monkeypatch.setattr(benchmark_v3, "MEASURE_TARGET_SECONDS", 0.5)
    monkeypatch.setattr(benchmark_v3, "MEASURE_MIN_ITERATIONS", 3)
    monkeypatch.setattr(benchmark_v3, "WARMUP_MAX_SECONDS", 0.5)
    monkeypatch.setattr(benchmark_v3, "WARMUP_MIN_ITERATIONS", 2)

    ticks = {"t": 0.0}
    active_dev = {"id": "cpu"}

    def fake_clock():
        # CPU is faster (0.1s) than DirectML iGPU (0.3s)
        step = 0.3 if active_dev["id"] == "directml:0" else 0.1
        ticks["t"] += step
        return ticks["t"]

    class FakeEngine:
        def __init__(self):
            self.neural_engine_enabled = False
            self.active_backend_id = None
            self.active_model = None

        def load_model(self, _path, device_id, _precision, _info):
            active_dev["id"] = device_id
            self.active_backend_id = device_id
            if device_id == "directml:0":
                self.active_model = SimpleNamespace(
                    execution_provider="DmlExecutionProvider",
                    execution_verified=True,
                    cpu_tiles=0,
                    reset_execution_evidence=lambda: None,
                )
            else:
                self.active_model = SimpleNamespace()
            return torch.device("cpu"), torch.float32

        def process_frame(self, **kwargs):
            tensor = kwargs["img_tensor"]
            h, w = tensor.shape[1], tensor.shape[2]
            if active_dev["id"] == "cuda:0":
                # Fails correctness check completely
                return np.full((3, h * 4, w * 4), 250, dtype=np.uint8)
            return np.full((3, h * 4, w * 4), 120, dtype=np.uint8)

    adapter = SimpleNamespace(
        inspect=lambda _path: SimpleNamespace(
            architecture="PULSE",
            scale=4,
            in_channels=3,
            out_channels=3,
        )
    )

    result = benchmark_v3.run_benchmark_v3(
        engine=FakeEngine(),
        model_adapter=adapter,
        cancel_event=threading.Event(),
        clock=fake_clock,
    )
    payload = result.to_dict()
    assert payload["workload_version"] == "localsr-benchmark-v3"
    assert payload["recommended_device"] == "cpu"
    assert payload["recommended_cpu_threads"] == 4
    assert payload["missing_presets"][0]["preset"] == "best"

    by_device = {entry["device"]: entry for entry in payload["device_results"]}
    assert by_device["cpu"]["matched_cpu"] is True
    assert by_device["cpu"]["recommended"] is True
    assert by_device["directml:0"]["matched_cpu"] is True
    assert (
        by_device["directml:0"]["seconds_per_input_mp"] > by_device["cpu"]["seconds_per_input_mp"]
    )
    assert by_device["cuda:0"]["matched_cpu"] is False
    assert by_device["cuda:0"]["seconds_per_input_mp"] is None
    assert by_device["cuda:0"]["score"] == 0.0
    assert by_device["cuda:0"]["status_note"] == "did not match the CPU"


def test_run_benchmark_v3_cancellation_stops_cleanly(monkeypatch, tmp_path):
    from localsr.core import benchmark_v3
    from localsr.core.model_catalog import CATALOG_BY_ID

    quick = CATALOG_BY_ID["localsr_quick_x4_v1"]
    quick_path = tmp_path / quick.filename
    quick_path.write_bytes(b"fake")

    monkeypatch.setattr(
        benchmark_v3,
        "resolve_benchmark_preset_models",
        lambda **_kw: (
            [
                {
                    "preset": "quick",
                    "preset_label": "Quick",
                    "model": quick,
                    "model_path": str(quick_path),
                }
            ],
            [],
        ),
    )
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(InterruptedError):
        benchmark_v3.run_benchmark_v3(
            engine=SimpleNamespace(neural_engine_enabled=False),
            model_adapter=SimpleNamespace(
                inspect=lambda _p: SimpleNamespace(architecture="PULSE", scale=4)
            ),
            cancel_event=cancel,
        )


def test_worker_run_benchmark_v3_emits_started_and_completed(monkeypatch, tmp_path):
    from localsr.core import benchmark_v3, hardware
    from localsr.core.model_catalog import CATALOG_BY_ID
    from localsr.worker import server as module

    quick = CATALOG_BY_ID["localsr_quick_x4_v1"]
    checkpoint = tmp_path / quick.filename
    checkpoint.write_bytes(b"fake")

    events = []
    monkeypatch.setattr(
        module, "send_message", lambda event: events.append(json.loads(event.to_json()))
    )
    monkeypatch.setattr(
        hardware,
        "get_capability_report",
        lambda: {"devices": [{"id": "cpu", "type": "cpu", "name": "CPU"}]},
    )
    monkeypatch.setattr(
        benchmark_v3,
        "resolve_benchmark_preset_models",
        lambda **_kw: (
            [
                {
                    "preset": "quick",
                    "preset_label": "Quick",
                    "model": quick,
                    "model_path": str(checkpoint),
                }
            ],
            [],
        ),
    )
    monkeypatch.setattr(
        benchmark_v3,
        "run_benchmark_v3",
        lambda **_kw: SimpleNamespace(
            to_dict=lambda: {
                "workload_version": "localsr-benchmark-v3",
                "recommended_device": "cpu",
                "recommended_cpu_threads": 4,
                "device_results": [],
                "score": 1.0,
            }
        ),
    )

    worker = SimpleNamespace(
        cancel_event=threading.Event(),
        engine=SimpleNamespace(neural_engine_enabled=False),
        model_adapter=SimpleNamespace(),
        model_store=SimpleNamespace(path_for=lambda _m: checkpoint, is_installed=lambda _m: True),
    )
    monkeypatch.setattr(
        module.WorkerServer,
        "_verify_benchmark_model",
        staticmethod(lambda *_a: (quick, checkpoint)),
    )
    module.WorkerServer._run_benchmark(
        worker,
        "bench-v3",
        {
            "workload": "v3",
            "models": [
                {
                    "preset": "quick",
                    "model_id": quick.model_id,
                    "model_name": quick.name,
                    "model_path": str(checkpoint),
                }
            ],
        },
    )
    assert events[0]["type"] == "benchmark_started"
    assert events[0]["data"]["workload_version"] == "localsr-benchmark-v3"
    assert events[-1]["type"] == "benchmark_completed"
    assert events[-1]["data"]["result"]["workload_version"] == "localsr-benchmark-v3"


def test_coreml_fallback_and_unsupported_do_not_produce_fake_scores(monkeypatch, tmp_path):
    from localsr.core import benchmark_v3
    from localsr.core.model_catalog import CATALOG_BY_ID

    best = CATALOG_BY_ID["realplksr_nomoswebphoto_x4"]
    best_path = tmp_path / best.filename
    best_path.write_bytes(b"fake")

    monkeypatch.setattr(
        benchmark_v3,
        "resolve_benchmark_preset_models",
        lambda **_kw: (
            [
                {
                    "preset": "best",
                    "preset_label": "Best Quality",
                    "model": best,
                    "model_path": str(best_path),
                }
            ],
            [],
        ),
    )
    monkeypatch.setattr(
        benchmark_v3,
        "enumerate_benchmark_devices",
        lambda *_a: [
            {
                "id": "cpu",
                "type": "cpu",
                "name": "CPU",
                "base_device_id": "cpu",
                "neural_engine": False,
            },
            {
                "id": "neural-engine",
                "type": "coreml",
                "name": "Neural Engine (Core ML)",
                "base_device_id": "mps",
                "neural_engine": True,
            },
        ],
    )
    monkeypatch.setattr(benchmark_v3, "cpu_thread_counts", lambda: (4, 4))
    monkeypatch.setattr(benchmark_v3, "_cool_down", lambda *_a, **_kw: "nominal")
    monkeypatch.setattr(benchmark_v3, "_thermal_state", lambda *_a: "nominal")
    monkeypatch.setattr(
        benchmark_v3, "coreml_units_for", lambda arch: ("CPU_AND_GPU",) if arch == "PLKSR" else ()
    )
    monkeypatch.setattr(benchmark_v3, "MEASURE_TARGET_SECONDS", 0.2)
    monkeypatch.setattr(benchmark_v3, "MEASURE_MIN_ITERATIONS", 2)
    monkeypatch.setattr(benchmark_v3, "WARMUP_MAX_SECONDS", 0.2)
    monkeypatch.setattr(benchmark_v3, "WARMUP_MIN_ITERATIONS", 1)

    ticks = {"t": 0.0}

    def fake_clock():
        ticks["t"] += 0.1
        return ticks["t"]

    class FallbackCoreMLEngine:
        def __init__(self):
            self.neural_engine_enabled = False
            self.active_model = None

        def load_model(self, _path, _device_id, _precision, _info):
            # Core ML wrapper whose active_unit is None (fell back to PyTorch MPS)
            self.active_model = SimpleNamespace(model=SimpleNamespace(active_unit=None))
            return torch.device("cpu"), torch.float32

        def process_frame(self, **kwargs):
            t = kwargs["img_tensor"]
            return np.full((3, t.shape[1] * 4, t.shape[2] * 4), 100, dtype=np.uint8)

    result = benchmark_v3.run_benchmark_v3(
        engine=FallbackCoreMLEngine(),
        model_adapter=SimpleNamespace(
            inspect=lambda _p: SimpleNamespace(architecture="PLKSR", scale=4)
        ),
        cancel_event=threading.Event(),
        clock=fake_clock,
    )
    by_dev = {entry["device"]: entry for entry in result.device_results}
    assert by_dev["cpu"]["matched_cpu"] is True
    assert by_dev["neural-engine"]["status"] == "unavailable"
    assert by_dev["neural-engine"]["seconds_per_input_mp"] is None
    assert result.recommended_device == "cpu"


def test_thread_probe_restores_the_worker_thread_count(monkeypatch):
    import threading

    import torch

    from localsr.core import benchmark_v3

    calls = []
    monkeypatch.setattr(benchmark_v3, "cpu_thread_counts", lambda: (2, 4))
    monkeypatch.setattr(benchmark_v3, "_scene_tensor", lambda scene: torch.zeros(1, 3, 8, 8))
    monkeypatch.setattr(torch, "get_num_threads", lambda: 7)
    monkeypatch.setattr(torch, "set_num_threads", calls.append)

    class Scene:
        width = height = tile_size = 8

    class Engine:
        def process_frame(self, **_kwargs):
            raise RuntimeError("model failed")

    with pytest.raises(RuntimeError, match="model failed"):
        benchmark_v3.probe_cpu_threads(
            engine=Engine(),
            model_info=None,
            model_id="m",
            model_name="M",
            scene=Scene(),
            cancel_event=threading.Event(),
        )
    assert calls == [2, 7]
