"""Deterministic LocalSR benchmark workload and metric aggregation.

The benchmark deliberately exercises :class:`InferenceEngine` through the
same tiled ``process_frame`` path used by video and desktop jobs.  It is not a
general-purpose hardware score: the workload and formula are versioned so two
results are comparable only when ``workload_version`` is identical.
"""

from __future__ import annotations

import math
import statistics
import threading
import time
from dataclasses import asdict, dataclass
from typing import Callable

import numpy as np
import torch

from .inference import InferenceEngine

WORKLOAD_VERSION = "localsr-benchmark-v1"
WORKLOAD_MODEL_ID = "span_photo_x4"
INPUT_WIDTH = 128
INPUT_HEIGHT = 128
WARMUP_COUNT = 1
MEASURED_FRAME_COUNT = 5
OUTPUT_SCALE = 4
TILE_SIZE = 128
HALO = 16
PRECISION = "fp32"


@dataclass(frozen=True)
class BenchmarkResult:
    workload_version: str
    backend: str
    device: str
    model_id: str
    model_name: str
    scale: int
    input_width: int
    input_height: int
    warmup_count: int
    measured_frame_count: int
    median_inference_ms: float
    p95_inference_ms: float
    end_to_end_fps: float
    processed_megapixels_per_second: float
    total_elapsed_seconds: float
    peak_memory_bytes: int | None
    score: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def deterministic_input(width: int = INPUT_WIDTH, height: int = INPUT_HEIGHT) -> torch.Tensor:
    """Create the license-safe, byte-stable RGB workload without external media."""
    x = np.arange(width, dtype=np.uint16)[None, :]
    y = np.arange(height, dtype=np.uint16)[:, None]
    rgb = np.empty((height, width, 3), dtype=np.uint8)
    rgb[:, :, 0] = ((x * 3 + y * 5) % 256).astype(np.uint8)
    rgb[:, :, 1] = ((x * 7 + y * 2 + 31) % 256).astype(np.uint8)
    rgb[:, :, 2] = (((x ^ y) * 11 + 17) % 256).astype(np.uint8)
    return torch.from_numpy(rgb.astype(np.float32) / 255.0).permute(2, 0, 1)


def percentile(values: list[float], percentile_value: float) -> float:
    """Return a linearly interpolated percentile for deterministic small samples."""
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


def aggregate_metrics(
    inference_seconds: list[float],
    total_elapsed_seconds: float,
    *,
    backend: str,
    device: str,
    model_name: str,
    peak_memory_bytes: int | None = None,
) -> BenchmarkResult:
    """Aggregate v1 metrics and its transparent higher-is-better score.

    ``score = end-to-end processed input megapixels/second × 1000``.  Output
    scale is held constant by the workload, so the score remains easy to audit
    and cannot be confused with a cross-workload hardware guarantee.
    """
    if len(inference_seconds) != MEASURED_FRAME_COUNT:
        raise ValueError(f"expected {MEASURED_FRAME_COUNT} measured frames")
    if total_elapsed_seconds <= 0 or any(value <= 0 for value in inference_seconds):
        raise ValueError("benchmark timings must be positive")
    frame_count = len(inference_seconds)
    input_megapixels = INPUT_WIDTH * INPUT_HEIGHT / 1_000_000
    fps = frame_count / total_elapsed_seconds
    megapixels_per_second = frame_count * input_megapixels / total_elapsed_seconds
    return BenchmarkResult(
        workload_version=WORKLOAD_VERSION,
        backend=backend,
        device=device,
        model_id=WORKLOAD_MODEL_ID,
        model_name=model_name,
        scale=OUTPUT_SCALE,
        input_width=INPUT_WIDTH,
        input_height=INPUT_HEIGHT,
        warmup_count=WARMUP_COUNT,
        measured_frame_count=frame_count,
        median_inference_ms=round(statistics.median(inference_seconds) * 1000.0, 3),
        p95_inference_ms=round(percentile(inference_seconds, 95.0) * 1000.0, 3),
        end_to_end_fps=round(fps, 4),
        processed_megapixels_per_second=round(megapixels_per_second, 4),
        total_elapsed_seconds=round(total_elapsed_seconds, 4),
        peak_memory_bytes=peak_memory_bytes,
        score=round(megapixels_per_second * 1000.0, 2),
    )


class _PeakMemorySampler:
    """Best-effort current-process RSS sampler; unavailable is reported as null."""

    def __init__(self) -> None:
        self.peak: int | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> _PeakMemorySampler:
        try:
            import psutil

            process = psutil.Process()
        except (ImportError, OSError):
            return self

        def sample() -> None:
            while not self._stop.wait(0.01):
                try:
                    rss = int(process.memory_info().rss)
                except (OSError, RuntimeError):
                    return
                self.peak = max(self.peak or 0, rss)

        self._thread = threading.Thread(target=sample, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.2)


def run_benchmark(
    *,
    engine: InferenceEngine,
    model_info: object,
    model_path: str,
    model_name: str,
    device: str,
    cancel_event: threading.Event,
    progress_callback: Callable[[int, int], None] | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> BenchmarkResult:
    """Run the fixed v1 workload through LocalSR's production inference path."""
    tensor = deterministic_input()
    engine.load_model(model_path, device, PRECISION, model_info)

    def process_once() -> None:
        if cancel_event.is_set():
            raise InterruptedError("benchmark cancelled")
        engine.process_frame(
            img_tensor=tensor,
            model_info=model_info,
            tile_size=TILE_SIZE,
            halo=HALO,
            cancel_event=cancel_event,
            progress_callback=lambda *_args: None,
            safe_memory=False,
            tile_callback=None,
        )

    with _PeakMemorySampler() as memory:
        for _ in range(WARMUP_COUNT):
            process_once()
        timings: list[float] = []
        measured_started = clock()
        for index in range(MEASURED_FRAME_COUNT):
            started = clock()
            process_once()
            timings.append(clock() - started)
            if progress_callback is not None:
                progress_callback(index + 1, MEASURED_FRAME_COUNT)
        elapsed = clock() - measured_started

    backend = device.split(":", maxsplit=1)[0].lower()
    return aggregate_metrics(
        timings,
        elapsed,
        backend=backend,
        device=device,
        model_name=model_name,
        peak_memory_bytes=memory.peak,
    )
