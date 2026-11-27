"""Progress weighted by what each step takes on this computer, learned from earlier jobs."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from localsr.core.pipeline import ImagePipeline, PipelineStage
from localsr.core.speed_table import MAX_ENTRIES, SpeedTable
from localsr.worker.job_progress import ImageJobProgress

WIDTH, HEIGHT, SCALE = 944, 630, 4
STAGES = (
    PipelineStage("restore", "fbcnn_color"),
    PipelineStage("restore", "ddcolor_modelscope"),
    PipelineStage("restore", "nafnet_sidd_width64"),
    PipelineStage("upscale", "span_photo_x4"),
)
# The owner's real job: seconds each step took, and a pause before its first tile (loading).
REAL_SECONDS = (2.6, 8.4, 4.5, 1.3)
LOADING = (0.7, 1.9, 0.4, 0.2)
TILES = (6, 1, 6, 8)
MEGAPIXELS = (WIDTH * HEIGHT / 1e6,) * 3 + (WIDTH * HEIGHT * SCALE * SCALE / 1e6,)
BACKEND = ("mps+coreml", "float16")


@pytest.fixture(autouse=True)
def quiet_memory(monkeypatch):
    monkeypatch.setattr("localsr.worker.job_progress.get_memory_snapshot", lambda _: {})


def speeds(real=REAL_SECONDS) -> list[float]:
    """Seconds per unit of work for each step: per megapixel, or per run for DDColor."""
    return [
        seconds / (1.0 if index == 1 else MEGAPIXELS[index]) for index, seconds in enumerate(real)
    ]


def teach(table: SpeedTable, real=REAL_SECONDS, which=(0, 1, 2, 3)) -> None:
    for index in which:
        table.record(STAGES[index].model_id, *BACKEND, "mps|", speeds(real)[index])


class Job:
    """One simulated image job on a fake clock, as the worker reports it."""

    def __init__(self, table: SpeedTable, real=REAL_SECONDS, backend=True):
        self.now = 500.0
        self.real = real
        self.messages: list = []
        self.progress = ImageJobProgress(
            "job",
            ImagePipeline(STAGES, 3),
            "mps",
            self.messages.append,
            Mock(),
            clock=lambda: self.now,
            backend=(lambda stage: BACKEND) if backend else None,
            speed_table=table,
        )
        self.progress.configure_work(WIDTH, HEIGHT, SCALE)
        # (time, bar, remaining) after every progress message
        self.samples: list[tuple[float, float, float]] = []
        self.stage_ends: list[tuple[float, float]] = []
        self.started = self.now

    def run(self, cancel_in: int | None = None, complete_last: bool = True) -> None:
        for index, stage in enumerate(STAGES):
            self.stage(index, stage, cancel=cancel_in == index, complete=complete_last)
            if cancel_in == index:
                return

    def stage(self, index, stage, *, cancel=False, complete=True, reset_at=None) -> None:
        self.progress.start(index, stage)
        self.now += LOADING[index]
        tiles = TILES[index]
        out_width = WIDTH * (SCALE if index == 3 else 1)
        out_height = HEIGHT * (SCALE if index == 3 else 1)
        height = out_height // tiles
        for number in range(1, tiles + 1):
            tile = SimpleNamespace(
                out_x=0, out_y=(number - 1) * height, out_w=out_width, out_h=height
            )
            args = (out_width, out_height, height)
            self.progress.tile("started", tile, None, number - 1, tiles, *args)
            self.now += self.real[index] / tiles
            if reset_at == number:
                self.progress.tile("reset", None, None, 0, 0, *args)
            self.progress.tile("completed", tile, None, number, tiles, *args)
            self.progress.update(number, tiles, height)
            update = self.messages[-1]
            self.samples.append((self.now, update.percentage, update.estimated_remaining_seconds))
            if cancel and number == tiles // 2:
                return
        if complete:
            self.progress.complete(index, stage)
        self.stage_ends.append((self.now, self.samples[-1][1]))


def table_at(tmp_path) -> SpeedTable:
    return SpeedTable(tmp_path / "table.json")


def check_shares_and_time_left(job: Job, *, bar_tolerance: float, left_tolerance: float):
    total = sum(job.real)
    elapsed = 0.0
    for (_, bar), seconds in zip(job.stage_ends, job.real, strict=True):
        elapsed += seconds
        expected = elapsed / total * 100.0
        assert bar == pytest.approx(expected, abs=bar_tolerance)
    assert job.samples[-1][1] == 100.0
    assert job.samples[-1][2] == 0
    finish = job.stage_ends[-1][0]
    # From the second step on, the time left tracks the truth (the load before a step is
    # not part of the estimate, so allow for it).
    late = [s for s in job.samples if s[0] > job.stage_ends[0][0] + 0.01 and s[1] < 100.0]
    assert late
    for now, _, left in late:
        truth = finish - now
        assert abs(left - truth) <= max(left_tolerance * truth, 0.5) + 2.5, (now, left, truth)


def test_known_speeds_give_shares_that_match_the_real_times_and_a_true_time_left(tmp_path):
    table = table_at(tmp_path)
    teach(table)
    job = Job(table)
    assert job.progress.weights_in_seconds
    # The weights are the expected seconds themselves.
    assert job.progress.weights == pytest.approx(list(REAL_SECONDS), rel=1e-6)
    job.run()
    check_shares_and_time_left(job, bar_tolerance=1.0, left_tolerance=0.2)
    # The old pixel weighting put the 2.6 s step at 10 %; the share of the real time is 16 %.
    assert job.stage_ends[0][1] == pytest.approx(2.6 / 16.8 * 100, abs=1.0)


def test_time_left_tracks_a_slower_computer_through_the_calibration(tmp_path):
    table = table_at(tmp_path)
    teach(table)
    job = Job(table, real=tuple(seconds * 2 for seconds in REAL_SECONDS))
    job.run()
    finish = job.stage_ends[-1][0]
    # After the first step the later steps are expected twice as long as the table says.
    after_first = [s for s in job.samples if job.stage_ends[0][0] < s[0] and s[1] < 100.0]
    for now, _, left in after_first:
        truth = finish - now
        assert left == pytest.approx(truth, rel=0.25, abs=3.0)


def test_unknown_models_fall_back_and_the_first_run_teaches_the_second(tmp_path):
    table = table_at(tmp_path)
    first = Job(table)
    assert not first.progress.weights_in_seconds
    first.run()
    assert first.samples[-1][1] == 100.0
    learned = {entry["subject"]: entry for entry in table._load().values()}
    assert set(learned) == {stage.model_id for stage in STAGES}
    assert all(entry["backend"] == "mps+coreml" for entry in learned.values())
    assert all(entry["precision"] == "float16" for entry in learned.values())
    for index, stage in enumerate(STAGES):
        # The first tile (warm-up) is left out of a stage of several tiles, so the speed
        # lies at or under the plain one.
        assert learned[stage.model_id]["seconds"] <= speeds()[index] * 1.0001
        assert learned[stage.model_id]["seconds"] >= speeds()[index] * 0.75

    second = Job(table)
    assert second.progress.weights_in_seconds
    second.run()
    check_shares_and_time_left(second, bar_tolerance=2.0, left_tolerance=0.25)


def test_partly_known_job_scales_the_unknown_steps_by_the_median_ratio(tmp_path):
    table = table_at(tmp_path)
    teach(table, which=(0, 2))
    job = Job(table)
    fallback = [
        stage_work * factor
        for stage_work, factor in zip(
            (WIDTH * HEIGHT, 512 * 512, WIDTH * HEIGHT, WIDTH * HEIGHT * 16),
            (_factor(stage) for stage in STAGES),
            strict=True,
        )
    ]
    weights = job.progress.weights
    assert weights[0] == pytest.approx(speeds()[0] * MEGAPIXELS[0])
    assert weights[2] == pytest.approx(speeds()[2] * MEGAPIXELS[2])
    ratios = sorted(weights[i] / fallback[i] for i in (0, 2))
    median = (ratios[0] + ratios[1]) / 2
    assert weights[1] == pytest.approx(fallback[1] * median)
    assert weights[3] == pytest.approx(fallback[3] * median)


def _factor(stage: PipelineStage) -> float:
    from localsr.core.model_catalog import CATALOG_BY_ID

    return CATALOG_BY_ID[stage.model_id].time_factor


def test_a_corrupt_table_is_ignored_and_rewritten(tmp_path):
    table = table_at(tmp_path)
    table.path.write_text("{ this is not json", encoding="utf-8")
    first = Job(table)
    assert not first.progress.weights_in_seconds
    first.run()
    assert first.samples[-1][1] == 100.0
    data = json.loads(table.path.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert len(data["entries"]) == 4
    assert Job(table).progress.weights_in_seconds


@pytest.mark.parametrize(
    "content",
    [
        "[1, 2, 3]",
        '{"version": 99, "entries": {}}',
        '{"version": 1, "entries": [1]}',
        '{"version": 1, "entries": {"a": {"seconds": "slow"}, "b": 4}}',
        "",
    ],
)
def test_a_foreign_table_reads_as_empty_and_is_replaced_on_the_next_measurement(tmp_path, content):
    table = table_at(tmp_path)
    table.path.write_text(content, encoding="utf-8")
    assert table.lookup("fbcnn_color", "mps|") is None
    table.record("fbcnn_color", *BACKEND, "mps|", 3.0)
    assert table.lookup("fbcnn_color", "mps|") == 3.0
    assert len(json.loads(table.path.read_text(encoding="utf-8"))["entries"]) == 1


def test_an_unwritable_table_does_not_fail_the_job(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    table = SpeedTable(blocker / "inside" / "table.json")
    job = Job(table)
    job.run()
    assert job.samples[-1][1] == 100.0


def test_speeds_move_by_an_exponential_average(tmp_path):
    table = table_at(tmp_path)
    table.record("m", *BACKEND, "mps|", 4.0)
    table.record("m", *BACKEND, "mps|", 2.0)
    assert table.lookup("m", "mps|") == pytest.approx(3.0)
    # Another backend of the same model is a separate entry.
    table.record("m", "cpu", "float32", "cpu|", 20.0)
    assert table.lookup("m", "cpu|") == 20.0
    assert table.lookup("m", "mps|") == pytest.approx(3.0)


def test_the_table_stays_bounded_and_drops_the_oldest(tmp_path, monkeypatch):
    table = table_at(tmp_path)
    clock = iter(range(1, 10_000))
    monkeypatch.setattr("localsr.core.speed_table.time.time", lambda: float(next(clock)))
    for number in range(MAX_ENTRIES + 25):
        table.record(f"m{number}", "cpu", "float32", "cpu|", 1.0)
    entries = table._load()
    assert len(entries) == MAX_ENTRIES
    assert table.lookup("m0", "cpu|") is None
    assert table.lookup(f"m{MAX_ENTRIES + 24}", "cpu|") == 1.0


def test_stopped_restarted_and_unfinished_steps_teach_nothing(tmp_path):
    table = table_at(tmp_path)
    cancelled = Job(table)
    cancelled.run(cancel_in=0)
    assert table._load() == {}

    restarted = Job(table)
    restarted.stage(0, STAGES[0], reset_at=2)
    assert table._load() == {}

    skipped = Job(table)
    skipped.progress.start(0, STAGES[0])
    skipped.progress.complete(0, STAGES[0])
    assert table._load() == {}


def test_the_bar_never_goes_back_and_reaches_100_only_at_the_last_tile(tmp_path):
    table = table_at(tmp_path)
    teach(table)
    job = Job(table)
    job.run()
    bars = [bar for _, bar, _ in job.samples]
    assert bars == sorted(bars)
    assert max(bars[:-1]) < 100.0
    assert bars[-1] == 100.0


def test_first_measurement_is_awaited_before_a_time_left_is_shown(tmp_path):
    table = table_at(tmp_path)
    teach(table)
    job = Job(table)
    job.stage(0, STAGES[0], complete=False)
    # Six tiles of 0.43 s: the first sample comes before any speed can be known.
    assert job.samples[0][2] == 0


def test_the_job_without_a_backend_label_uses_the_requested_device(tmp_path):
    table = table_at(tmp_path)
    job = Job(table, backend=False)
    job.run()
    entries = table._load().values()
    assert {entry["backend"] for entry in entries} == {"mps"}
    assert {entry["precision"] for entry in entries} == {""}


def test_a_face_stage_is_measured_together_with_its_primary(tmp_path):
    from localsr.core.steps import FACE_STAGE_KIND

    table = table_at(tmp_path)
    stages = (
        PipelineStage("upscale", "span_photo_x4"),
        PipelineStage(FACE_STAGE_KIND, "face_model"),
    )
    pipeline = ImagePipeline(stages, 0)
    assert pipeline.face is stages[1]
    now = [0.0]
    messages: list = []

    def make() -> ImageJobProgress:
        progress = ImageJobProgress(
            "job",
            pipeline,
            "cuda",
            messages.append,
            Mock(),
            clock=lambda: now[0],
            speed_table=table,
        )
        progress.configure_work(1000, 1000, 4)
        return progress

    first = make()
    assert not first.weights_in_seconds
    first.start(0, stages[0], span=2)
    for number in range(1, 5):
        tile = SimpleNamespace(out_x=0, out_y=0, out_w=4000, out_h=1000)
        first.tile("started", tile, None, number - 1, 4, 4000, 4000, 1000)
        now[0] += 2.0
        first.tile("completed", tile, None, number, 4, 4000, 4000, 1000)
        first.update(number, 4, 1000)
    first.complete(0, stages[0])
    first.complete(1, stages[1])
    (entry,) = table._load().values()
    assert entry["subject"] == "span_photo_x4+face_model"
    # 3 tiles after the warm-up tile took 6 s over 12 MP.
    assert entry["seconds"] == pytest.approx(0.5)

    second = make()
    assert second.weights_in_seconds
    # 16 MP at 0.5 s/MP is the primary's share; the face stage rides inside it.
    assert second.weights == pytest.approx([8.0, 0.0])
