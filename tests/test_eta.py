"""Remaining-time estimates stay near the truth for synthetic progress sequences.

Each test feeds the progress reporter the callbacks the engine makes, with a fake clock, and
compares every estimate it emits with the time that really remained in that simulation.
"""

from __future__ import annotations

import math
import random
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

from test_video_timing import make_vfr

from localsr.core import video_pipeline
from localsr.core.eta import RemainingTime
from localsr.core.pipeline import resolve_image_pipeline
from localsr.core.video_pipeline import VideoJobConfig, run_video_job
from localsr.worker.job_progress import ImageJobProgress
from localsr.worker.server import WorkerServer


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
        """Every estimate is finite and not negative; later ones sit near the truth.

        Within a factor of two in the first half of the job, a quarter after that.
        """
        total = finished_at - 1000.0
        for moment, estimate, _stage in self.samples:
            assert math.isfinite(estimate) and estimate >= 0
            true = finished_at - moment
            if estimate == 0 or moment - 1000.0 < after * total:
                continue
            fraction = (moment - 1000.0) / total
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
    for moment, estimate in late:
        assert (finish - moment) / 1.6 <= estimate <= (finish - moment) * 1.6


class Clock:
    now = 5000.0


class SlowFirstEngine:
    """Four tiles per frame; the model loads slowly and the first tile compiles for 30 s."""

    def __init__(self, clock, frame_seconds=2.0, tiles=4):
        self.clock, self.frame_seconds, self.tiles, self.frames = clock, frame_seconds, tiles, 0

    def load_model(self, *_args):
        self.clock.now += 20.0

    def process_frame(self, *, img_tensor, tile_callback, **_kwargs):
        first_frame = self.frames == 0
        self.frames += 1
        height, width = img_tensor.shape[-2:]
        for number in range(1, self.tiles + 1):
            tile_callback("started", None, None, number - 1, self.tiles, width, height, 64)
            self.clock.now += self.frame_seconds / self.tiles
            if first_frame and number == 1:
                self.clock.now += 30.0
            tile_callback("completed", None, None, number, self.tiles, width, height, 64)
        return (img_tensor * 255).round().byte().numpy()


def test_video_estimate_leaves_out_model_loading_and_the_first_frame(tmp_path, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(video_pipeline, "time", SimpleNamespace(perf_counter=lambda: clock.now))
    source = make_vfr(tmp_path / "clip.mp4", tuple(index * 40 for index in range(30)))
    tiles, frames = [], []
    run_video_job(
        VideoJobConfig(
            str(source),
            "test",
            str(tmp_path / "out.mp4"),
            SimpleNamespace(scale=1),
            "cpu",
            "fp32",
            64,
            4,
            False,
        ),
        SlowFirstEngine(clock),
        threading.Event(),
        progress_cb=lambda done, total, elapsed, remaining=None: frames.append(
            (clock.now, done, remaining)
        ),
        tile_progress_cb=lambda frame, total, done, count, elapsed, remaining, size: tiles.append(
            (clock.now, frame + done / count, remaining)
        ),
    )
    finished = clock.now
    shown = [(t, f, r) for t, f, r in tiles if r is not None]
    assert shown, "an estimate is shown once the speed is measured"
    for time_now, fraction, remaining in shown:
        true = finished - time_now
        assert math.isfinite(remaining) and remaining >= 0
        # The first frame (and so about 3 % of the job) is warm-up; after 10 % every
        # estimate is within a factor of two of what really remains.
        if fraction >= 3:
            assert true / 2 <= remaining <= true * 2, f"{remaining:.0f}s shown, {true:.0f}s left"
    # The frame-completed number is the same one, not a second formula.
    for time_now, _done, remaining in frames[3:-1]:
        assert remaining is not None
        assert (finished - time_now) / 2 <= remaining <= (finished - time_now) * 2


def test_temporal_estimate_leaves_out_model_loading_and_the_first_chunk(tmp_path, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(time, "monotonic", lambda: clock.now)
    source = make_vfr(tmp_path / "long.mp4", tuple(index * 40 for index in range(73)))
    messages = []
    monkeypatch.setattr("localsr.worker.server.send_message", messages.append)

    class Engine:
        calls = 0
        max_temporal_window = 9

        def process_frames(self, frames, **_kwargs):
            clock.now += 1.2 * len(frames) + (40.0 if self.calls == 0 else 0.0)
            self.calls += 1
            return frames

    def load(*_args):
        clock.now += 60.0
        return Engine()

    worker = SimpleNamespace(cancel_event=threading.Event(), _emit_live_preview=lambda _: None)
    WorkerServer._run_temporal_video_job(
        worker,
        "long",
        {
            "video_path": str(source),
            "output_video_path": str(tmp_path / "out.mp4"),
            "preview_enabled": False,
        },
        load,
    )
    done = [m for m in messages if type(m).__name__ == "VideoFrameCompleted"]
    finished = clock.now
    assert len(done) > 5
    # The job's clock started at 5000, so a report's time is 5000 plus its elapsed seconds.
    first = done[0]
    assert first.estimated_remaining_seconds == 0, "no speed is known after the warm-up chunk"
    # The last chunk may hold a single new frame that still costs a model window.
    for message in done[1:-2]:
        true = finished - (5000.0 + message.elapsed_seconds)
        assert true / 2 <= message.estimated_remaining_seconds <= true * 2


def test_a_sleep_inside_one_tile_does_not_poison_the_estimate(monkeypatch):
    # The computer sleeps for an hour during tile 50 on a clock that keeps counting.
    run = Run(monkeypatch)
    run.start_stage(0)
    _grid(run, 100, lambda n: 3600.0 if n == 50 else 1.0)
    for _moment, estimate, _stage in run.samples[48:]:
        assert estimate < 120


def _noisy(seed: int, count: int = 150, base: float = 0.5, warmup: float = 8.0):
    """Tile durations with +-30 % noise, a slow tile now and then, a slow first tile."""
    rng = random.Random(seed)
    durations = []
    for number in range(1, count + 1):
        seconds = base * (1 + rng.uniform(-0.3, 0.3))
        if rng.random() < 0.02:
            seconds *= 3
        durations.append(seconds + (warmup if number == 1 else 0.0))
    return durations


def test_estimates_from_noisy_tiles_are_within_the_accuracy_targets(monkeypatch):
    # After 15 % of the work the number is within 25 % of what remains, after 50 % within
    # 15 % (until the last fifth, where one slow tile left to come is a quarter of the rest).
    for seed in range(60):
        run = Run(monkeypatch, width=1500, height=1000)
        run.start_stage(0)
        count = 150
        for number, seconds in enumerate(_noisy(seed, count), 1):
            run.tile(number, count, 0, 0, 100, seconds)
        finish = run.now
        for number, (moment, estimate, _stage) in enumerate(run.samples, 1):
            fraction, true = number / count, finish - moment
            if fraction < 0.15 or fraction > 0.8 or estimate == 0:
                continue
            tolerance = 0.25 if fraction < 0.5 else 0.15
            assert abs(estimate - true) <= tolerance * true, (
                f"seed {seed} at {fraction:.0%}: shows {estimate:.1f}s, {true:.1f}s remain"
            )


def test_one_slower_tile_moves_the_estimate_by_its_share_of_the_work_so_far(monkeypatch):
    # Steady 1 s tiles, then one that takes 2 s at tile 60 of 100. The speed is the average
    # over the 59 steady tiles and this one, so the estimate may rise by about the extra
    # second spread over the work done (+1.7 %), not by the share of a recent window.
    run = Run(monkeypatch)
    run.start_stage(0)
    _grid(run, 59, lambda n: 1.0, total=100)
    before = run.samples[-1][1]
    _grid(run, 1, lambda n: 2.0, total=100, first=60)
    after = run.samples[-1][1]
    one_tile_less = before - 1.0
    assert abs(after - one_tile_less) <= 0.02 * before


def test_lanes_that_finish_tiles_a_moment_apart_do_not_slow_the_estimate(monkeypatch):
    # Two lanes at 1 s per tile finish within 10 ms of each other: reports come as 10 ms and
    # 990 ms intervals. The batch finishes 2 tiles per second, whatever the report spacing.
    run = Run(monkeypatch, width=1000, height=2000)
    run.start_stage(0)
    count = 200
    run.now += 5.0
    run.tile(1, count, 0, 0, 100, 0.0)
    for number in range(2, count + 1):
        run.tile(number, count, 0, 0, 100, 0.99 if number % 2 else 0.01)
    finish = run.now
    for number, (moment, estimate, _stage) in enumerate(run.samples, 1):
        if number > 0.1 * count and estimate:
            assert abs(estimate - (finish - moment)) <= 0.05 * (finish - moment) + 1.0


def test_a_lasting_speed_up_is_followed(monkeypatch):
    # A second lane joins after 40 tiles and the tiles take a fifth of the time.
    run = Run(monkeypatch)
    run.start_stage(0)
    _grid(run, 100, lambda n: 1.0 if n <= 40 else 0.2)
    finish = run.now
    for moment, estimate, _stage in run.samples[75:-1]:
        assert (finish - moment) / 1.6 <= estimate <= (finish - moment) * 1.6


class Batch:
    """Two photos of 100 tiles each, rendered by two lanes of different speed.

    The lanes take tiles from one queue in photo order, so a lane's completion belongs to
    whichever photo the tile was from, and the reports of the two lanes interleave.
    """

    def __init__(self, monkeypatch, speeds=(0.2, 0.55), warm=(1.0, 2.0), seed=3):
        monkeypatch.setattr("localsr.worker.job_progress.get_memory_snapshot", lambda _: {})
        self.now = 2000.0
        rng = random.Random(seed)
        self.messages: list[list] = [[], []]
        pipeline = _pipeline(1)
        self.rate = RemainingTime(2 * 100 * 64 * 64, warmup=len(speeds))
        self.photos = [
            ImageJobProgress(
                f"photo{index}",
                pipeline,
                "mps",
                self.messages[index].append,
                Mock(),
                clock=lambda: self.now,
                rate=self.rate,
            )
            for index in range(2)
        ]
        # (time of completion, lane) for every tile, from the lanes working side by side.
        free = [0.0] * len(speeds)
        events = []
        for number in range(200):
            lane = free.index(min(free))
            free[lane] += speeds[lane] * (1 + rng.uniform(-0.3, 0.3))
            if number < len(speeds):
                free[lane] += warm[lane]
            events.append(free[lane])
        self.events = sorted(events)
        self.samples: list[tuple[int, float, float]] = []  # (photo, time, estimate)

    def run(self):
        start, done = self.now, [0, 0]
        for number, moment in enumerate(self.events, 1):
            index = 0 if number <= 100 else 1
            self.now = start + moment
            if done[index] == 0:
                self.photos[index].start(0, self.photos[index].pipeline.primary)
            done[index] += 1
            tile = SimpleNamespace(out_x=0, out_y=0, out_w=64, out_h=64)
            args = (done[index], 100, 640, 640, 64)
            self.photos[index].tile("started", tile, None, done[index] - 1, 100, 640, 640, 64)
            self.rate.update(self.now, number * 64 * 64)
            self.photos[index].tile("completed", tile, b"", *args)
            self.photos[index].update(done[index], 100, 64)
            update = [m for m in self.messages[index] if type(m).__name__ == "ProgressUpdate"][-1]
            self.samples.append((index, self.now, update.estimated_remaining_seconds))
        return start + self.events[-1]


def test_a_batch_shares_one_speed_across_its_lanes_and_photos(monkeypatch):
    batch = Batch(monkeypatch)
    finish = batch.run()
    first_shown = {}
    previous = {}
    for index, moment, estimate in batch.samples:
        first_shown.setdefault(index, moment) if estimate else None
        if estimate and index in previous and moment > batch.events[14] + 2000.0:
            # The lanes' reports interleave, but the number only counts down (a tile's worth
            # of speed noise is the most it may rise by).
            assert estimate <= previous[index] * 1.04, "the estimate went up"
        if estimate:
            previous[index] = estimate
    # The second photo starts with the speed the batch has measured: no new "measuring".
    starts = [m for m in batch.samples if m[0] == 1][:2]
    assert all(estimate > 0 for _i, _t, estimate in starts)
    # Photo 1 ends when its last tile is done; what it shows is that photo's time at the
    # speed of both lanes together.
    ends = [moment for index, moment, _e in batch.samples if index == 0][-1]
    for index, moment, estimate in batch.samples:
        number = len([s for s in batch.samples if s[0] == index and s[1] <= moment])
        if index == 0 and number >= 15 and estimate:
            true = ends - moment
            assert abs(estimate - true) <= 0.25 * true + 1.0
    assert finish > ends
