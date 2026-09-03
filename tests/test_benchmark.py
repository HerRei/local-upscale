import threading

import numpy as np

from localsr.core.benchmark import (
    INPUT_HEIGHT,
    INPUT_WIDTH,
    MEASURED_FRAME_COUNT,
    WORKLOAD_VERSION,
    aggregate_metrics,
    deterministic_input,
    percentile,
    run_benchmark,
)


def test_benchmark_fixture_is_deterministic_and_bounded():
    first = deterministic_input()
    second = deterministic_input()
    assert tuple(first.shape) == (3, INPUT_HEIGHT, INPUT_WIDTH)
    assert first.equal(second)
    assert float(first.min()) >= 0
    assert float(first.max()) <= 1


def test_metric_aggregation_and_score_are_stable():
    durations = [0.010, 0.020, 0.030, 0.040, 0.050]
    result = aggregate_metrics(
        durations,
        0.2,
        backend="cpu",
        device="cpu",
        model_name="Quick",
        peak_memory_bytes=1234,
    )
    assert percentile(durations, 95) == 0.048
    assert result.workload_version == WORKLOAD_VERSION
    assert result.median_inference_ms == 30.0
    assert result.p95_inference_ms == 48.0
    assert result.end_to_end_fps == 25.0
    assert result.processed_megapixels_per_second == 0.4096
    assert result.score == 409.6
    assert result.peak_memory_bytes == 1234


class _FakeEngine:
    def __init__(self):
        self.calls = 0
        self.loaded = None

    def load_model(self, path, device, precision, info):
        self.loaded = (path, device, precision, info)

    def process_frame(self, **kwargs):
        self.calls += 1
        tensor = kwargs["img_tensor"]
        return np.zeros((3, tensor.shape[1] * 4, tensor.shape[2] * 4), dtype=np.uint8)


def test_benchmark_uses_production_frame_path_with_deterministic_clock():
    engine = _FakeEngine()
    moments = iter([0.0, 0.0, 0.01, 0.01, 0.03, 0.03, 0.06, 0.06, 0.10, 0.10, 0.15, 0.2])
    progress = []
    result = run_benchmark(
        engine=engine,
        model_info=object(),
        model_path="quick.pth",
        model_name="Quick",
        device="cpu",
        cancel_event=threading.Event(),
        progress_callback=lambda completed, total: progress.append((completed, total)),
        clock=lambda: next(moments),
    )
    assert engine.calls == MEASURED_FRAME_COUNT + 1
    assert engine.loaded[:3] == ("quick.pth", "cpu", "fp32")
    assert progress[-1] == (MEASURED_FRAME_COUNT, MEASURED_FRAME_COUNT)
    assert result.total_elapsed_seconds == 0.2


def test_benchmark_cancellation_is_honored_before_inference():
    cancel = threading.Event()
    cancel.set()
    engine = _FakeEngine()
    try:
        run_benchmark(
            engine=engine,
            model_info=object(),
            model_path="quick.pth",
            model_name="Quick",
            device="cpu",
            cancel_event=cancel,
        )
    except InterruptedError:
        pass
    else:  # pragma: no cover - assertion branch
        raise AssertionError("cancelled benchmark ran")
    assert engine.calls == 0
