"""Tests for the multi-device LocalSR Benchmark v2 workload."""

import json
import math
import threading
from pathlib import Path

import numpy as np
import pytest
import torch

from localsr.core.benchmark_v2 import (
    ENCODE_QUALITY,
    HALO,
    MEASURE_MIN_ITERATIONS,
    MEASURE_MIN_SLOW_ITERATIONS,
    OUTPUT_SCALE,
    PRECISION,
    REFERENCE_SCORES,
    SCENES,
    TARGET_CV,
    WORKLOAD_MODEL_ID,
    WORKLOAD_VERSION,
    SceneSpec,
    StageUpdate,
    _encode_jpeg_ms,
    _reference_comparison,
    _scene_tensor,
    aggregate_v2,
    coefficient_of_variation,
    cpu_scene_score,
    percentile,
    run_device_phase,
    system_score,
)


def test_scene_specs_are_fixed_and_versioned():
    assert WORKLOAD_VERSION == "localsr-benchmark-v2.1"
    assert WORKLOAD_MODEL_ID == "span_photo_x4"
    assert [scene.scene_id for scene in SCENES] == [
        "s1-classroom",
        "s2-gallery",
        "s3-gallery-encode",
    ]
    for scene in SCENES:
        assert scene.width > 0 and scene.height > 0
        assert scene.tile_size >= 64
    # The big scene must exercise the tiled path (more than one tile at 256).
    assert (SCENES[1].width * SCENES[1].height) / (256 * 256) > 1


def test_scene_tensors_are_deterministic_and_bounded():
    for scene in SCENES:
        first = _scene_tensor(scene)
        second = _scene_tensor(scene)
        assert tuple(first.shape) == (3, scene.height, scene.width)
        assert first.equal(second)
        assert float(first.min()) >= 0.0
        assert float(first.max()) <= 1.0


def test_scene_content_has_distinct_frequency_zones():
    scene = SCENES[0]
    array = (_scene_tensor(scene).permute(1, 2, 0).numpy() * 255).astype(np.uint8)
    third = scene.width // 3
    gradient_zone = array[:, :third, 0].astype(np.float32)
    checker_zone = array[:, third : 2 * third, 0].astype(np.float32)
    noise_zone = array[:, 2 * third :, 0].astype(np.float32)

    def horizontal_variation(plane):
        return float(np.abs(np.diff(plane, axis=1)).mean())

    assert horizontal_variation(checker_zone) > horizontal_variation(gradient_zone) * 4
    assert horizontal_variation(noise_zone) > horizontal_variation(gradient_zone)
    # The scene is not flat: a real workload for the model.
    assert array.std() > 20


def test_coefficient_of_variation_bounds():
    assert coefficient_of_variation([]) == float("inf")
    assert coefficient_of_variation([0.5]) == float("inf")
    assert coefficient_of_variation([0.10, 0.10, 0.10, 0.10]) == 0.0
    spread = coefficient_of_variation([0.08, 0.10, 0.12])
    assert 0.0 < spread < 0.2


def test_percentile_matches_v1_definition():
    values = [0.010, 0.020, 0.030, 0.040, 0.050]
    assert percentile(values, 95) == pytest.approx(0.048)
    assert percentile(values, 5) == pytest.approx(0.012)
    assert percentile(values, 50) == pytest.approx(0.030)


def test_scores_separate_cpu_and_accelerators():
    devices = [
        {
            "device_type": "mps",
            "scenes": [
                {"megapixels_per_second": 2.0},
                {"megapixels_per_second": 1.0},
            ],
        },
        {"device_type": "cpu", "scenes": [{"megapixels_per_second": 0.09}]},
    ]
    expected_gpu = math.sqrt(2.0 * 1.0)
    assert system_score(devices) == pytest.approx(expected_gpu, abs=0.01)
    assert cpu_scene_score(devices) == pytest.approx(0.09, abs=0.001)
    # CPU-only runs produce no headline system score.
    assert system_score([devices[1]]) is None
    assert cpu_scene_score([devices[0]]) is None

    # A second, slower adapter must not reduce the system headline score.
    devices.insert(
        1,
        {
            "device_type": "cuda",
            "score": 0.5,
            "scenes": [{"megapixels_per_second": 0.5}],
        },
    )
    assert system_score(devices) == pytest.approx(expected_gpu, abs=0.01)


def test_aggregate_v2_gates_unstable_runs():
    stable_devices = [
        {
            "device_type": "mps",
            "device": "mps",
            "device_name": "Apple GPU",
            "thermal_state": "nominal",
            "warmup_iterations": 4,
            "peak_memory_bytes": 100,
            "peak_device_memory_bytes": 200,
            "stable": True,
            "cv_percent": 3.0,
            "scenes": [{"megapixels_per_second": 1.44}],
        },
        {
            "device_type": "cpu",
            "device": "cpu",
            "device_name": "CPU",
            "thermal_state": "nominal",
            "warmup_iterations": 3,
            "peak_memory_bytes": 80,
            "peak_device_memory_bytes": None,
            "stable": True,
            "cv_percent": 4.0,
            "scenes": [{"megapixels_per_second": 0.06}],
        },
    ]
    result = aggregate_v2(stable_devices, 123.4)
    assert result.stable
    assert result.cv_percent == 4.0
    assert result.system_score == pytest.approx(1.44, abs=0.01)
    assert result.cpu_score == pytest.approx(0.06, abs=0.001)

    payload = result.to_dict()
    # v1 compatibility fields must exist for the Rust struct.
    for key in (
        "backend",
        "device",
        "model_id",
        "model_name",
        "scale",
        "input_width",
        "input_height",
        "warmup_count",
        "measured_frame_count",
        "median_inference_ms",
        "p95_inference_ms",
        "end_to_end_fps",
        "processed_megapixels_per_second",
        "total_elapsed_seconds",
        "peak_memory_bytes",
        "score",
    ):
        assert key in payload
    assert payload["workload_version"] == WORKLOAD_VERSION
    assert payload["score"] == pytest.approx(1.44, abs=0.01)

    unstable = [dict(device, stable=False) for device in stable_devices]
    unstable_result = aggregate_v2(unstable, 99.0)
    assert not unstable_result.stable
    assert unstable_result.system_score is None
    assert unstable_result.cpu_score is None
    assert unstable_result.reference_label is None
    assert unstable_result.to_dict()["score"] == 0.0


def test_reference_comparison_prefers_closest_faster_reference():
    references = (("slow", 1.3), ("fast", 2.1))
    label, ratio = _reference_comparison(1.44, references)
    assert label == "slow"
    assert ratio == pytest.approx(1.11, abs=0.01)

    label, ratio = _reference_comparison(0.05, references)
    assert label is not None and ratio is not None
    assert ratio < 1.0

    assert _reference_comparison(None) == (None, None)
    # Bundled entries, when present, must be physical positive measurements.
    assert all(value > 0 for _label, value in REFERENCE_SCORES)


def test_bundled_references_have_physical_run_provenance():
    path = Path(__file__).parents[1] / "src/localsr/core/benchmark_references.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    # Historical measurements keep the workload they actually ran. The changed
    # v2.1 input must not inherit a score from the original v2 scene.
    assert payload["workload_version"] == "localsr-benchmark-v2"
    assert payload["workload_version"] != WORKLOAD_VERSION
    assert payload["metric"] == "output_megapixels_per_second"
    assert not REFERENCE_SCORES
    for entry in payload["entries"]:
        assert entry["source"] == "physical-local-run"
        assert entry["sample_count"] == len(entry["samples"])
        assert entry["sample_count"] >= 2
        assert entry["maximum_cv_percent"] <= TARGET_CV * 100


def test_encode_stage_produces_real_jpeg_output():
    array = np.full((3, 64, 64), 128, dtype=np.uint8)
    elapsed = _encode_jpeg_ms(array)
    assert elapsed >= 0.0


class _FakeEngine:
    """Records load_model/process_frame calls without touching torch devices."""

    def __init__(self):
        self.loaded = None
        self.calls = 0

    def load_model(self, path, device, precision, info):
        self.loaded = (path, device, precision, info)
        return torch.device("cpu"), torch.float32

    def process_frame(self, **kwargs):
        self.calls += 1
        tensor = kwargs["img_tensor"]
        scale = (
            kwargs.get("model_info").scale
            if hasattr(kwargs.get("model_info"), "scale")
            else OUTPUT_SCALE
        )
        return np.zeros((3, tensor.shape[1] * scale, tensor.shape[2] * scale), dtype=np.uint8)


class _FakeModelInfo:
    scale = OUTPUT_SCALE


def test_run_device_phase_warms_up_and_measures_all_scenes():
    engine = _FakeEngine()
    cancel = threading.Event()
    # Deterministic clock: warmup loop is bounded by WARMUP_MAX_SECONDS and
    # the measure loop by MEASURE_MIN_ITERATIONS once time passes the floor.
    ticks = {"value": 0.0}

    def clock():
        ticks["value"] += 0.5
        return ticks["value"]

    scenes = (
        SceneSpec("test-compute", 16, 16, 16, "compute"),
        SceneSpec("test-tiled", 32, 16, 16, "tiled-end-to-end"),
    )
    stages: list[StageUpdate] = []
    previews = []

    def capture(preview):
        previews.append(preview)
        # Even expensive preview transport is outside the measured iterations.
        ticks["value"] += 1000

    result = run_device_phase(
        engine=engine,
        model_info=_FakeModelInfo(),
        model_path="/models/quick.pth",
        device_id="cpu",
        device_name="CPU",
        device_type="cpu",
        scenes=scenes,
        cancel_event=cancel,
        progress_callback=stages.append,
        clock=clock,
        preview_callback=capture,
    )
    assert result.device == "cpu"
    assert result.warmup_iterations >= 1
    assert len(result.scenes) == 2
    assert result.scenes[0]["scene_id"] == "test-compute"
    assert result.scenes[0]["iterations"] >= MEASURE_MIN_ITERATIONS
    assert result.scenes[0]["median_ms"] >= 0.0
    assert result.stable  # the fake clock has zero spread
    expected_output_mps = 16 * 16 * OUTPUT_SCALE**2 / 1_000_000 / 0.5
    assert result.scenes[0]["megapixels_per_second"] == pytest.approx(
        expected_output_mps, abs=0.0001
    )
    assert result.score > 0
    assert len(previews) == 2
    assert result.scenes[0]["preview"] == previews[0]
    import base64
    import io

    from PIL import Image

    captured = Image.open(
        io.BytesIO(base64.b64decode(previews[0]["output_data_url"].split(",", 1)[1]))
    )
    assert captured.size == (64, 64)
    assert np.asarray(captured).max() == 0  # the engine's actual black output
    assert engine.loaded[1] == "cpu"
    assert engine.loaded[2] == PRECISION
    assert result.peak_memory_bytes is None or result.peak_memory_bytes >= 0
    assert stages[0].event == "started"
    assert stages[0].stage == "cpu:warmup"
    assert stages[-1].event == "completed"
    assert stages[-1].stage == "cpu:cooldown"


def test_empty_aggregate_is_unstable_and_safe():
    result = aggregate_v2([], 0.1)
    assert not result.stable
    assert result.system_score is None
    assert result.cpu_score is None
    assert result.thermal_state == "unknown"


def test_slow_device_collects_enough_samples_for_consistency_and_strict_json():
    from localsr.protocol.messages import BenchmarkCompleted

    ticks = iter(range(0, 100_000, 60))
    phase = run_device_phase(
        engine=_FakeEngine(),
        model_info=_FakeModelInfo(),
        model_path="test.pth",
        device_id="cpu",
        device_name="Slow CPU",
        device_type="cpu",
        scenes=(SceneSpec("test-slow", 256, 256, 256, "compute"),),
        cancel_event=threading.Event(),
        progress_callback=None,
        clock=lambda: next(ticks),
    )
    assert phase.scenes[0]["iterations"] == MEASURE_MIN_SLOW_ITERATIONS == 3
    assert phase.scenes[0]["cv_percent"] == 0
    assert phase.cv_percent == 0 and phase.stable
    payload = aggregate_v2([phase.to_dict()], 600).to_dict()
    message = BenchmarkCompleted(job_id="slow-device", result=payload).to_json()
    decoded = json.loads(
        message, parse_constant=lambda value: pytest.fail(f"Invalid JSON: {value}")
    )
    assert decoded["type"] == "benchmark_completed"
    assert decoded["data"]["result"]["cv_percent"] == 0
    assert decoded["data"]["result"]["cpu_score"] > 0
    assert decoded["data"]["result"]["device_results"][0]["scenes"][0]["median_ms"] > 0
    with pytest.raises(ValueError, match="JSON compliant"):
        BenchmarkCompleted(job_id="invalid", result={"score": float("inf")}).to_json()


def test_run_device_phase_stops_on_cancel():
    engine = _FakeEngine()
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(InterruptedError):
        run_device_phase(
            engine=engine,
            model_info=_FakeModelInfo(),
            model_path="/models/quick.pth",
            device_id="cpu",
            device_name="CPU",
            device_type="cpu",
            scenes=SCENES[:1],
            cancel_event=cancel,
            progress_callback=None,
        )


def test_v2_constants_match_production_settings():
    assert HALO == 16
    assert PRECISION == "fp32"
    assert ENCODE_QUALITY == 90
    assert TARGET_CV == 0.05


@pytest.mark.parametrize("requested", ["cpu", "mps"])
def test_worker_runs_only_requested_device_and_never_falls_back(tmp_path, monkeypatch, requested):
    from types import SimpleNamespace

    from localsr.core import benchmark_v2 as core
    from localsr.core import hardware
    from localsr.worker import server as module

    checkpoint = tmp_path / "trusted.pth"
    checkpoint.write_bytes(b"test checkpoint")
    events, calls = [], []
    monkeypatch.setattr(
        module, "send_message", lambda event: events.append(json.loads(event.to_json()))
    )
    monkeypatch.setattr(
        hardware,
        "get_capability_report",
        lambda: {"devices": [{"id": "cpu", "type": "cpu"}, {"id": "mps", "type": "mps"}]},
    )

    def run(**kwargs):
        calls.append(kwargs["device_id"])
        return SimpleNamespace(
            to_dict=lambda: {
                "device": kwargs["device_id"],
                "device_type": kwargs["device_type"],
                "score": 3.5,
                "stable": True,
                "cv_percent": 1,
                "scenes": [],
            }
        )

    monkeypatch.setattr(core, "run_device_phase", run)
    worker = SimpleNamespace(
        cancel_event=threading.Event(),
        engine=object(),
        model_adapter=SimpleNamespace(inspect=lambda _: object()),
        model_store=SimpleNamespace(path_for=lambda _: checkpoint, is_installed=lambda _: True),
    )
    data = {"device": requested, "model_id": core.WORKLOAD_MODEL_ID, "model_path": str(checkpoint)}
    module.WorkerServer._run_benchmark_v2(worker, "test-benchmark", data)
    assert calls == [requested]
    result = events[-1]["data"]["result"]
    assert [device["device"] for device in result["device_results"]] == [requested]
    assert result["cpu_score" if requested == "cpu" else "system_score"] == 3.5
    assert result["system_score" if requested == "cpu" else "cpu_score"] is None
    calls.clear()
    with pytest.raises(ValueError, match="unavailable"):
        module.WorkerServer._run_benchmark_v2(worker, "test", {**data, "device": "cuda:9"})
    assert calls == []


def test_render_tiles_are_only_requested_for_unmeasured_warmup():
    from types import SimpleNamespace

    from localsr.core.benchmark_v2 import run_scene

    calls, pixels = [], []

    def process(**kwargs):
        calls.append(kwargs)
        output = np.full((3, 32, 32), 127, np.uint8)
        if kwargs.get("tile_callback"):
            kwargs["tile_callback"]("completed", None, output, 1, 1, 32, 32, 8)
        return output

    scene = SceneSpec("test", 8, 8, 8, "compute")
    arguments = dict(
        engine=SimpleNamespace(process_frame=process),
        model_info=object(),
        scene=scene,
        device=torch.device("cpu"),
        cancel_event=threading.Event(),
        tile_callback=lambda *args: pixels.append(args[2]),
    )
    run_scene(**arguments, warm=True)
    run_scene(**arguments, warm=False)
    assert "tile_callback" in calls[0] and "tile_callback" not in calls[1]
    assert len(pixels) == 1 and np.all(pixels[0] == 127)
