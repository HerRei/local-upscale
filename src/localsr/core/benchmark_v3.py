"""LocalSR Benchmark v3 — multi-device, preset-model benchmark with correctness gating.

Measures every available processor (CPU, iGPU via DirectML / OpenVINO, Intel NPU,
Apple Neural Engine via Core ML, MPS, CUDA/ROCm/XPU) in one run against the
downloaded Quick and Best Quality preset models. Each accelerator's output is
checked against the CPU reference before its speed counts, CPU physical vs
logical thread counts are timed, and pre-AVX2 CPUs run in NCHW.
"""

from __future__ import annotations

import math
import os
import statistics
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Callable, Literal

import numpy as np
import psutil
import torch

from .benchmark import percentile
from .benchmark_v2 import (
    PROGRESS_INTERVAL_SECONDS,
    TARGET_CV,
    SceneResult,
    SceneSpec,
    StageUpdate,
    _cool_down,
    _geometric_mean,
    _PeakMemorySampler,
    _scene_tensor,
    _synchronize,
    _thermal_state,
    coefficient_of_variation,
)
from .coreml_runtime import coreml_units_for
from .hardware import cpu_memory_format, cpu_supports_avx2, get_capability_report
from .inference import InferenceEngine
from .model_catalog import MODEL_CATALOG, ModelPurpose, ModelStore
from .presets import PresetMode, select_model_for_preset

WORKLOAD_VERSION = "localsr-benchmark-v3"
PRECISION = "fp32"
HALO = 16
FALLBACK_PSNR_DB = 45.0

# Smaller than v2's 3072x2048 so multi-device + multi-model runs finish even on
# slow CPUs (e.g. pre-AVX2 laptops) while still exercising both single-tile
# compute (256x256) and 4-tile halo compositing with non-square edge tiles.
V3_SCENES: tuple[SceneSpec, ...] = (
    SceneSpec("s1-classroom", 256, 256, 256, "compute"),
    SceneSpec("s2-gallery", 512, 384, 256, "tiled-end-to-end"),
)

WARMUP_MIN_ITERATIONS = 2
WARMUP_MAX_SECONDS = 15.0
MEASURE_TARGET_SECONDS = 10.0
MEASURE_MIN_ITERATIONS = 5
MEASURE_MAX_SECONDS = 25.0
MEASURE_MIN_SLOW_ITERATIONS = 2
THREAD_PROBE_SECONDS = 8.0
THREAD_PROBE_MIN_ITERATIONS = 3
NEURAL_ENGINE_DEVICE_ID = "neural-engine"


def compute_psnr(first: np.ndarray, second: np.ndarray) -> float:
    """PSNR in dB for normalized [0, 1] float arrays."""
    error = float(np.mean((np.clip(first, 0, 1) - np.clip(second, 0, 1)) ** 2))
    return math.inf if error == 0 else 10.0 * math.log10(1.0 / error)


def psnr_threshold_for_device(device_type: str) -> float:
    """Reuse the backend's accuracy gate threshold when present, else 45 dB."""
    kind = device_type.lower()
    if kind == "coreml":
        try:
            from .coreml_runtime import MINIMUM_PSNR as COREML_MIN_PSNR

            return float(COREML_MIN_PSNR)
        except ImportError:
            return FALLBACK_PSNR_DB
    if kind in {"directml", "openvino"}:
        try:
            from .onnx_runtime import MINIMUM_PSNR as ONNX_MIN_PSNR

            return float(ONNX_MIN_PSNR)
        except ImportError:
            return 50.0
    if kind == "npu":
        try:
            from .onnx_runtime import NPU_MINIMUM_PSNR

            return float(NPU_MINIMUM_PSNR)
        except ImportError:
            return FALLBACK_PSNR_DB
    return FALLBACK_PSNR_DB


def cpu_thread_counts() -> tuple[int, int]:
    """Return (physical_cores, logical_cores), both >= 1."""
    logical = psutil.cpu_count(logical=True) or os.cpu_count() or 1
    physical = psutil.cpu_count(logical=False) or logical
    logical = max(1, int(logical))
    physical = max(1, min(int(physical), logical))
    return physical, logical


def resolve_benchmark_preset_models(
    catalog=MODEL_CATALOG,
    store: ModelStore | None = None,
) -> tuple[list[dict], list[dict]]:
    """Return (available, missing) preset models for Quick and Best Quality.

    Only already-downloaded models take part; no download is triggered.
    """
    model_store = store or ModelStore()
    quick = select_model_for_preset(catalog, PresetMode.QUICK_UPSCALE, purpose=ModelPurpose.PHOTO)
    best = select_model_for_preset(catalog, PresetMode.BEST_UPSCALE, purpose=ModelPurpose.PHOTO)

    candidates = [("quick", "Quick", quick)]
    if best.model_id != quick.model_id:
        candidates.append(("best", "Best Quality", best))

    available: list[dict] = []
    missing: list[dict] = []
    for preset_key, preset_label, model in candidates:
        if model_store.is_installed(model):
            available.append(
                {
                    "preset": preset_key,
                    "preset_label": preset_label,
                    "model": model,
                    "model_path": str(model_store.path_for(model)),
                }
            )
        else:
            missing.append(
                {
                    "preset": preset_key,
                    "preset_label": preset_label,
                    "model_id": model.model_id,
                    "model_name": model.name,
                }
            )
    return available, missing


def enumerate_benchmark_devices(capabilities: dict | None = None) -> list[dict]:
    """Enumerate every testable device for this machine, with CPU first."""
    report = capabilities if capabilities is not None else get_capability_report()
    raw_devices = list(report.get("devices", []))
    entries: list[dict] = []
    for dev in raw_devices:
        dev_id = str(dev["id"])
        dev_type = str(dev.get("type") or dev_id)
        dev_name = str(dev.get("name") or dev_id)
        if dev_id == "mps" and dev.get("neural_engine"):
            entries.append(
                {
                    "id": NEURAL_ENGINE_DEVICE_ID,
                    "type": "coreml",
                    "name": "Neural Engine (Core ML)",
                    "base_device_id": "mps",
                    "neural_engine": True,
                }
            )
        entries.append(
            {
                "id": dev_id,
                "type": dev_type,
                "name": dev_name,
                "base_device_id": dev_id,
                "neural_engine": False,
            }
        )
    # Run CPU first so its reference output and optimal thread count are ready
    # before evaluating accelerators.
    entries.sort(key=lambda item: (0 if item["type"] == "cpu" else 1,))
    return entries


def probe_cpu_threads(
    *,
    engine: InferenceEngine,
    model_info: object,
    model_id: str,
    model_name: str,
    scene: SceneSpec,
    cancel_event: threading.Event,
    clock: Callable[[], float] = time.perf_counter,
    probe_seconds: float = THREAD_PROBE_SECONDS,
    min_iterations: int = THREAD_PROBE_MIN_ITERATIONS,
) -> dict:
    """Compare physical core count vs all logical cores on CPU and pick the faster."""
    physical, logical = cpu_thread_counts()
    if physical >= logical:
        return {
            "physical_cores": physical,
            "logical_cores": logical,
            "recommended_threads": logical,
            "tested": False,
            "physical_seconds_per_input_mp": None,
            "logical_seconds_per_input_mp": None,
            "model_id": model_id,
            "model_name": model_name,
        }

    scene_tensor = _scene_tensor(scene).contiguous(memory_format=cpu_memory_format())
    input_mp = (scene.width * scene.height) / 1_000_000.0
    results_sec: dict[int, float] = {}
    # The worker is long-lived: later jobs must keep the thread count they had before.
    original_threads = torch.get_num_threads()
    try:
        for thread_count in (physical, logical):
            if cancel_event.is_set():
                raise InterruptedError("benchmark cancelled")
            torch.set_num_threads(thread_count)
            timings: list[float] = []
            started_phase = clock()
            # One untimed warm pass after changing thread pool size.
            engine.process_frame(
                img_tensor=scene_tensor,
                model_info=model_info,
                tile_size=scene.tile_size,
                halo=HALO,
                cancel_event=cancel_event,
                progress_callback=lambda *_args: None,
                safe_memory=False,
                device=torch.device("cpu"),
            )
            while len(timings) < min_iterations or (clock() - started_phase) < probe_seconds:
                if cancel_event.is_set():
                    raise InterruptedError("benchmark cancelled")
                t0 = clock()
                engine.process_frame(
                    img_tensor=scene_tensor,
                    model_info=model_info,
                    tile_size=scene.tile_size,
                    halo=HALO,
                    cancel_event=cancel_event,
                    progress_callback=lambda *_args: None,
                    safe_memory=False,
                    device=torch.device("cpu"),
                )
                timings.append(max(1e-9, clock() - t0))
            results_sec[thread_count] = statistics.median(timings) / input_mp
    finally:
        torch.set_num_threads(original_threads)

    recommended = physical if results_sec[physical] <= results_sec[logical] else logical
    return {
        "physical_cores": physical,
        "logical_cores": logical,
        "recommended_threads": recommended,
        "tested": True,
        "physical_seconds_per_input_mp": round(results_sec[physical], 3),
        "logical_seconds_per_input_mp": round(results_sec[logical], 3),
        "model_id": model_id,
        "model_name": model_name,
    }


@dataclass(frozen=True)
class V3DeviceResult:
    device: str
    device_type: str
    device_name: str
    preset: str
    model_id: str
    model_name: str
    thermal_state: str
    warmup_iterations: int
    peak_memory_bytes: int | None
    peak_device_memory_bytes: int | None
    stable: bool
    cv_percent: float | None
    score: float
    seconds_per_input_mp: float | None
    matched_cpu: bool | None
    psnr_db: float | None
    status: str
    status_note: str | None = None
    recommended: bool = False
    scenes: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class BenchmarkV3Result:
    workload_version: str
    device_results: list[dict]
    benchmarked_models: list[dict]
    missing_presets: list[dict]
    recommended_device: str | None
    recommended_device_name: str | None
    recommended_device_seconds_per_input_mp: float | None
    recommended_cpu_threads: int
    cpu_threads: dict
    cpu_avx2: bool
    cpu_memory_format: str
    system_score: float | None
    cpu_score: float | None
    stable: bool
    cv_percent: float | None
    elapsed_seconds: float
    thermal_state: str
    model_id: str
    model_name: str
    scale: int = 4

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        valid_results = [
            d
            for d in self.device_results
            if d.get("matched_cpu") is True and d.get("seconds_per_input_mp") is not None
        ]
        headline = next(
            (d for d in valid_results if d.get("recommended")),
            valid_results[0] if valid_results else None,
        )
        headline_scene = headline["scenes"][0] if headline and headline.get("scenes") else {}
        total_iterations = sum(
            int(scene.get("iterations", 0))
            for device in self.device_results
            for scene in device.get("scenes", [])
        )
        median_ms = float(headline_scene.get("median_ms", 0.0))
        p95_ms = float(headline_scene.get("p95_ms", 0.0))
        fps = 1000.0 / median_ms if median_ms > 0 else 0.0
        payload.update(
            {
                "backend": str(headline.get("device_type", "cpu")) if headline else "cpu",
                "device": str(headline.get("device", "cpu")) if headline else "cpu",
                "input_width": int(headline_scene.get("input_width", V3_SCENES[0].width)),
                "input_height": int(headline_scene.get("input_height", V3_SCENES[0].height)),
                "warmup_count": int(headline.get("warmup_iterations", 0)) if headline else 0,
                "measured_frame_count": total_iterations,
                "median_inference_ms": median_ms,
                "p95_inference_ms": p95_ms,
                "end_to_end_fps": round(fps, 4),
                "processed_megapixels_per_second": float(
                    headline_scene.get("megapixels_per_second", 0.0)
                ),
                "total_elapsed_seconds": self.elapsed_seconds,
                "result_elapsed_seconds": self.elapsed_seconds,
                "peak_memory_bytes": headline.get("peak_memory_bytes") if headline else None,
                "score": float(headline.get("score", 0.0)) if headline else 0.0,
            }
        )
        return payload


def _verify_backend_execution(
    engine: InferenceEngine, device_entry: dict
) -> tuple[bool, str | None]:
    """Ensure ONNX/Core ML routes actually executed on the accelerator."""
    dev_type = device_entry["type"]
    if dev_type == "coreml":
        coreml_inner = getattr(engine.active_model, "model", None)
        if getattr(coreml_inner, "active_unit", None) is None:
            return False, "Core ML fell back to PyTorch"
        return True, None
    if dev_type in {"directml", "openvino", "npu"}:
        active_model = getattr(engine, "active_model", None)
        expected_provider = (
            "DmlExecutionProvider" if dev_type == "directml" else "OpenVINOExecutionProvider"
        )
        if getattr(active_model, "execution_provider", None) == expected_provider:
            if (
                not getattr(active_model, "execution_verified", False)
                or getattr(active_model, "cpu_tiles", 0) > 0
            ):
                return False, "did not verify GPU/NPU execution"
    return True, None


def _run_v3_device_model(
    *,
    engine: InferenceEngine,
    model_info: object,
    model_entry: dict,
    device_entry: dict,
    cpu_reference: np.ndarray | None,
    stage_offset: int,
    stage_count: int,
    cancel_event: threading.Event,
    progress_callback: Callable[[StageUpdate], None] | None,
    preview_callback: Callable[[dict], None] | None,
    tile_callback: Callable[..., None] | None,
    clock: Callable[[], float],
) -> tuple[V3DeviceResult, np.ndarray | None]:
    """Check correctness against CPU, then measure all v3 scenes for one (model, device)."""
    model = model_entry["model"]
    model_path = model_entry["model_path"]
    preset = model_entry["preset"]
    scale = int(getattr(model_info, "scale", 4))
    arch = str(getattr(model_info, "architecture", ""))
    device_id = device_entry["id"]
    device_type = device_entry["type"]
    device_name = device_entry["name"]
    base_device_id = device_entry["base_device_id"]

    def make_failed(
        *,
        status: str,
        note: str,
        matched_cpu: bool | None = None,
        psnr_db: float | None = None,
        thermal: str = "unknown",
    ) -> V3DeviceResult:
        return V3DeviceResult(
            device=device_id,
            device_type=device_type,
            device_name=device_name,
            preset=preset,
            model_id=model.model_id,
            model_name=model.name,
            thermal_state=thermal,
            warmup_iterations=0,
            peak_memory_bytes=None,
            peak_device_memory_bytes=None,
            stable=False,
            cv_percent=None,
            score=0.0,
            seconds_per_input_mp=None,
            matched_cpu=matched_cpu,
            psnr_db=psnr_db,
            status=status,
            status_note=note,
            recommended=False,
            scenes=[],
        )

    def emit(
        event: Literal["started", "progress", "completed"],
        stage: str,
        local_index: int,
        *,
        completed_units: int = 0,
        total_units: int = 0,
        fraction: float = 0.0,
    ) -> None:
        if progress_callback is not None:
            progress_callback(
                StageUpdate(
                    event=event,
                    stage=f"{preset}:{device_id}:{stage}",
                    stage_index=stage_offset + local_index,
                    stage_count=stage_count,
                    completed_units=completed_units,
                    total_units=total_units,
                    fraction=max(0.0, min(1.0, fraction)),
                )
            )

    if device_type == "coreml" and not coreml_units_for(arch):
        return (
            make_failed(
                status="unsupported",
                note=f"{arch or 'This model'} is not supported on Core ML.",
            ),
            cpu_reference,
        )

    if device_type == "npu":
        try:
            from .onnx_runtime import NPU_ARCHITECTURES

            if arch not in NPU_ARCHITECTURES:
                return (
                    make_failed(
                        status="unsupported",
                        note=f"{arch or 'This model'} is not supported on the NPU.",
                    ),
                    cpu_reference,
                )
        except ImportError:
            pass

    if cancel_event.is_set():
        raise InterruptedError("benchmark cancelled")

    engine.neural_engine_enabled = bool(device_entry["neural_engine"])
    thermal_before = _thermal_state(base_device_id)
    fmt = cpu_memory_format() if device_type == "cpu" else torch.contiguous_format

    try:
        torch_device, _ = engine.load_model(model_path, base_device_id, PRECISION, model_info)
    except InterruptedError:
        raise
    except Exception as error:  # noqa: BLE001
        return (
            make_failed(status="unavailable", note=str(error), thermal=thermal_before),
            cpu_reference,
        )

    if device_type in {"directml", "openvino", "npu"}:
        reset_ev = getattr(engine.active_model, "reset_execution_evidence", None)
        if callable(reset_ev):
            reset_ev()

    # Correctness check on V3_SCENES[0] before spending time warming up or measuring.
    check_scene = V3_SCENES[0]
    check_tensor = _scene_tensor(check_scene).contiguous(memory_format=fmt)
    emit("started", "warmup", 0)
    try:
        if cancel_event.is_set():
            raise InterruptedError("benchmark cancelled")
        t_check0 = clock()
        check_output = engine.process_frame(
            img_tensor=check_tensor,
            model_info=model_info,
            tile_size=check_scene.tile_size,
            halo=HALO,
            cancel_event=cancel_event,
            progress_callback=lambda *_args: None,
            safe_memory=False,
            device=torch_device,
        )
        _synchronize(torch_device)
        first_warm_elapsed = max(1e-9, clock() - t_check0)
    except InterruptedError:
        raise
    except Exception as error:  # noqa: BLE001
        note = str(error)
        if "GpuRouteRejected" in type(error).__name__ or "different results" in note.lower():
            return (
                make_failed(
                    status="mismatch",
                    note="did not match the CPU",
                    matched_cpu=False,
                    thermal=thermal_before,
                ),
                cpu_reference,
            )
        return (
            make_failed(status="error", note=note, thermal=thermal_before),
            cpu_reference,
        )

    verified, verify_note = _verify_backend_execution(engine, device_entry)
    if not verified:
        return (
            make_failed(
                status="unavailable",
                note=verify_note or "did not run on the selected hardware",
                thermal=thermal_before,
            ),
            cpu_reference,
        )

    candidate_float = check_output.astype(np.float32) / 255.0
    psnr_db: float | None = None
    if device_type == "cpu":
        cpu_reference = candidate_float
        matched_cpu: bool | None = True
    elif cpu_reference is not None:
        if candidate_float.shape != cpu_reference.shape or not np.isfinite(candidate_float).all():
            return (
                make_failed(
                    status="mismatch",
                    note="did not match the CPU",
                    matched_cpu=False,
                    thermal=thermal_before,
                ),
                cpu_reference,
            )
        psnr_val = compute_psnr(candidate_float, cpu_reference)
        threshold = psnr_threshold_for_device(device_type)
        if not math.isfinite(psnr_val) and psnr_val != math.inf:
            return (
                make_failed(
                    status="mismatch",
                    note="did not match the CPU",
                    matched_cpu=False,
                    thermal=thermal_before,
                ),
                cpu_reference,
            )
        if psnr_val < threshold:
            return (
                make_failed(
                    status="mismatch",
                    note="did not match the CPU",
                    matched_cpu=False,
                    psnr_db=round(psnr_val, 2),
                    thermal=thermal_before,
                ),
                cpu_reference,
            )
        psnr_db = None if math.isinf(psnr_val) else round(psnr_val, 2)
        matched_cpu = True
    else:
        matched_cpu = None

    # Continue warm-up after the initial check inference.
    warm_timings = [first_warm_elapsed]
    warmup_iterations = 1
    warm_started = clock() - first_warm_elapsed
    recent_cv: list[float] = []
    last_progress_at = 0.0
    while clock() - warm_started < WARMUP_MAX_SECONDS:
        if cancel_event.is_set():
            raise InterruptedError("benchmark cancelled")
        if warmup_iterations >= WARMUP_MIN_ITERATIONS and len(warm_timings) >= 4:
            recent_cv.append(coefficient_of_variation(warm_timings[-4:]))
            if len(recent_cv) >= 3 and all(cv < TARGET_CV for cv in recent_cv[-3:]):
                break
        started = clock()
        engine.process_frame(
            img_tensor=check_tensor,
            model_info=model_info,
            tile_size=check_scene.tile_size,
            halo=HALO,
            cancel_event=cancel_event,
            progress_callback=lambda *_args: None,
            safe_memory=False,
            device=torch_device,
        )
        _synchronize(torch_device)
        warm_timings.append(max(1e-9, clock() - started))
        warmup_iterations += 1
        now = time.monotonic()
        if now - last_progress_at >= PROGRESS_INTERVAL_SECONDS:
            emit(
                "progress",
                "warmup",
                0,
                completed_units=warmup_iterations,
                fraction=min(0.99, (clock() - warm_started) / WARMUP_MAX_SECONDS),
            )
            last_progress_at = now

    emit("completed", "warmup", 0, completed_units=warmup_iterations, fraction=1.0)

    if device_type in {"directml", "openvino", "npu"}:
        reset_ev = getattr(engine.active_model, "reset_execution_evidence", None)
        if callable(reset_ev):
            reset_ev()

    scene_results: list[dict] = []
    all_cvs: list[float] = []
    with _PeakMemorySampler(torch_device) as memory:
        for scene_index, scene in enumerate(V3_SCENES, start=1):
            if cancel_event.is_set():
                raise InterruptedError("benchmark cancelled")
            emit("started", scene.scene_id, scene_index)
            scene_tensor = _scene_tensor(scene).contiguous(memory_format=fmt)

            # Untimed warm-up render for live preview tiles.
            if cancel_event.is_set():
                raise InterruptedError("benchmark cancelled")
            warm_out = engine.process_frame(
                img_tensor=scene_tensor,
                model_info=model_info,
                tile_size=scene.tile_size,
                halo=HALO,
                cancel_event=cancel_event,
                progress_callback=lambda *_args: None,
                safe_memory=False,
                device=torch_device,
                **(
                    {
                        "tile_callback": (
                            lambda *args, scene=scene: tile_callback(
                                model_entry, device_entry, scene, scale, *args
                            )
                        )
                    }
                    if tile_callback
                    else {}
                ),
            )
            _synchronize(torch_device)
            preview = None
            if preview_callback is not None:
                import base64

                from .video_io import thumbnail_jpeg, uint8_chw_to_rgb_hwc

                source = (scene_tensor * 255).round().byte().numpy()

                def data_url(array):
                    jpeg = thumbnail_jpeg(
                        uint8_chw_to_rgb_hwc(array), max_dimension=640, quality=90
                    )
                    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")

                preview = {
                    "device": device_id,
                    "scene_id": scene.scene_id,
                    "model_name": model.name,
                    "input_width": scene.width,
                    "input_height": scene.height,
                    "output_width": int(warm_out.shape[2]),
                    "output_height": int(warm_out.shape[1]),
                    "input_data_url": data_url(source),
                    "output_data_url": data_url(warm_out),
                }
                preview_callback(preview)

            timings: list[float] = []
            measured_started = clock()
            iterations = 0
            last_progress_at = 0.0
            while True:
                if cancel_event.is_set():
                    raise InterruptedError("benchmark cancelled")
                _synchronize(torch_device)
                t0 = clock()
                engine.process_frame(
                    img_tensor=scene_tensor,
                    model_info=model_info,
                    tile_size=scene.tile_size,
                    halo=HALO,
                    cancel_event=cancel_event,
                    progress_callback=lambda *_args: None,
                    safe_memory=False,
                    device=torch_device,
                )
                _synchronize(torch_device)
                timings.append(max(1e-9, clock() - t0))
                iterations += 1
                elapsed = clock() - measured_started
                minimum_iterations = (
                    MEASURE_MIN_SLOW_ITERATIONS
                    if elapsed >= MEASURE_MAX_SECONDS
                    else MEASURE_MIN_ITERATIONS
                )
                required_fraction = min(
                    elapsed / MEASURE_TARGET_SECONDS,
                    iterations / minimum_iterations,
                )
                now = time.monotonic()
                if now - last_progress_at >= PROGRESS_INTERVAL_SECONDS:
                    emit(
                        "progress",
                        scene.scene_id,
                        scene_index,
                        completed_units=iterations,
                        total_units=minimum_iterations,
                        fraction=min(0.99, required_fraction),
                    )
                    last_progress_at = now
                if elapsed >= MEASURE_TARGET_SECONDS and iterations >= minimum_iterations:
                    break

            cv = coefficient_of_variation(timings)
            all_cvs.append(cv)
            median_s = statistics.median(timings)
            median_ms = median_s * 1000.0
            input_mp = (scene.width * scene.height) / 1_000_000.0
            output_mp = (scene.width * scale * scene.height * scale) / 1_000_000.0
            sec_per_input_mp = median_s / input_mp if input_mp > 0 else 0.0
            out_mps = output_mp / median_s if median_s > 0 else 0.0
            scene_results.append(
                {
                    **SceneResult(
                        scene_id=scene.scene_id,
                        purpose=scene.purpose,
                        input_width=scene.width,
                        input_height=scene.height,
                        output_width=scene.width * scale,
                        output_height=scene.height * scale,
                        iterations=iterations,
                        median_ms=round(median_ms, 3),
                        p05_ms=round(percentile(timings, 5) * 1000.0, 3),
                        p95_ms=round(percentile(timings, 95) * 1000.0, 3),
                        cv_percent=round(cv * 100.0, 2) if math.isfinite(cv) else None,
                        megapixels_per_second=round(out_mps, 4),
                        encode_ms=None,
                        preview=preview,
                    ).to_dict(),
                    "seconds_per_input_mp": round(sec_per_input_mp, 3),
                }
            )
            emit(
                "completed",
                scene.scene_id,
                scene_index,
                completed_units=iterations,
                total_units=iterations,
                fraction=1.0,
            )

    verified_after, verify_after_note = _verify_backend_execution(engine, device_entry)
    if not verified_after:
        return (
            make_failed(
                status="unavailable",
                note=verify_after_note or "did not verify hardware execution",
                thermal=thermal_before,
            ),
            cpu_reference,
        )

    thermal_after = _thermal_state(base_device_id)
    cooldown_index = len(V3_SCENES) + 1
    emit("started", "cooldown", cooldown_index)
    _cool_down(
        cancel_event,
        base_device_id,
        thermal_before,
        progress_callback=lambda fraction: emit(
            "progress", "cooldown", cooldown_index, fraction=fraction
        ),
    )
    emit("completed", "cooldown", cooldown_index, fraction=1.0)

    phase_cv = max(all_cvs) if all_cvs else float("inf")
    sec_per_mp_geo = _geometric_mean(
        float(scene["seconds_per_input_mp"]) for scene in scene_results
    )
    score_geo = _geometric_mean(float(scene["megapixels_per_second"]) for scene in scene_results)
    return (
        V3DeviceResult(
            device=device_id,
            device_type=device_type,
            device_name=device_name,
            preset=preset,
            model_id=model.model_id,
            model_name=model.name,
            thermal_state=thermal_after or thermal_before,
            warmup_iterations=warmup_iterations,
            peak_memory_bytes=memory.peak,
            peak_device_memory_bytes=memory.peak_device,
            stable=phase_cv <= TARGET_CV,
            cv_percent=round(phase_cv * 100.0, 2) if math.isfinite(phase_cv) else None,
            score=round(score_geo, 2) if score_geo is not None else 0.0,
            seconds_per_input_mp=(round(sec_per_mp_geo, 3) if sec_per_mp_geo is not None else None),
            matched_cpu=matched_cpu,
            psnr_db=psnr_db,
            status="ok",
            status_note=None,
            recommended=False,
            scenes=scene_results,
        ),
        cpu_reference,
    )


def run_benchmark_v3(
    *,
    engine: InferenceEngine,
    model_adapter: object,
    cancel_event: threading.Event,
    store: ModelStore | None = None,
    capabilities: dict | None = None,
    progress_callback: Callable[[StageUpdate], None] | None = None,
    preview_callback: Callable[[dict], None] | None = None,
    tile_callback: Callable[..., None] | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> BenchmarkV3Result:
    """Run localsr-benchmark-v3 across every available device and installed preset model."""
    started_at = clock()
    available_models, missing_presets = resolve_benchmark_preset_models(store=store)
    if not available_models:
        missing_names = ", ".join(item["model_name"] for item in missing_presets)
        raise FileNotFoundError(
            f"Download at least one preset model before benchmarking ({missing_names})."
        )

    devices = enumerate_benchmark_devices(capabilities)
    if not devices:
        raise RuntimeError("No compute devices are available for benchmarking.")

    stages_per_device = len(V3_SCENES) + 2
    total_stages = len(available_models) * len(devices) * stages_per_device

    previous_threads = torch.get_num_threads()
    previous_neural_engine = getattr(engine, "neural_engine_enabled", True)
    physical_cores, logical_cores = cpu_thread_counts()
    cpu_threads_info: dict = {
        "physical_cores": physical_cores,
        "logical_cores": logical_cores,
        "recommended_threads": logical_cores,
        "tested": False,
        "physical_seconds_per_input_mp": None,
        "logical_seconds_per_input_mp": None,
        "model_id": available_models[0]["model"].model_id,
        "model_name": available_models[0]["model"].name,
    }

    raw_results: list[V3DeviceResult] = []
    benchmarked_models: list[dict] = []
    probed_threads = False

    try:
        for model_index, model_entry in enumerate(available_models):
            if cancel_event.is_set():
                raise InterruptedError("benchmark cancelled")
            model = model_entry["model"]
            model_info = model_adapter.inspect(model_entry["model_path"])
            scale = int(getattr(model_info, "scale", model.native_scale))
            cpu_reference: np.ndarray | None = None

            if not probed_threads:
                probed_threads = True
                if physical_cores < logical_cores:
                    engine.neural_engine_enabled = False
                    engine.load_model(model_entry["model_path"], "cpu", PRECISION, model_info)
                    cpu_threads_info = probe_cpu_threads(
                        engine=engine,
                        model_info=model_info,
                        model_id=model.model_id,
                        model_name=model.name,
                        scene=V3_SCENES[0],
                        cancel_event=cancel_event,
                        clock=clock,
                    )
                    torch.set_num_threads(int(cpu_threads_info["recommended_threads"]))

            model_device_results: list[V3DeviceResult] = []
            for dev_index, device_entry in enumerate(devices):
                if cancel_event.is_set():
                    raise InterruptedError("benchmark cancelled")
                stage_offset = (model_index * len(devices) + dev_index) * stages_per_device
                result, cpu_reference = _run_v3_device_model(
                    engine=engine,
                    model_info=model_info,
                    model_entry=model_entry,
                    device_entry=device_entry,
                    cpu_reference=cpu_reference,
                    stage_offset=stage_offset,
                    stage_count=total_stages,
                    cancel_event=cancel_event,
                    progress_callback=progress_callback,
                    preview_callback=preview_callback,
                    tile_callback=tile_callback,
                    clock=clock,
                )
                model_device_results.append(result)

            # Pick the fastest verified device for this model.
            verified_for_model = [
                item
                for item in model_device_results
                if item.matched_cpu is True and item.seconds_per_input_mp is not None
            ]
            fastest_for_model = min(
                verified_for_model,
                key=lambda item: item.seconds_per_input_mp or float("inf"),
                default=None,
            )
            for item in model_device_results:
                is_winner = (
                    fastest_for_model is not None and item.device == fastest_for_model.device
                )
                raw_results.append(V3DeviceResult(**{**asdict(item), "recommended": is_winner}))

            benchmarked_models.append(
                {
                    "preset": model_entry["preset"],
                    "preset_label": model_entry["preset_label"],
                    "model_id": model.model_id,
                    "model_name": model.name,
                    "scale": scale,
                    "recommended_device": (fastest_for_model.device if fastest_for_model else None),
                    "recommended_device_name": (
                        fastest_for_model.device_name if fastest_for_model else None
                    ),
                    "recommended_seconds_per_input_mp": (
                        fastest_for_model.seconds_per_input_mp if fastest_for_model else None
                    ),
                }
            )
    finally:
        torch.set_num_threads(previous_threads)
        engine.neural_engine_enabled = previous_neural_engine

    # Pick overall recommended device across benchmarked models. Prefer a device
    # that passed correctness for the most benchmarked models, breaking ties by
    # lowest geometric-mean seconds per input MP.
    by_device: dict[str, list[V3DeviceResult]] = {}
    for item in raw_results:
        if item.matched_cpu is True and item.seconds_per_input_mp is not None:
            by_device.setdefault(item.device, []).append(item)

    overall_winner_id: str | None = None
    overall_winner_name: str | None = None
    overall_winner_sec: float | None = None
    if by_device:
        ranked_devices = sorted(
            by_device.items(),
            key=lambda kv: (
                -len(kv[1]),
                _geometric_mean(
                    float(entry.seconds_per_input_mp)
                    for entry in kv[1]
                    if entry.seconds_per_input_mp is not None
                )
                or float("inf"),
            ),
        )
        overall_winner_id = ranked_devices[0][0]
        overall_winner_name = ranked_devices[0][1][0].device_name
        geo = _geometric_mean(
            float(entry.seconds_per_input_mp)
            for entry in ranked_devices[0][1]
            if entry.seconds_per_input_mp is not None
        )
        overall_winner_sec = round(geo, 3) if geo is not None else None

    serialized = [item.to_dict() for item in raw_results]
    verified_entries = [
        d
        for d in serialized
        if d.get("matched_cpu") is True and d.get("seconds_per_input_mp") is not None
    ]
    stable = bool(verified_entries) and all(bool(d.get("stable")) for d in verified_entries)
    cvs = [d.get("cv_percent") for d in verified_entries]
    measured = bool(cvs) and all(cv is not None and math.isfinite(cv) for cv in cvs)
    worst_cv = max(cvs) if measured else None
    stable = stable and measured

    accel_scores = [
        float(d["score"])
        for d in verified_entries
        if d.get("device_type") != "cpu" and float(d.get("score") or 0) > 0
    ]
    cpu_scores = [
        float(d["score"])
        for d in verified_entries
        if d.get("device_type") == "cpu" and float(d.get("score") or 0) > 0
    ]
    thermal_states = {str(d.get("thermal_state", "unknown")) for d in verified_entries}
    primary_model = available_models[0]["model"]

    return BenchmarkV3Result(
        workload_version=WORKLOAD_VERSION,
        device_results=serialized,
        benchmarked_models=benchmarked_models,
        missing_presets=missing_presets,
        recommended_device=overall_winner_id,
        recommended_device_name=overall_winner_name,
        recommended_device_seconds_per_input_mp=overall_winner_sec,
        recommended_cpu_threads=int(cpu_threads_info["recommended_threads"]),
        cpu_threads=cpu_threads_info,
        cpu_avx2=cpu_supports_avx2(),
        cpu_memory_format="nchw",
        system_score=round(max(accel_scores), 2) if (stable and accel_scores) else None,
        cpu_score=round(max(cpu_scores), 2) if (stable and cpu_scores) else None,
        stable=stable,
        cv_percent=round(worst_cv, 2) if worst_cv is not None else None,
        elapsed_seconds=round(clock() - started_at, 2),
        thermal_state=(
            "mixed" if len(thermal_states) > 1 else next(iter(thermal_states), "unknown")
        ),
        model_id=primary_model.model_id,
        model_name=primary_model.name,
        scale=int(primary_model.native_scale),
    )
