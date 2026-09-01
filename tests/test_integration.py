import glob
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any

import pytest
import spandrel.architectures.ESRGAN as E
import torch
from PIL import Image
from PySide6.QtCore import QProcess, QSettings
from pytestqt.exceptions import TimeoutError as QtBotTimeoutError

from localsr.protocol.messages import InspectRequest
from localsr.ui.main_window import MainWindow


def create_dummy_esrgan_2x_model(pth_path: str) -> str:
    """Creates a real 2x ESRGAN model checkpoint using spandrel architecture."""
    model = E.ESRGAN(in_nc=3, out_nc=3, num_filters=16, num_blocks=1, scale=2)
    torch.save(model.state_dict(), pth_path)
    return pth_path


def create_test_image(path: str, width: int, height: int, color="red") -> str:
    """Creates a synthetic PNG image of given dimensions and color."""
    img = Image.new("RGB", (width, height), color=color)
    img.save(path)
    return path


def _ipc_timeout() -> float:
    """Allow constrained self-hosted CI VMs to import PyTorch before IPC begins."""
    return 120.0 if os.environ.get("CI") else 10.0


class WorkerSubprocessHarness:
    """Helper harness to spawn, interact with, and cleanly shut down a worker subprocess."""

    def __init__(self):
        environment = os.environ.copy()
        # Synthetic test checkpoints are created locally in this process and are
        # explicitly trusted for this isolated worker fixture.
        environment["LOCALSR_ALLOW_UNVERIFIED_CHECKPOINTS"] = "1"
        self.proc = subprocess.Popen(
            [sys.executable, "-u", "-m", "localsr.worker.__main__"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        )
        self.pid = self.proc.pid
        self._stdout_lines: queue.Queue[str | None] = queue.Queue()
        self._stdout_reader = threading.Thread(target=self._collect_stdout, daemon=True)
        self._stdout_reader.start()

    def _collect_stdout(self) -> None:
        """Move worker output into a queue so test timeouts cannot block on readline()."""
        try:
            for line in self.proc.stdout:
                self._stdout_lines.put(line)
        finally:
            self._stdout_lines.put(None)

    def read_message(self, timeout: float | None = None) -> dict[str, Any]:
        """Reads a single JSON message line from worker stdout."""
        if timeout is None:
            timeout = _ipc_timeout()
        try:
            line = self._stdout_lines.get(timeout=timeout)
        except queue.Empty as error:
            raise TimeoutError(
                f"Worker emitted no IPC message within {timeout:.1f}s "
                f"(returncode={self.proc.poll()})."
            ) from error
        if line is None:
            stderr = self.proc.stderr.read() if self.proc.poll() is not None else ""
            detail = f" Worker stderr: {stderr.strip()}" if stderr.strip() else ""
            raise RuntimeError(f"Subprocess stdout closed unexpectedly.{detail}")
        return json.loads(line.strip())

    def send_message(self, msg: dict[str, Any]):
        """Sends a JSON message line to worker stdin."""
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def wait_for_message(self, predicate, timeout: float) -> dict[str, Any]:
        """Wait for a matching IPC message without exceeding an absolute deadline."""
        deadline = time.monotonic() + timeout
        seen_types: list[str] = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"No matching worker IPC message within {timeout:.1f}s; observed={seen_types}."
                )
            try:
                message = self.read_message(timeout=remaining)
            except TimeoutError as error:
                raise TimeoutError(
                    f"No matching worker IPC message within {timeout:.1f}s; observed={seen_types}."
                ) from error
            message_type = str(message.get("type"))
            seen_types.append(message_type)
            if message_type == "job_failed":
                raise AssertionError(f"Worker reported job_failed: {message.get('data')}")
            if predicate(message):
                return message

    def wait_for_ready(self) -> dict[str, Any]:
        """Waits for and returns the worker_ready message."""
        msg = self.read_message(timeout=_ipc_timeout())
        assert msg.get("type") == "worker_ready", f"Expected worker_ready, got {msg}"
        return msg

    def shutdown(self, timeout=5.0) -> int:
        """Sends shutdown_request and waits for clean exit."""
        if self.proc.poll() is None:
            try:
                self.send_message({"type": "shutdown_request"})
            except (BrokenPipeError, OSError):
                pass
            try:
                self.proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        return self.proc.returncode

    def close(self):
        """Force termination if still running."""
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()


@pytest.fixture
def dummy_model(tmp_path):
    pth_path = str(tmp_path / "dummy_esrgan_2x.pth")
    create_dummy_esrgan_2x_model(pth_path)
    return pth_path


def test_f4_1_subprocess_launch():
    """F4.1: Test launching actual worker subprocess over stdin/stdout JSON lines IPC."""
    proc = subprocess.Popen(
        [sys.executable, "-u", "-m", "localsr.worker.__main__"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert proc.pid is not None and proc.pid > 0, "Worker subprocess must have a valid PID."

    # poll() is portable across Windows, macOS, and Linux.
    assert proc.poll() is None, "Worker process was not running after spawn."

    # Read ready line to avoid pipe deadlock
    ready_line = proc.stdout.readline()
    assert "worker_ready" in ready_line

    # Send shutdown
    proc.stdin.write(json.dumps({"type": "shutdown_request"}) + "\n")
    proc.stdin.flush()
    # Closing stdin also releases the worker's reader thread. Cold Windows
    # runners can need more than five seconds to unload PyTorch DLLs even
    # after the protocol loop has stopped, so keep this a bounded but
    # realistic clean-shutdown assertion.
    proc.stdin.close()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        pytest.fail("Worker did not exit within 15 seconds after shutdown_request")
    assert proc.returncode == 0, f"Worker process exit code should be 0, got {proc.returncode}"


def test_f4_2_worker_ready_signal():
    """F4.2: Test worker_ready signal detection on worker startup."""
    harness = WorkerSubprocessHarness()
    try:
        msg = harness.wait_for_ready()
        assert msg["type"] == "worker_ready", f"First IPC message must be worker_ready, got: {msg}"
        assert msg.get("data") == {}, (
            f"worker_ready data field must be empty dict {{}}, got: {msg.get('data')}"
        )
    finally:
        retcode = harness.shutdown()
        assert retcode == 0


def test_f4_3_dummy_job_execution(tmp_path, dummy_model):
    """F4.3: Test dummy job execution producing a real output image with exact 2x upscaled dimensions."""
    harness = WorkerSubprocessHarness()
    try:
        harness.wait_for_ready()

        in_img_path = create_test_image(str(tmp_path / "input_16x16.png"), 16, 16, color="blue")
        out_img_path = str(tmp_path / "output_32x32.png")

        job_req = {
            "type": "job_request",
            "data": {
                "job_id": "job_f4_3",
                "image_path": in_img_path,
                "model_path": dummy_model,
                "output_path": out_img_path,
                "output_format": "png",
                "device": "cpu",
                "tile_size": 64,
                "halo": 8,
                "precision": "fp32",
                "jpeg_quality": 98,
                "preserve_metadata": False,
                "safe_memory": False,
            },
        }

        harness.send_message(job_req)

        # Collect IPC responses until job_completed
        completed_msg = None
        started_msg = None
        start_time = time.time()

        while time.time() - start_time < 10.0:
            msg = harness.read_message()
            msg_type = msg.get("type")
            if msg_type == "job_started" and msg["data"]["job_id"] == "job_f4_3":
                started_msg = msg
            elif msg_type == "job_completed" and msg["data"]["job_id"] == "job_f4_3":
                completed_msg = msg
                break
            elif msg_type == "job_failed" and msg["data"]["job_id"] == "job_f4_3":
                pytest.fail(f"Job failed unexpectedly: {msg['data']}")

        assert started_msg is not None, "Worker must emit job_started signal."
        assert completed_msg is not None, "Worker must emit job_completed signal."
        assert os.path.exists(out_img_path), (
            f"Output image file must be written to disk at {out_img_path}"
        )

        with Image.open(out_img_path) as out_img:
            assert out_img.size == (32, 32), (
                f"2x upscale of 16x16 image must be exact (32, 32), got {out_img.size}"
            )

    finally:
        retcode = harness.shutdown()
        assert retcode == 0


def test_f4_4_job_cancellation(tmp_path, dummy_model):
    """F4.4: Test job cancellation (cancel_request) preventing final output file creation."""
    harness = WorkerSubprocessHarness()
    try:
        harness.wait_for_ready()

        # Use 512x512 image so multi-tile processing gives reliable window for cancel_request
        in_img_path = create_test_image(
            str(tmp_path / "input_512x512.png"), 512, 512, color="green"
        )
        out_img_path = str(tmp_path / "cancelled_output.png")

        job_req = {
            "type": "job_request",
            "data": {
                "job_id": "job_f4_4",
                "image_path": in_img_path,
                "model_path": dummy_model,
                "output_path": out_img_path,
                "output_format": "png",
                "device": "cpu",
                "tile_size": 64,
                "halo": 8,
                "precision": "fp32",
                "jpeg_quality": 98,
                "preserve_metadata": False,
                "safe_memory": True,
            },
        }

        harness.send_message(job_req)

        ipc_timeout = _ipc_timeout()

        # Wait for job_started, then send cancel_request.
        harness.wait_for_message(lambda message: message.get("type") == "job_started", ipc_timeout)

        harness.send_message({"type": "cancel_request", "data": {"job_id": "job_f4_4"}})

        # Read IPC responses until job_cancelled
        cancelled_msg = harness.wait_for_message(
            lambda message: (
                message.get("type") == "job_cancelled" and message["data"]["job_id"] == "job_f4_4"
            ),
            ipc_timeout,
        )

        assert cancelled_msg is not None, "Worker must emit job_cancelled signal."
        assert not os.path.exists(out_img_path), (
            "Cancelled job must NOT create final output image file."
        )

    finally:
        retcode = harness.shutdown()
        assert retcode == 0


def test_f4_5_subsequent_job_after_cancellation(tmp_path, dummy_model):
    """F4.5: Test execution and output verification of a subsequent job after cancellation."""
    harness = WorkerSubprocessHarness()
    try:
        harness.wait_for_ready()

        # Job 1: Cancelled
        in_img1 = create_test_image(str(tmp_path / "cancel_input.png"), 256, 256, color="yellow")
        out_img1 = str(tmp_path / "cancelled_output_1.png")

        job_req1 = {
            "type": "job_request",
            "data": {
                "job_id": "job_cancel_first",
                "image_path": in_img1,
                "model_path": dummy_model,
                "output_path": out_img1,
                "output_format": "png",
                "device": "cpu",
                "tile_size": 64,
                "halo": 8,
                "precision": "fp32",
                "jpeg_quality": 98,
                "preserve_metadata": False,
                "safe_memory": True,
            },
        }

        harness.send_message(job_req1)

        ipc_timeout = _ipc_timeout()

        harness.wait_for_message(lambda message: message.get("type") == "job_started", ipc_timeout)

        harness.send_message({"type": "cancel_request", "data": {"job_id": "job_cancel_first"}})

        harness.wait_for_message(
            lambda message: message.get("type") == "job_cancelled",
            ipc_timeout,
        )

        assert not os.path.exists(out_img1), "Cancelled job output file must not exist."

        # Job 2: Subsequent Job
        in_img2 = create_test_image(
            str(tmp_path / "subsequent_input_16x16.png"), 16, 16, color="red"
        )
        out_img2 = str(tmp_path / "subsequent_output_32x32.png")

        job_req2 = {
            "type": "job_request",
            "data": {
                "job_id": "job_subsequent_second",
                "image_path": in_img2,
                "model_path": dummy_model,
                "output_path": out_img2,
                "output_format": "png",
                "device": "cpu",
                "tile_size": 64,
                "halo": 8,
                "precision": "fp32",
                "jpeg_quality": 98,
                "preserve_metadata": False,
                "safe_memory": False,
            },
        }

        harness.send_message(job_req2)

        completed_msg = harness.wait_for_message(
            lambda message: (
                message.get("type") == "job_completed"
                and message["data"]["job_id"] == "job_subsequent_second"
            ),
            ipc_timeout,
        )

        assert completed_msg is not None, "Subsequent job must complete successfully."
        assert os.path.exists(out_img2), "Subsequent job output file must exist."

        with Image.open(out_img2) as out_img:
            assert out_img.size == (32, 32), (
                f"Subsequent job 2x upscale of 16x16 image must yield (32, 32), got {out_img.size}"
            )

    finally:
        retcode = harness.shutdown()
        assert retcode == 0


def test_f4_6_worker_shutdown_and_tempfile_cleanup(tmp_path, dummy_model):
    """F4.6: Test worker process shutdown leaving zero orphaned Python worker processes and zero .dat temporary memmap files."""
    # Capture existing .dat files before test
    dat_before = set(glob.glob(os.path.join(tempfile.gettempdir(), "localsr_*.dat")))

    harness = WorkerSubprocessHarness()
    pid = harness.pid

    try:
        harness.wait_for_ready()

        # Run a job to generate temporary memmaps
        in_img = create_test_image(str(tmp_path / "cleanup_test_in.png"), 64, 64)
        out_img = str(tmp_path / "cleanup_test_out.png")

        job_req = {
            "type": "job_request",
            "data": {
                "job_id": "job_cleanup_test",
                "image_path": in_img,
                "model_path": dummy_model,
                "output_path": out_img,
                "output_format": "png",
                "device": "cpu",
                "tile_size": 64,
                "halo": 8,
                "precision": "fp32",
                "jpeg_quality": 98,
                "preserve_metadata": False,
                "safe_memory": False,
            },
        }

        harness.send_message(job_req)

        while True:
            msg = harness.read_message()
            if msg.get("type") == "job_completed":
                break

    finally:
        # Shut down worker
        retcode = harness.shutdown(timeout=5)
        assert retcode == 0, f"Worker process shutdown exit code should be 0, got {retcode}"

    # 1. Assert the exact worker handle has exited. Avoid platform-specific
    # process-table commands such as ps/pgrep so this test also runs on Windows.
    assert harness.proc.poll() is not None, f"Worker process {pid} is still running."

    # 2. Assert zero leftover .dat temporary files on disk
    dat_after = set(glob.glob(os.path.join(tempfile.gettempdir(), "localsr_*.dat")))
    new_dats = dat_after - dat_before
    assert len(new_dats) == 0, f"Leftover .dat temporary memmap files found: {new_dats}"


def test_full_gui_qprocess_spandrel_pipeline(qtbot, tmp_path, dummy_model, monkeypatch):
    """Run a complete GUI-to-worker upscale with a real Spandrel descriptor."""
    timeout_multiplier = 4 if sys.platform == "win32" else 1
    input_path = create_test_image(str(tmp_path / "gui_input.png"), 16, 16, color="blue")
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    monkeypatch.setenv("LOCALSR_ALLOW_UNVERIFIED_CHECKPOINTS", "1")
    window = MainWindow(settings=settings)
    qtbot.addWidget(window)
    failures = []
    protocol_errors = []
    worker_logs = []
    window.worker.job_failed.connect(lambda _job_id, error: failures.append(error))
    window.worker.warning.connect(lambda warning: protocol_errors.append(f"warning: {warning}"))
    window.worker.worker_error.connect(lambda error: protocol_errors.append(f"error: {error}"))
    window.worker.log_received.connect(lambda record: worker_logs.append(dict(record)))

    try:
        qtbot.waitUntil(
            lambda: (
                window.worker.process.state() == QProcess.Running
                and window.progress_label.text() == "Worker ready."
                and int(window.capability_report.get("system_ram_total", 0)) > 0
            ),
            timeout=30_000 * timeout_multiplier,
        )

        # This test exercises GUI-to-worker IPC and real CPU inference, not the
        # resource-policy thresholds of a particular CI VM.  Windows can have
        # less free RAM after the preceding stress tests, which correctly
        # disables the button even for this synthetic 16x16 image.  Use a
        # deterministic safe-memory snapshot after the real capability reply
        # has arrived so the integration path itself remains testable.
        window.capability_report["system_ram_available"] = 8 * 1024**3

        window.output_dir = str(tmp_path)
        window.out_dir_label.setText(str(tmp_path))
        window.set_image(input_path)
        window.model_path = dummy_model
        window.combo_device.setCurrentText("cpu")
        window.combo_tile.setCurrentText("128")
        window.combo_halo.setCurrentText("16")
        window.combo_precision.setCurrentText("fp32")
        window.check_safe_mem.setChecked(False)

        window.worker.send_request(InspectRequest(model_path=dummy_model))
        try:
            qtbot.waitUntil(
                lambda: bool(failures) or bool(protocol_errors) or window.model_scale == 2,
                timeout=30_000 * timeout_multiplier,
            )
        except QtBotTimeoutError:
            pytest.fail(
                "GUI worker model inspection timed out: "
                f"process_state={window.worker.process.state().name}, "
                f"bytes_to_write={window.worker.process.bytesToWrite()}, "
                f"model_scale={window.model_scale}, "
                f"upscale_enabled={window.btn_upscale.isEnabled()}, "
                f"progress={window.progress_label.text()!r}, "
                f"protocol_errors={protocol_errors!r}, worker_logs={worker_logs[-20:]!r}"
            )
        assert not failures, f"GUI worker model inspection failed: {failures}"
        assert not protocol_errors, f"GUI worker protocol failed: {protocol_errors}"
        assert window.current_estimate is not None
        assert not window.current_estimate.blocking, window.current_estimate.warnings
        assert window.btn_upscale.isEnabled()

        output_path = window.get_output_path()
        window.start_upscale()
        qtbot.waitUntil(
            lambda: (
                bool(failures)
                or (
                    os.path.exists(output_path)
                    and window.progress_label.text() == "Completed successfully!"
                )
            ),
            timeout=60_000 * timeout_multiplier,
        )

        assert not failures, f"GUI worker job failed: {failures}"
        with Image.open(output_path) as output:
            assert output.size == (32, 32)
            assert output.mode == "RGB"
    finally:
        window.close()

    assert window.worker.process.state() == QProcess.NotRunning
