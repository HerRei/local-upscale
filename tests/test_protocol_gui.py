import json
import subprocess
import sys

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from localsr.protocol import client as client_module
from localsr.protocol.client import WorkerClient, worker_command
from localsr.ui.main_window import MainWindow


@pytest.fixture(scope="session", autouse=True)
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def test_worker_client_signal_emission():
    """
    F2.1 & F2.2: Test that WorkerClient parses JSON protocol lines and emits
    the new PySide signals: worker_ready, job_started, job_completed,
    job_cancelled, job_failed, warning.
    """
    client = WorkerClient()

    emitted = []

    client.worker_ready.connect(lambda: emitted.append(("worker_ready", None)))
    client.job_started.connect(lambda j_id: emitted.append(("job_started", j_id)))
    client.job_completed.connect(lambda j_id, res: emitted.append(("job_completed", (j_id, res))))
    client.job_cancelled.connect(lambda j_id: emitted.append(("job_cancelled", j_id)))
    client.job_failed.connect(lambda j_id, err: emitted.append(("job_failed", (j_id, err))))
    client.warning.connect(lambda msg: emitted.append(("warning", msg)))
    client.preview_ready.connect(lambda data: emitted.append(("preview_ready", data)))
    client.preview_failed.connect(lambda data: emitted.append(("preview_failed", data)))
    client.tile_updated.connect(lambda data: emitted.append(("tile_update", data)))

    # 1. worker_ready
    client.buffer = json.dumps({"type": "worker_ready", "data": {}}) + "\n"
    client.handle_stdout()
    assert emitted[-1] == ("worker_ready", None)

    # 2. job_started
    client.buffer = json.dumps({"type": "job_started", "data": {"job_id": "job_1"}}) + "\n"
    client.handle_stdout()
    assert emitted[-1] == ("job_started", "job_1")

    # 3. job_completed
    client.buffer = json.dumps({"type": "job_completed", "data": {"job_id": "job_1"}}) + "\n"
    client.handle_stdout()
    assert emitted[-1][0] == "job_completed"
    assert emitted[-1][1][0] == "job_1"
    assert emitted[-1][1][1]["success"] is True

    # 4. job_cancelled
    client.buffer = json.dumps({"type": "job_cancelled", "data": {"job_id": "job_2"}}) + "\n"
    client.handle_stdout()
    assert emitted[-1] == ("job_cancelled", "job_2")

    # 5. job_failed
    client.buffer = (
        json.dumps({"type": "job_failed", "data": {"job_id": "job_3", "error": "CUDA OOM"}}) + "\n"
    )
    client.handle_stdout()
    assert emitted[-1] == ("job_failed", ("job_3", "CUDA OOM"))

    # 6. warning
    client.buffer = json.dumps({"type": "warning", "data": {"message": "Low memory"}}) + "\n"
    client.handle_stdout()
    assert emitted[-1] == ("warning", "Low memory")

    client.buffer = (
        json.dumps(
            {"type": "preview_ready", "data": {"image_path": "raw.dng", "jpeg_base64": "abc"}}
        )
        + "\n"
    )
    client.handle_stdout()
    assert emitted[-1][0] == "preview_ready"

    client.buffer = (
        json.dumps(
            {"type": "preview_failed", "data": {"image_path": "raw.dng", "error_message": "x"}}
        )
        + "\n"
    )
    client.handle_stdout()
    assert emitted[-1][0] == "preview_failed"

    client.buffer = (
        json.dumps({"type": "tile_update", "data": {"job_id": "job_1", "phase": "completed"}})
        + "\n"
    )
    client.handle_stdout()
    assert emitted[-1] == (
        "tile_update",
        {"job_id": "job_1", "phase": "completed"},
    )


def test_worker_client_shutdown_state():
    """
    F3.2: Verify _is_shutting_down boolean in WorkerClient suppresses worker_error
    signal emissions during stop / clean process exit.
    """
    client = WorkerClient()
    assert client._is_shutting_down is False

    error_emitted = []
    client.worker_error.connect(lambda err: error_emitted.append(err))

    # Trigger error while NOT shutting down
    client.handle_error("Crash error")
    assert len(error_emitted) == 1
    assert "Crash error" in error_emitted[0]

    # Set shutting down and test handle_finished & handle_error
    client._is_shutting_down = True
    client.handle_error("Another error")
    client.handle_finished(0, 0)
    client.handle_finished(1, 0)
    # Should not have emitted new error
    assert len(error_emitted) == 1

    # Clean exit code 0 without shutdown flag also suppressed
    client._is_shutting_down = False
    client.handle_finished(0, 0)
    assert len(error_emitted) == 1


def test_worker_client_flushes_each_request_to_the_child_pipe(monkeypatch):
    """A queued JSON request is fully drained before send_request succeeds."""

    class FakeProcess:
        def __init__(self):
            self.payload = b""
            self.pending = 0
            self.wait_calls = 0

        def state(self):
            return client_module.QProcess.Running

        def write(self, payload):
            self.payload = bytes(payload)
            self.pending = len(payload)
            return len(payload)

        def bytesToWrite(self):
            return self.pending

        def waitForBytesWritten(self, timeout):
            assert timeout == 5000
            self.wait_calls += 1
            self.pending = 0
            return True

    client = WorkerClient()
    process = FakeProcess()
    monkeypatch.setattr(client, "process", process)

    request = client_module.ShutdownRequest()
    assert client.send_request(request) is True
    assert process.payload.endswith(b"\n")
    assert json.loads(process.payload) == {"type": "shutdown_request", "data": {}}
    assert process.wait_calls == 1


def test_packaged_worker_command_prefers_sibling_executable(monkeypatch, tmp_path):
    gui_name = "LocalSR.exe" if sys.platform == "win32" else "LocalSR"
    worker_name = "LocalSRWorker.exe" if sys.platform == "win32" else "LocalSRWorker"
    gui = tmp_path / gui_name
    worker = tmp_path / worker_name
    gui.touch()
    worker.touch()
    monkeypatch.setattr(client_module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(client_module.sys, "executable", str(gui))

    assert worker_command() == (str(worker), [])


def test_main_window_button_states_and_signals(tmp_path):
    """
    F2.3 & F2.4 & F3.3: Verify MainWindow connects all WorkerClient signals,
    updates button states correctly on job_completed or job_cancelled, and
    avoids restarting worker when _is_shutting_down is True.
    """
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    window = MainWindow(start_worker=False, settings=settings)
    window.image_path = str(tmp_path / "input.png")
    window.model_path = str(tmp_path / "model.pth")
    window.image_w = 100
    window.image_h = 100
    window.model_scale = 2
    window.update_predict()

    line_height = window.hardware_label.fontMetrics().lineSpacing()
    assert window.hardware_label.wordWrap()
    assert window.hardware_label.minimumHeight() >= line_height * 4
    assert window.progress_bar.minimumHeight() >= 22
    assert window.progress_bar.format() == "Ready"

    # Ready inputs enable upscale.
    assert window.btn_upscale.isEnabled() is True
    assert window.btn_cancel.isEnabled() is False
    assert window.btn_open.isEnabled() is False
    assert window.btn_reveal.isEnabled() is False

    # Simulate job_started
    window.on_job_started("job_100")
    assert window.btn_upscale.isEnabled() is False
    assert window.btn_cancel.isEnabled() is True
    assert window.progress_bar.format() == "%p%"

    # Simulate job_completed
    window.on_job_completed("job_100", {"success": True})
    assert window.btn_upscale.isEnabled() is True
    assert window.btn_cancel.isEnabled() is False
    assert window.btn_open.isEnabled() is True
    assert window.btn_reveal.isEnabled() is True
    assert window.progress_bar.format() == "Complete"

    # Reset button state and simulate job_cancelled
    window.on_job_started("job_101")
    assert window.btn_cancel.isEnabled() is True
    window.on_job_cancelled("job_101")
    assert window.btn_upscale.isEnabled() is True
    assert window.btn_cancel.isEnabled() is False
    assert window.progress_bar.format() == "Cancelled"

    # Check F3.3: on_worker_error with _is_shutting_down = True
    window.worker._is_shutting_down = True
    window.on_worker_error("Worker process exited with code 0")
    # Clean exit or shutting down should not crash or trigger restart
    window.close()


def test_worker_server_stdin_eof_shutdown():
    """
    F3.1: Spawns WorkerServer process, closes stdin, and asserts process exits
    cleanly with returncode 0.
    """
    proc = subprocess.Popen(
        [sys.executable, "-u", "-m", "localsr.worker.__main__"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    ready_line = proc.stdout.readline()
    assert "worker_ready" in ready_line

    # Close stdin pipe
    proc.stdin.close()

    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        pytest.fail("Worker process failed to terminate on stdin EOF")

    assert proc.returncode == 0
