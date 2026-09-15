import json
import threading
import tracemalloc

import pytest

from localsr.automation import (
    FileFingerprint,
    Reporter,
    WatchLedger,
    _unique_output_path,
    _watch,
    build_automation_parser,
    discover_watch_files,
    run_automation_cli,
    stable_watch_candidates,
)


def test_watch_discovers_supported_files_but_never_its_output_or_partials(tmp_path):
    incoming = tmp_path / "incoming"
    output = incoming / "LocalSR output"
    incoming.mkdir()
    output.mkdir()
    good = incoming / "photo.PNG"
    good.write_bytes(b"image")
    (incoming / "upload.png.part").write_bytes(b"partial")
    (incoming / ".hidden.jpg").write_bytes(b"hidden")
    (incoming / "notes.txt").write_text("no", encoding="utf-8")
    (output / "photo_localsr_4x.png").write_bytes(b"own output")

    assert discover_watch_files(incoming, output) == [good.resolve()]


def test_watch_waits_for_unchanged_fingerprint_and_persists_deduplication(tmp_path):
    incoming = tmp_path / "incoming"
    output = tmp_path / "output"
    incoming.mkdir()
    output.mkdir()
    source = incoming / "photo.png"
    source.write_bytes(b"first")
    ledger_path = output / ".state.json"
    ledger = WatchLedger(ledger_path)
    observations = {}

    assert not stable_watch_candidates(
        [source], ledger=ledger, observations=observations, stable_seconds=2, now=10
    )
    source.write_bytes(b"changed")
    assert not stable_watch_candidates(
        [source], ledger=ledger, observations=observations, stable_seconds=2, now=12
    )
    ready = stable_watch_candidates(
        [source], ledger=ledger, observations=observations, stable_seconds=2, now=14
    )
    assert [path for path, _ in ready] == [source]
    ledger.mark_completed(*ready[0])

    restarted = WatchLedger(ledger_path)
    assert restarted.is_completed(source, FileFingerprint.from_path(source))
    assert not stable_watch_candidates(
        [source], ledger=restarted, observations={}, stable_seconds=0, now=20
    )


def test_large_watch_queue_remains_bounded_without_running_inference(tmp_path):
    incoming = tmp_path / "incoming"
    output = tmp_path / "output"
    incoming.mkdir()
    output.mkdir()
    for index in range(2_000):
        (incoming / f"fixture-{index:04}.png").touch()

    tracemalloc.start()
    files = discover_watch_files(incoming, output)
    ledger = WatchLedger(output / ".state.json")
    observations = {}
    stable_watch_candidates(
        files, ledger=ledger, observations=observations, stable_seconds=0, now=1
    )
    ready = stable_watch_candidates(
        files, ledger=ledger, observations=observations, stable_seconds=0, now=2
    )
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert len(files) == len(ready) == len(observations) == 2_000
    assert peak < 32 * 1024 * 1024


def test_output_allocation_never_overwrites_source_or_previous_result(tmp_path):
    source = tmp_path / "photo.png"
    source.write_bytes(b"source")
    first = tmp_path / "photo_localsr_4x.png"
    first.write_bytes(b"previous")
    output = _unique_output_path(
        source,
        tmp_path,
        output_format="png",
        scale=4,
        video_container="mp4",
    )
    assert output.name == "photo_localsr_4x_1.png"


def test_automation_parser_exposes_process_benchmark_and_watch_commands():
    parser = build_automation_parser()
    process = parser.parse_args(["process", "a.png", "--output", "out"])
    benchmark = parser.parse_args(["benchmark", "--json"])
    watch = parser.parse_args(["watch", "incoming", "--output", "out", "--once"])
    assert process.command == "process"
    assert benchmark.command == "benchmark" and benchmark.json
    assert watch.command == "watch" and watch.once
    assert process.video_codec == "av1" and process.external_ffmpeg == ""


@pytest.mark.parametrize(
    "arguments,message",
    [
        (["--video-codec", "ffv1"], "MKV"),
        (["--video-codec", "h264"], "--external-ffmpeg"),
    ],
)
def test_automation_rejects_codec_choices_that_cannot_run(arguments, message):
    from localsr.automation import AutomationError, _validate_automation_args

    args = build_automation_parser().parse_args(["process", "a.mp4", "--output", "out", *arguments])
    with pytest.raises(AutomationError, match=message):
        _validate_automation_args(args)


def test_json_reporter_emits_machine_readable_json(capsys):
    Reporter(json_lines=True).emit("progress", completed=2, total=3)
    assert json.loads(capsys.readouterr().out) == {
        "completed": 2,
        "event": "progress",
        "total": 3,
    }


def test_watch_once_waits_for_stability_then_processes_and_records(tmp_path):
    incoming = tmp_path / "incoming"
    output = tmp_path / "output"
    incoming.mkdir()
    source = incoming / "photo.png"
    source.write_bytes(b"stable fixture")
    args = build_automation_parser().parse_args(
        [
            "watch",
            str(incoming),
            "--output",
            str(output),
            "--stable-seconds",
            "1",
            "--poll-seconds",
            "1",
            "--once",
        ]
    )

    class FakeRunner:
        def __init__(self):
            self.reporter = Reporter(json_lines=True)
            self.cancel_event = threading.Event()
            self.processed = []

        def process(self, path, destination, **_options):
            self.processed.append((path, destination))

    runner = FakeRunner()
    now = [0.0]
    result = _watch(
        args,
        runner,
        "cpu",
        clock=lambda: now[0],
        sleeper=lambda seconds: now.__setitem__(0, now[0] + seconds),
    )

    assert result == 0
    assert runner.processed == [(source.resolve(), output.resolve())]
    ledger = WatchLedger(output / ".localsr-watch-state.json")
    assert ledger.is_completed(source, FileFingerprint.from_path(source))


def test_watch_reports_processing_cancellation_without_mislabeling_it_as_failure(tmp_path, capsys):
    incoming = tmp_path / "incoming"
    output = tmp_path / "output"
    incoming.mkdir()
    source = incoming / "photo.png"
    source.write_bytes(b"stable fixture")
    args = build_automation_parser().parse_args(
        [
            "watch",
            str(incoming),
            "--output",
            str(output),
            "--stable-seconds",
            "0",
            "--poll-seconds",
            "1",
            "--once",
        ]
    )

    class CancellingRunner:
        def __init__(self):
            self.reporter = Reporter(json_lines=True)
            self.cancel_event = threading.Event()

        def process(self, *_args, **_options):
            raise InterruptedError("fixture cancellation")

    runner = CancellingRunner()
    now = [0.0]
    result = _watch(
        args,
        runner,
        "cpu",
        clock=lambda: now[0],
        sleeper=lambda seconds: now.__setitem__(0, now[0] + seconds),
    )
    events = [json.loads(line)["event"] for line in capsys.readouterr().out.splitlines()]

    assert result == 130
    assert "cancelled" in events
    assert "job_failed" not in events


def test_process_command_isolates_input_failures_and_continues(monkeypatch, tmp_path):
    calls = []

    class FakeRunner:
        def __init__(self, reporter, cancel_event):
            self.reporter = reporter
            self.cancel_event = cancel_event

        def process(self, source, _output, **_options):
            calls.append(source)
            if source.name == "broken.png":
                raise RuntimeError("fixture failure")

        def close(self):
            pass

    monkeypatch.setattr("localsr.automation.AutomationRunner", FakeRunner)
    monkeypatch.setattr("localsr.automation.select_device", lambda _device: "cpu")
    result = run_automation_cli(
        [
            "process",
            str(tmp_path / "broken.png"),
            str(tmp_path / "good.png"),
            "--output",
            str(tmp_path / "out"),
            "--json",
        ]
    )

    assert result == 1
    assert [path.name for path in calls] == ["broken.png", "good.png"]


def test_process_command_reports_cooperative_cancellation_as_130(monkeypatch, tmp_path):
    events = []

    class FakeRunner:
        def __init__(self, reporter, cancel_event):
            self.reporter = reporter
            self.cancel_event = cancel_event

        def process(self, *_args, **_options):
            raise InterruptedError("fixture cancellation")

        def close(self):
            events.append("closed")

    monkeypatch.setattr("localsr.automation.AutomationRunner", FakeRunner)
    monkeypatch.setattr("localsr.automation.select_device", lambda _device: "cpu")
    result = run_automation_cli(
        ["process", str(tmp_path / "photo.png"), "--output", str(tmp_path / "out")]
    )

    assert result == 130
    assert events == ["closed"]


def test_watch_rejects_non_finite_polling_without_starting(monkeypatch, tmp_path):
    closed = []

    class FakeRunner:
        def __init__(self, reporter, cancel_event):
            pass

        def close(self):
            closed.append(True)

    monkeypatch.setattr("localsr.automation.AutomationRunner", FakeRunner)
    result = run_automation_cli(
        [
            "watch",
            str(tmp_path),
            "--output",
            str(tmp_path / "out"),
            "--poll-seconds",
            "nan",
        ]
    )
    assert result == 2
    assert closed == [True]
