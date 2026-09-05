"""LocalSR Benchmark v2 — fixed multi-scene workload with stability gating.

Design goals (workload ``localsr-benchmark-v2``):

- **Fixed, versioned scenes** so two results are comparable only when the
  ``workload_version`` matches, in the spirit of Blender's BMW27: every
  machine renders the same procedural, license-safe content.
- **Measure CPU and every detected accelerator**, each in its own phase, so
  a single run produces a small device table instead of one number.
- **Repeatability over speed.** Warm-up runs until the rolling coefficient of
  variation settles (or a time cap), timings are taken around synchronized
  inferences, devices cool down between phases, and a result whose measured
  spread exceeds the stability threshold is reported as *unstable* instead of
  silently producing a different headline score on every run.

Scene content is generated from integer arithmetic only (gradients, periodic
checker/detail zones, synthetic sensor noise), never from external media, so
results stay byte-stable across machines and releases.
"""

from __future__ import annotations

import json
import math
import statistics
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Literal

import numpy as np
import torch

from .inference import InferenceEngine

WORKLOAD_VERSION = "localsr-benchmark-v2"
WORKLOAD_MODEL_ID = "span_photo_x4"
OUTPUT_SCALE = 4
HALO = 16
PRECISION = "fp32"

# Stability gate: the measured phase must settle below this coefficient of
# variation for the score to be reported as trustworthy.
TARGET_CV = 0.05
# Warm-up stops once three consecutive rolling CV samples are below TARGET_CV.
WARMUP_MIN_ITERATIONS = 3
WARMUP_MAX_SECONDS = 30.0
# Measure for at least this long (or this many iterations, whichever is later)
# so scheduling noise has a chance to average out.
MEASURE_TARGET_SECONDS = 20.0
MEASURE_MIN_ITERATIONS = 6
MEASURE_MAX_SECONDS = 45.0
COOLDOWN_MAX_SECONDS = 60.0
COOLDOWN_POLL_SECONDS = 2.0
PROGRESS_INTERVAL_SECONDS = 0.5
# Scene 3 measures the encode stage that every desktop job performs.
ENCODE_QUALITY = 90


@dataclass(frozen=True)
class SceneSpec:
    scene_id: str
    width: int
    height: int
    tile_size: int
    purpose: str


# The three fixed scenes. ``s2-gallery`` is deliberately large so the tiled
# path (halo overlap, compositing, memory traffic) dominates, and ``s3``
# adds the final JPEG encode that real jobs pay for.
SCENES: tuple[SceneSpec, ...] = (
    SceneSpec("s1-classroom", 512, 512, 256, "compute"),
    SceneSpec("s2-gallery", 3072, 2048, 256, "tiled-end-to-end"),
    SceneSpec("s3-gallery-encode", 768, 512, 256, "pipeline-with-encode"),
)


@dataclass(frozen=True)
class SceneResult:
    scene_id: str
    purpose: str
    input_width: int
    input_height: int
    output_width: int
    output_height: int
    iterations: int
    median_ms: float
    p05_ms: float
    p95_ms: float
    cv_percent: float
    megapixels_per_second: float
    encode_ms: float | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class DeviceBenchmark:
    device: str
    device_type: str
    device_name: str
    thermal_state: str
    warmup_iterations: int
    peak_memory_bytes: int | None
    peak_device_memory_bytes: int | None
    stable: bool
    cv_percent: float
    score: float
    scenes: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class StageUpdate:
    event: Literal["started", "progress", "completed"]
    stage: str
    stage_index: int
    stage_count: int
    completed_units: int = 0
    total_units: int = 0
    fraction: float = 0.0


StageCallback = Callable[[StageUpdate], None]


@dataclass(frozen=True)
class BenchmarkV2Result:
    workload_version: str
    device_results: list[dict]
    system_score: float | None
    cpu_score: float | None
    stable: bool
    cv_percent: float
    elapsed_seconds: float
    thermal_state: str
    reference_label: str | None
    reference_ratio: float | None
    model_id: str = WORKLOAD_MODEL_ID
    model_name: str = "SPAN 4x NomosUni — Quick"
    scale: int = OUTPUT_SCALE

    def to_dict(self) -> dict[str, object]:
        """Emit a superset of the v1 result so one Rust struct serves both.

        The v1 headline fields summarize the fastest accelerator's first
        scene; the authoritative v2 data lives in ``device_results`` and the
        dedicated v2 fields. The stability gate blanks the headline scores of
        an unstable run instead of publishing a number that would differ from
        the next run.
        """
        payload = asdict(self)
        headline = next(
            (device for device in self.device_results if device.get("device_type") != "cpu"),
            self.device_results[0] if self.device_results else None,
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
                "input_width": int(headline_scene.get("input_width", 0)),
                "input_height": int(headline_scene.get("input_height", 0)),
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
                "score": self.system_score if self.system_score is not None else 0.0,
            }
        )
        return payload


def _scene_tensor(scene: SceneSpec) -> torch.Tensor:
    """Procedural, license-free scene content with distinct frequency zones.

    Left third: smooth gradients (large receptive fields matter).
    Middle third: high-frequency checkerboard (reconstruction sharpness).
    Right third: synthetic sensor noise plus a soft vignette (denoise-style
    load). All values derive from integer coordinates, so the scene is
    byte-identical everywhere.
    """
    width, height = scene.width, scene.height
    x = np.arange(width, dtype=np.int32)[None, :]
    y = np.arange(height, dtype=np.int32)[:, None]
    rgb = np.empty((height, width, 3), dtype=np.uint8)

    gradient = ((x * 3 + y * 5) % 256).astype(np.uint8)
    checker = (((x // 4 + y // 4) % 2) * 235 + 10).astype(np.uint8)
    rng = np.random.default_rng(20240904)
    noise = rng.integers(0, 256, size=(height, width), dtype=np.uint16)
    radius = np.sqrt(
        ((x - width // 2).astype(np.float32) ** 2 + (y - height // 2).astype(np.float32) ** 2)
    )
    vignette = (1.0 - np.clip(radius / max(width, height), 0.0, 1.0) * 0.6) * 255.0

    third = width // 3
    for channel in range(3):
        plane = np.empty((height, width), dtype=np.uint8)
        plane[:, :third] = np.broadcast_to(gradient[:, :third], (height, third))
        plane[:, third : 2 * third] = np.broadcast_to(
            checker[:, third : 2 * third], (height, third)
        )
        noise_channel = ((noise + channel * 37) % 256).astype(np.float32)
        plane[:, 2 * third :] = np.clip(
            noise_channel[:, 2 * third :] * vignette[:, 2 * third :] / 255.0, 0, 255
        ).astype(np.uint8)
        rgb[:, :, channel] = plane

    return torch.from_numpy(rgb.astype(np.float32) / 255.0).permute(2, 0, 1)


def coefficient_of_variation(seconds: list[float]) -> float:
    """CV of timing samples as a fraction (0.05 == 5%)."""
    if len(seconds) < 2:
        return float("inf")
    mean = statistics.fmean(seconds)
    if mean <= 0:
        return float("inf")
    return statistics.pstdev(seconds) / mean


def percentile(values: list[float], percentile_value: float) -> float:
    """Linearly interpolated percentile, matching the v1 definition."""
    if not values:
        raise ValueError("at least one measurement is required")
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * min(100.0, max(0.0, percentile_value)) / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def _synchronize(device: torch.device) -> None:
    if device.type == "cuda":
        torch.cuda.synchronize(device)
    elif device.type == "xpu":
        torch.xpu.synchronize(device)
    elif device.type == "mps":
        torch.mps.synchronize()
    # cpu and directml need no extra barrier beyond the returned array.


def _thermal_state(device_id: str) -> str:
    """Best-effort thermal state probe.

    ``pmset -g therm`` prints the CPU thermal pressure level only once the
    system has recorded a warning (e.g. ``CPU_Sleep_Prefer``); the healthy
    state is a plain "No thermal warning level has been recorded" note. Other
    platforms have no portable equivalent, so they report ``unknown``.
    """
    import sys

    if sys.platform == "darwin":
        try:
            import subprocess

            output = subprocess.check_output(["pmset", "-g", "therm"], text=True, timeout=2).lower()
            if "no thermal warning level" in output:
                return "nominal"
            for level in ("critical", "wait", "fair", "sleep"):
                if level in output:
                    return level
            return "unknown"
        except (OSError, subprocess.SubprocessError, ValueError):
            return "unknown"
    if device_id.startswith("cuda") or device_id.startswith("xpu"):
        return "not exposed by this backend"
    return "unknown"


def _cool_down(
    cancel_event: threading.Event,
    device_id: str,
    baseline: str,
    progress_callback: Callable[[float], None] | None = None,
) -> str:
    """Wait until thermal pressure returns to the pre-run state or the cap."""
    current = _thermal_state(device_id)
    unavailable = {"unknown", "not exposed by this backend"}
    if baseline in unavailable or current in unavailable or current == baseline:
        return current

    severity = {"nominal": 0, "fair": 1, "wait": 2, "sleep": 2, "critical": 3}
    baseline_severity = severity.get(baseline)
    current_severity = severity.get(current)
    if (
        baseline_severity is None
        or current_severity is None
        or current_severity <= baseline_severity
    ):
        return current

    started = time.monotonic()
    while time.monotonic() - started < COOLDOWN_MAX_SECONDS:
        if cancel_event.is_set():
            raise InterruptedError("benchmark cancelled")
        time.sleep(COOLDOWN_POLL_SECONDS)
        elapsed = time.monotonic() - started
        if progress_callback is not None:
            progress_callback(min(1.0, elapsed / COOLDOWN_MAX_SECONDS))
        current = _thermal_state(device_id)
        current_severity = severity.get(current)
        if current in unavailable or (
            current_severity is not None and current_severity <= baseline_severity
        ):
            return current
    return _thermal_state(device_id)


class _PeakMemorySampler:
    """Best-effort peak RSS + device memory sampler; None means unavailable."""

    def __init__(self, device: torch.device | None) -> None:
        self.peak: int | None = None
        self.peak_device: int | None = None
        self._device = device
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _sample_device(self) -> int | None:
        device = self._device
        if device is None:
            return None
        try:
            if device.type == "cuda":
                return int(torch.cuda.max_memory_allocated(device))
            if device.type == "xpu":
                stats = getattr(torch.xpu, "memory_stats", None)
                if callable(stats):
                    return int(stats(device).get("allocated_bytes.all.peak", 0))
                return None
            if device.type == "mps":
                current = getattr(torch.mps, "driver_allocated_memory", None)
                return int(current()) if callable(current) else None
        except (RuntimeError, ValueError):
            return None
        return None

    def __enter__(self) -> _PeakMemorySampler:
        try:
            import psutil

            process = psutil.Process()
        except (ImportError, OSError):
            process = None

        def sample() -> None:
            while not self._stop.wait(0.05):
                if process is not None:
                    try:
                        rss = int(process.memory_info().rss)
                    except (OSError, RuntimeError):
                        pass
                    else:
                        self.peak = max(self.peak or 0, rss)
                device_peak = self._sample_device()
                if device_peak is not None:
                    self.peak_device = max(self.peak_device or 0, device_peak)

        self._thread = threading.Thread(target=sample, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.5)
        # Capture a final device reading after the last synchronize.
        device_peak = self._sample_device()
        if device_peak is not None:
            self.peak_device = max(self.peak_device or 0, device_peak)


def _encode_jpeg_ms(array: np.ndarray) -> float:
    """Time the lossy JPEG encode stage used by real desktop jobs."""
    import io

    from PIL import Image

    image = Image.fromarray(array.transpose(1, 2, 0), mode="RGB")
    started = time.perf_counter()
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=ENCODE_QUALITY, optimize=True)
    if not buffer.getvalue():
        raise RuntimeError("benchmark encode produced no output")
    return (time.perf_counter() - started) * 1000.0


def run_scene(
    *,
    engine: InferenceEngine,
    model_info: object,
    scene: SceneSpec,
    device: torch.device,
    cancel_event: threading.Event,
    clock: Callable[[], float] = time.perf_counter,
    warm: bool = False,
    tensor: torch.Tensor | None = None,
) -> tuple[list[float], float | None, int]:
    """Run one fixed scene; returns (timings, encode_ms, iterations)."""
    scene_tensor = tensor if tensor is not None else _scene_tensor(scene)
    timings: list[float] = []
    encode_ms: float | None = None

    def process_once() -> None:
        nonlocal encode_ms
        if cancel_event.is_set():
            raise InterruptedError("benchmark cancelled")
        _synchronize(device)
        started = clock()
        output = engine.process_frame(
            img_tensor=scene_tensor,
            model_info=model_info,
            tile_size=scene.tile_size,
            halo=HALO,
            cancel_event=cancel_event,
            progress_callback=lambda *_args: None,
            safe_memory=False,
            device=device,
        )
        _synchronize(device)
        if scene.scene_id == "s3-gallery-encode" and not warm:
            encode_ms = _encode_jpeg_ms(output)
        timings.append(clock() - started)

    process_once()
    return timings, encode_ms, len(timings)


def run_device_phase(
    *,
    engine: InferenceEngine,
    model_info: object,
    model_path: str,
    device_id: str,
    device_name: str,
    device_type: str,
    scenes: tuple[SceneSpec, ...],
    cancel_event: threading.Event,
    progress_callback: StageCallback | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> DeviceBenchmark:
    """Warm up, measure every scene, and gate the phase on timing stability."""
    if not scenes:
        raise ValueError("at least one benchmark scene is required")

    thermal_before = _thermal_state(device_id)
    torch_device, _ = engine.load_model(model_path, device_id, PRECISION, model_info)
    stage_count = len(scenes) + 2

    def emit(
        event: Literal["started", "progress", "completed"],
        stage: str,
        stage_index: int,
        *,
        completed_units: int = 0,
        total_units: int = 0,
        fraction: float = 0.0,
    ) -> None:
        if progress_callback is not None:
            progress_callback(
                StageUpdate(
                    event=event,
                    stage=f"{device_id}:{stage}",
                    stage_index=stage_index,
                    stage_count=stage_count,
                    completed_units=completed_units,
                    total_units=total_units,
                    fraction=max(0.0, min(1.0, fraction)),
                )
            )

    emit("started", "warmup", 0)
    warmup_iterations = 0
    warm_started = clock()
    warm_tensor = _scene_tensor(scenes[0])
    recent_cv: list[float] = []
    warm_timings: list[float] = []
    last_progress_at = 0.0
    while clock() - warm_started < WARMUP_MAX_SECONDS:
        if cancel_event.is_set():
            raise InterruptedError("benchmark cancelled")
        started = clock()
        engine.process_frame(
            img_tensor=warm_tensor,
            model_info=model_info,
            tile_size=scenes[0].tile_size,
            halo=HALO,
            cancel_event=cancel_event,
            progress_callback=lambda *_args: None,
            safe_memory=False,
            device=torch_device,
        )
        _synchronize(torch_device)
        warm_timings.append(clock() - started)
        warmup_iterations += 1
        warm_elapsed = clock() - warm_started
        now = time.monotonic()
        if now - last_progress_at >= PROGRESS_INTERVAL_SECONDS:
            emit(
                "progress",
                "warmup",
                0,
                completed_units=warmup_iterations,
                fraction=min(0.99, warm_elapsed / WARMUP_MAX_SECONDS),
            )
            last_progress_at = now
        if warmup_iterations >= WARMUP_MIN_ITERATIONS and len(warm_timings) >= 4:
            recent_cv.append(coefficient_of_variation(warm_timings[-4:]))
            if len(recent_cv) >= 3 and all(cv < TARGET_CV for cv in recent_cv[-3:]):
                break
    emit(
        "completed",
        "warmup",
        0,
        completed_units=warmup_iterations,
        fraction=1.0,
    )

    scene_results: list[dict] = []
    all_cvs: list[float] = []
    with _PeakMemorySampler(torch_device) as memory:
        for scene_index, scene in enumerate(scenes, start=1):
            if cancel_event.is_set():
                raise InterruptedError("benchmark cancelled")
            emit("started", scene.scene_id, scene_index)
            scene_tensor = _scene_tensor(scene)
            # Re-warm briefly per scene (tile shape changes memory pools).
            run_scene(
                engine=engine,
                model_info=model_info,
                scene=scene,
                device=torch_device,
                cancel_event=cancel_event,
                clock=clock,
                warm=True,
                tensor=scene_tensor,
            )
            timings: list[float] = []
            encode_ms: float | None = None
            measured_started = clock()
            iterations = 0
            last_progress_at = 0.0
            while True:
                if cancel_event.is_set():
                    raise InterruptedError("benchmark cancelled")
                scene_timings, scene_encode, _ = run_scene(
                    engine=engine,
                    model_info=model_info,
                    scene=scene,
                    device=torch_device,
                    cancel_event=cancel_event,
                    clock=clock,
                    tensor=scene_tensor,
                )
                timings.extend(scene_timings)
                if encode_ms is None and scene_encode is not None:
                    encode_ms = scene_encode
                iterations += 1
                elapsed = clock() - measured_started
                required_fraction = min(
                    elapsed / MEASURE_TARGET_SECONDS,
                    iterations / MEASURE_MIN_ITERATIONS,
                )
                now = time.monotonic()
                if now - last_progress_at >= PROGRESS_INTERVAL_SECONDS:
                    emit(
                        "progress",
                        scene.scene_id,
                        scene_index,
                        completed_units=iterations,
                        fraction=min(0.99, required_fraction),
                    )
                    last_progress_at = now
                if (
                    elapsed >= MEASURE_TARGET_SECONDS and iterations >= MEASURE_MIN_ITERATIONS
                ) or elapsed >= MEASURE_MAX_SECONDS:
                    break

            cv = coefficient_of_variation(timings)
            all_cvs.append(cv)
            median_ms = statistics.median(timings) * 1000.0
            output_megapixels = scene.width * OUTPUT_SCALE * scene.height * OUTPUT_SCALE / 1_000_000
            mps = output_megapixels / statistics.median(timings) if timings else 0.0
            scene_results.append(
                SceneResult(
                    scene_id=scene.scene_id,
                    purpose=scene.purpose,
                    input_width=scene.width,
                    input_height=scene.height,
                    output_width=scene.width * OUTPUT_SCALE,
                    output_height=scene.height * OUTPUT_SCALE,
                    iterations=iterations,
                    median_ms=round(median_ms, 3),
                    p05_ms=round(percentile(timings, 5) * 1000.0, 3),
                    p95_ms=round(percentile(timings, 95) * 1000.0, 3),
                    cv_percent=round(cv * 100.0, 2),
                    megapixels_per_second=round(mps, 4),
                    encode_ms=round(encode_ms, 3) if encode_ms is not None else None,
                ).to_dict()
            )
            emit(
                "completed",
                scene.scene_id,
                scene_index,
                completed_units=iterations,
                total_units=iterations,
                fraction=1.0,
            )

    thermal_after = _thermal_state(device_id)
    cooldown_index = stage_count - 1
    emit("started", "cooldown", cooldown_index)
    _cool_down(
        cancel_event,
        device_id,
        thermal_before,
        progress_callback=lambda fraction: emit(
            "progress", "cooldown", cooldown_index, fraction=fraction
        ),
    )
    emit("completed", "cooldown", cooldown_index, fraction=1.0)
    phase_cv = max(all_cvs) if all_cvs else float("inf")
    score = _geometric_mean(
        float(scene.get("megapixels_per_second", 0.0)) for scene in scene_results
    )
    return DeviceBenchmark(
        device=device_id,
        device_type=device_type,
        device_name=device_name,
        thermal_state=thermal_before if thermal_before == thermal_after else thermal_after,
        warmup_iterations=warmup_iterations,
        peak_memory_bytes=memory.peak,
        peak_device_memory_bytes=memory.peak_device,
        stable=phase_cv <= TARGET_CV,
        cv_percent=round(phase_cv * 100.0, 2),
        score=round(score, 2) if score is not None else 0.0,
        scenes=scene_results,
    )


def _geometric_mean(values: Iterable[float]) -> float | None:
    rates = [float(value) for value in values if float(value) > 0]
    if not rates:
        return None
    return math.prod(rates) ** (1.0 / len(rates))


def _device_score(device: dict) -> float | None:
    recorded = float(device.get("score", 0.0))
    if recorded > 0:
        return recorded
    return _geometric_mean(
        float(scene.get("megapixels_per_second", 0.0)) for scene in device.get("scenes", [])
    )


def system_score(device_results: list[dict]) -> float | None:
    """Return the fastest accelerator's geometric-mean scene throughput.

    The CPU phase is reported separately (``cpu_score``) exactly like Blender
    separates CPU and GPU render times. Multiple accelerators are also kept as
    independent results: averaging them would penalize a system for containing
    an additional slower adapter even though the benchmark does not combine
    devices for one inference.
    """
    scores = [
        score
        for device in device_results
        if device.get("device_type") != "cpu"
        if (score := _device_score(device)) is not None
    ]
    if not scores:
        return None
    return round(max(scores), 2)


def cpu_scene_score(device_results: list[dict]) -> float | None:
    """Geometric mean of CPU scene throughput (median-based, in MP/s)."""
    scores = [
        score
        for device in device_results
        if device.get("device_type") == "cpu"
        if (score := _device_score(device)) is not None
    ]
    if not scores:
        return None
    return round(max(scores), 2)


def aggregate_v2(
    device_results: list[dict],
    elapsed_seconds: float,
) -> BenchmarkV2Result:
    """Assemble the final v2 payload with the stability gate applied."""
    stable = bool(device_results) and all(
        bool(device.get("stable", False)) for device in device_results
    )
    worst_cv = max(
        (float(device.get("cv_percent", 100.0)) for device in device_results), default=100.0
    )
    thermal_states = {str(device.get("thermal_state", "unknown")) for device in device_results}

    score = system_score(device_results)
    cpu = cpu_scene_score(device_results)
    reference_label, reference_ratio = _reference_comparison(score)

    return BenchmarkV2Result(
        workload_version=WORKLOAD_VERSION,
        device_results=device_results,
        system_score=score if stable else None,
        cpu_score=cpu if stable else None,
        stable=stable,
        cv_percent=round(worst_cv, 2),
        elapsed_seconds=round(elapsed_seconds, 2),
        thermal_state=(
            "mixed" if len(thermal_states) > 1 else next(iter(thermal_states), "unknown")
        ),
        reference_label=reference_label if stable else None,
        reference_ratio=reference_ratio if stable else None,
    )


def _reference_comparison(
    score: float | None,
    references: tuple[tuple[str, float], ...] | None = None,
) -> tuple[str | None, float | None]:
    """Compare against the bundled reference table.

    Prefer the *closest faster* reference (the smallest ratio >= 1.0), which
    reads as "you are N× as fast as X". If the score beats nothing, fall back
    to the closest slower reference. GPU and CPU references are never mixed;
    the caller picks the list matching the score being compared.
    """
    if score is None:
        return None, None
    available_references = REFERENCE_SCORES if references is None else references

    def best_for(score_value: float) -> tuple[str | None, float | None]:
        faster = [
            (label, score_value / reference)
            for label, reference in available_references
            if reference > 0 and score_value / reference >= 1.0
        ]
        if faster:
            # Smallest ratio above 1.0 == the tightest "beaten" reference.
            label, ratio = min(faster, key=lambda item: item[1])
            return label, round(ratio, 2)
        slower = [
            (label, score_value / reference)
            for label, reference in available_references
            if reference > 0 and score_value / reference < 1.0
        ]
        if slower:
            # Largest ratio below 1.0 == the tightest "not yet beaten" one.
            label, ratio = max(slower, key=lambda item: item[1])
            return label, round(ratio, 2)
        return None, None

    return best_for(score)


def _load_reference_scores() -> tuple[tuple[str, float], ...]:
    """Load only references measured with this exact workload and metric."""
    path = Path(__file__).with_name("benchmark_references.json")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return ()
    if payload.get("workload_version") != WORKLOAD_VERSION:
        return ()
    if payload.get("metric") != "output_megapixels_per_second":
        return ()

    references: list[tuple[str, float]] = []
    for entry in payload.get("entries", []):
        if entry.get("kind") != "accelerator":
            continue
        try:
            label = str(entry["label"]).strip()
            score = float(entry["score"])
        except (KeyError, TypeError, ValueError):
            continue
        if label and math.isfinite(score) and score > 0:
            references.append((label, score))
    return tuple(references)


# The JSON file intentionally contains only physical, repeatable measurements;
# unsupported marketing estimates are ignored rather than shown as facts.
REFERENCE_SCORES = _load_reference_scores()
