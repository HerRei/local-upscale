"""Remaining-time estimates stay near the truth for synthetic progress sequences.

Each test feeds the progress reporter the callbacks the engine makes, with a fake clock, and
compares every estimate it emits with the time that really remained in that simulation.
"""

from __future__ import annotations

import math
from types import SimpleNamespace
from unittest.mock import Mock

from localsr.core.pipeline import resolve_image_pipeline
from localsr.worker.job_progress import ImageJobProgress


def _pipeline(stage_count: int):
    stages = [
        {"kind": "restore", "model_id": f"fix{i}", "model_path": f"fix{i}.pth"}
        for i in range(stage_count - 1)
    ]
    stages.append({"kind": "upscale", "model_id": "up", "model_path": "up.pth"})
    return resolve_image_pipeline(raw_stages=stages, model_path="up.pth")


class Run:
    """One simulated image job: tiles of any size, each with its own duration."""

    def __init__(self, monkeypatch, stage_count: int = 1, width: int = 640, height: int = 640):
        monkeypatch.setattr("localsr.worker.job_progress.get_memory_snapshot", lambda _: {})
        self.now = 1000.0
        self.pipeline = _pipeline(stage_count)
        self.messages: list = []
        self.progress = ImageJobProgress(
            "job",
            self.pipeline,
            "cpu",
            self.messages.append,
            Mock(),
            clock=lambda: self.now,
        )
        self.width, self.height = width, height
        # (time, remaining seconds the worker reported, stage index)
        self.samples: list[tuple[float, float, int]] = []
        self.stage = 0

    def start_stage(self, index: int, span: int = 1) -> None:
        self.stage = index
        self.progress.start(index, self.pipeline.stages[index], span=span)

    def tile(self, completed: int, total: int, x: int, y: int, size: int, seconds: float) -> None:
        tile = SimpleNamespace(out_x=x, out_y=y, out_w=size, out_h=size)
        args = (tile, None, completed - 1, total, self.width, self.height, size)
        self.progress.tile("started", *args)
        self.now += seconds
        self.progress.tile("completed", tile, b"", completed, total, self.width, self.height, size)
        self.progress.update(completed, total, size)
        self.record()

    def reset(self, size: int) -> None:
        self.progress.tile("reset", None, None, 0, 0, self.width, self.height, size)

    def record(self) -> None:
        update = [m for m in self.messages if type(m).__name__ == "ProgressUpdate"][-1]
        self.samples.append((self.now, update.estimated_remaining_seconds, self.stage))

    def check_against_truth(self, finished_at: float, *, after: float = 0.0) -> None:
        """Every estimate is finite and not negative; later ones sit within a factor of two (a quarter after half the job)."""
        total = finished_at - 1000.0
        for time, estimate, _stage in self.samples:
            assert math.isfinite(estimate) and estimate >= 0
            true = finished_at - time
            if estimate == 0 or time - 1000.0 < after * total:
                continue
            fraction = (time - 1000.0) / total
            factor = 2.0 if fraction < 0.5 else 1.25
            assert true / factor <= estimate <= true * factor, (
                f"at {fraction:.0%}: shows {estimate:.1f}s, {true:.1f}s remain"
            )


def _grid(run: Run, count: int, durations, size: int = 64, first: int = 1, total=None) -> None:
    total = total or count
    for number in range(first, first + count):
        run.tile(number, total, 0, 0, size, durations(number))


def test_the_first_slow_tile_does_not_inflate_the_estimate(monkeypatch):
    # The first tile pays for compiling the model: 30 s, then half a second per tile.
    run = Run(monkeypatch, width=640, height=640)
    run.start_stage(0)
    _grid(run, 100, lambda n: 30.0 if n == 1 else 0.5)
    run.check_against_truth(run.now)
    first = run.samples[0][1]
    # With one tile done there is no steady rate yet: say nothing rather than 2970 s.
    assert first == 0
    # After 10 % of the tiles the number is within a factor of two of the truth.
    assert any(estimate > 0 for _t, estimate, _s in run.samples[:12])


def test_an_estimate_needs_evidence_before_it_is_shown(monkeypatch):
    run = Run(monkeypatch)
    run.start_stage(0)
    _grid(run, 3, lambda n: 1.0, total=100)
    assert [estimate for _t, estimate, _s in run.samples][:2] == [0, 0]


def test_one_slow_tile_among_steady_ones_does_not_swing_the_estimate(monkeypatch):
    run = Run(monkeypatch)
    run.start_stage(0)
    _grid(run, 100, lambda n: 20.0 if n == 50 else 1.0)
    previous = None
    for _time, estimate, _stage in run.samples:
        if previous and estimate:
            assert estimate <= previous * 1.5 + 5
        previous = estimate or previous
    assert max(estimate for _t, estimate, _s in run.samples[10:49]) < 100


def test_a_fix_stage_does_not_claim_to_be_the_whole_job(monkeypatch):
    # Fix: 16 tiles at 0.1 s. Upscale: 16 tiles at 3 s. The old number during the fix
    # stage was a second or two; the job needs another 48 s.
    run = Run(monkeypatch, stage_count=2, width=256, height=256)
    run.start_stage(0)
    _grid(run, 16, lambda n: 0.1)
    assert all(estimate == 0 for _t, estimate, stage in run.samples if stage == 0)
    run.start_stage(1)
    _grid(run, 16, lambda n: 3.0)
    run.check_against_truth(run.now)
    assert any(estimate > 0 for _t, estimate, stage in run.samples if stage == 1)


def test_memory_back_off_keeps_the_measured_speed_and_restarts_the_work(monkeypatch):
    # 8 x 8 tiles of 128 px, 1 s each. After 32 tiles the tile size halves: 16 x 16 tiles
    # of 64 px that take a quarter of a second each, so the 64 s of work start again.
    run = Run(monkeypatch, width=1024, height=1024)
    run.start_stage(0)
    _grid(run, 32, lambda n: 1.0, size=128, total=64)
    run.reset(64)
    run.samples.clear()
    _grid(run, 256, lambda n: 0.25, size=64, total=256)
    run.check_against_truth(run.now, after=0.1)
    assert all(estimate <= 80 for _t, estimate, _s in run.samples)


def test_a_clock_that_stands_still_or_runs_backwards_gives_no_nonsense(monkeypatch):
    run = Run(monkeypatch)
    run.start_stage(0)
    for number in range(1, 20):
        run.tile(number, 40, 0, 0, 64, 0.0 if number % 3 else -0.5)
    for _time, estimate, _stage in run.samples:
        assert math.isfinite(estimate) and estimate >= 0


def test_a_lasting_slowdown_is_followed(monkeypatch):
    # The GPU gives up after 40 tiles and the rest run on the CPU, ten times slower.
    run = Run(monkeypatch)
    run.start_stage(0)
    _grid(run, 100, lambda n: 0.2 if n <= 40 else 2.0)
    finish = run.now
    late = [(t, e) for t, e, _s in run.samples[70:-1]]
    for time, estimate in late:
        assert (finish - time) / 1.6 <= estimate <= (finish - time) * 1.6
