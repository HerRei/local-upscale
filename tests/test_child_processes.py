"""FFmpeg and the system codec helper must not outlive the worker or a cancelled job."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from localsr.core.external_ffmpeg import ExternalFFmpeg, ExternalFFmpegError, _run

pytestmark = pytest.mark.skipif(os.name != "posix", reason="process groups and signals")

ROOT = Path(__file__).resolve().parents[1]


def stand_in(tmp_path: Path) -> tuple[Path, Path]:
    """A program that records its pid and then runs for minutes, like a long conversion."""
    pid_file = tmp_path / "child.pid"
    script = tmp_path / "ffmpeg"
    script.write_text(f"#!/bin/sh\necho $$ > '{pid_file}'\nexec sleep 300\n", encoding="utf-8")
    script.chmod(0o755)
    return script, pid_file


def read_pid(pid_file: Path, timeout: float = 10.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            text = pid_file.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            text = ""
        if text:
            return int(text)
        time.sleep(0.02)
    raise AssertionError("the stand-in program never started")


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie that nobody has collected yet has already stopped running.
    state = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True
    ).stdout.strip()
    return bool(state) and not state.startswith("Z")


def assert_gone(pid: int, timeout: float = 8.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not alive(pid):
            return
        time.sleep(0.05)
    os.kill(pid, signal.SIGKILL)
    raise AssertionError(f"process {pid} kept running")


def fake_ffmpeg(script: Path) -> ExternalFFmpeg:
    return ExternalFFmpeg(str(script), "7.0", frozenset())


def test_cancelling_a_conversion_ends_the_program(tmp_path):
    script, pid_file = stand_in(tmp_path)
    cancel = threading.Event()
    threading.Timer(0.5, cancel.set).start()
    with pytest.raises(InterruptedError):
        _run(fake_ffmpeg(script), ["-i", "x"], cancel_event=cancel, stage="test")
    assert_gone(read_pid(pid_file))


def test_an_error_while_a_conversion_runs_ends_the_program(tmp_path):
    script, pid_file = stand_in(tmp_path)
    script.write_text(
        f"#!/bin/sh\necho $$ > '{pid_file}'\necho frame=1\nexec sleep 300\n", encoding="utf-8"
    )

    def progress(_frame):
        raise RuntimeError("the progress receiver failed")

    with pytest.raises(RuntimeError, match="progress receiver"):
        _run(
            fake_ffmpeg(script),
            ["-i", "x"],
            cancel_event=None,
            progress=progress,
            stage="test",
        )
    assert_gone(read_pid(pid_file))


WORKER = textwrap.dedent(
    """
    import sys, threading, time
    from pathlib import Path
    from localsr.core.child_processes import stop_children_on_termination
    from localsr.core.external_ffmpeg import ExternalFFmpeg, _run
    from localsr.core.system_codecs import SystemCodecs

    mode, script, program = sys.argv[1], sys.argv[2], sys.argv[3]

    def run_system_helper():
        with SystemCodecs(script, 1)._process(["encode"]) as process:
            process.wait()

    stop_children_on_termination()
    conversion = threading.Thread(
        target=run_system_helper
        if program == "system"
        else lambda: _run(
            ExternalFFmpeg(script, "7.0", frozenset()),
            ["-i", "x"],
            cancel_event=None,
            stage="test",
        ),
        daemon=True,
    )
    conversion.start()
    if mode == "exit":
        time.sleep(1.0)  # the worker ends while the conversion still runs
        sys.exit(0)
    time.sleep(300)
    """
)


def start_worker(mode: str, script: Path, program: str) -> subprocess.Popen:
    """A worker as the host starts it: the leader of its own process group."""
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.Popen(
        [sys.executable, "-c", WORKER, mode, str(script), program],
        env=environment,
        start_new_session=True,
    )


@pytest.fixture
def worker(tmp_path):
    started = []

    def start(mode: str, program: str = "ffmpeg"):
        script, pid_file = stand_in(tmp_path)
        process = start_worker(mode, script, program)
        started.append(process)
        return process, read_pid(pid_file)

    yield start
    for process in started:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def test_the_program_shares_the_workers_process_group(worker):
    process, child = worker("wait")
    assert os.getpgid(child) == process.pid
    assert os.getsid(child) == os.getsid(process.pid)


def test_killing_the_workers_process_group_reaches_the_program(worker):
    process, child = worker("wait")
    os.killpg(process.pid, signal.SIGKILL)
    process.wait()
    assert_gone(child)


def test_a_terminated_worker_takes_the_program_along(worker):
    process, child = worker("wait")
    process.send_signal(signal.SIGTERM)
    process.wait(timeout=10)
    assert process.returncode == -signal.SIGTERM
    assert_gone(child)


def test_a_worker_that_exits_takes_the_program_along(worker):
    process, child = worker("exit")
    process.wait(timeout=10)
    assert_gone(child)


def test_a_failing_program_is_reported_not_left_running(tmp_path):
    script = tmp_path / "ffmpeg"
    script.write_text("#!/bin/sh\necho broken >&2\nexit 3\n", encoding="utf-8")
    script.chmod(0o755)
    with pytest.raises(ExternalFFmpegError, match="broken"):
        _run(fake_ffmpeg(script), ["-i", "x"], cancel_event=None, stage="test")


@pytest.mark.parametrize("mode", ["wait", "exit"])
def test_the_system_codec_helper_does_not_outlive_the_worker_either(worker, mode):
    process, child = worker(mode, "system")
    assert os.getpgid(child) == process.pid
    if mode == "wait":
        process.send_signal(signal.SIGTERM)
    process.wait(timeout=10)
    assert_gone(child)


@pytest.mark.parametrize(
    ("flag", "module"),
    [("--video-playback-preview", "localsr.core.video_playback"), (None, "localsr.worker.server")],
)
def test_the_worker_entry_points_stop_their_programs_on_termination(monkeypatch, flag, module):
    import importlib

    import localsr.core.child_processes as child_processes
    import localsr.core.gpu_guard as gpu_guard
    from localsr.worker import cli

    calls = []
    monkeypatch.setattr(child_processes, "stop_children_on_termination", lambda: calls.append(1))
    monkeypatch.setattr(gpu_guard, "settle", lambda: None)
    monkeypatch.setattr(
        importlib.import_module(module), "main", lambda *_args: calls.append(2) or 0
    )
    monkeypatch.setattr(sys, "argv", ["localsr"] + ([flag, "in", "out"] if flag else []))
    try:
        cli.main()
    except SystemExit:
        pass
    assert calls == [1, 2]
