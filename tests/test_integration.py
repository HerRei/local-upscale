import glob
import json
import os
import subprocess
import sys
import tempfile
import time
from typing import Any

import pytest
import spandrel.architectures.ESRGAN as E
import torch
from PIL import Image
from PySide6.QtCore import QProcess, QSettings

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


class WorkerSubprocessHarness:
    """Helper harness to spawn, interact with, and cleanly shut down a worker subprocess."""

    def __init__(self):
        self.proc = subprocess.Popen(
            [sys.executable, "-u", "-m", "localsr.worker.__main__"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.pid = self.proc.pid

    def read_message(self, timeout=10.0) -> dict[str, Any]:
        """Reads a single JSON message line from worker stdout."""
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError("Subprocess stdout closed unexpectedly.")
        return json.loads(line.strip())

    def send_message(self, msg: dict[str, Any]):
        """Sends a JSON message line to worker stdin."""
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def wait_for_ready(self) -> dict[str, Any]:
        """Waits for and returns the worker_ready message."""
        msg = self.read_message()
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
    proc.wait(timeout=5)
    assert proc.returncode == 0, f"Worker process exit code should be 0, got {proc.returncode}"


def test_f4_2_worker_ready_signal():
    """F4.2: Test worker_ready signal detection on worker startup."""
    harness = WorkerSubprocessHarness()
    try:
        msg = harness.read_message()
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

        # Wait for job_started, then send cancel_request
        start_time = time.time()
        while time.time() - start_time < 5.0:
            msg = harness.read_message()
            if msg.get("type") == "job_started":
                break

        harness.send_message({"type": "cancel_request", "data": {"job_id": "job_f4_4"}})

        # Read IPC responses until job_cancelled
        cancelled_msg = None
        start_time = time.time()
        while time.time() - start_time < 10.0:
            msg = harness.read_message()
            if msg.get("type") == "job_cancelled" and msg["data"]["job_id"] == "job_f4_4":
                cancelled_msg = msg
                break

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

        # Read job_started
        while True:
            msg = harness.read_message()
            if msg.get("type") == "job_started":
                break

        harness.send_message({"type": "cancel_request", "data": {"job_id": "job_cancel_first"}})

        while True:
            msg = harness.read_message()
            if msg.get("type") == "job_cancelled":
                break

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

        completed_msg = None
        start_time = time.time()
        while time.time() - start_time < 10.0:
            msg = harness.read_message()
            if (
                msg.get("type") == "job_completed"
                and msg["data"]["job_id"] == "job_subsequent_second"
            ):
                completed_msg = msg
                break
            elif msg.get("type") == "job_failed":
                pytest.fail(f"Subsequent job failed: {msg['data']}")

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


def test_full_gui_qprocess_spandrel_pipeline(qtbot, tmp_path, dummy_model):
    """Run a complete GUI-to-worker upscale with a real Spandrel descriptor."""
    input_path = create_test_image(str(tmp_path / "gui_input.png"), 16, 16, color="blue")
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    window = MainWindow(settings=settings)
    qtbot.addWidget(window)
    failures = []
    window.worker.job_failed.connect(lambda _job_id, error: failures.append(error))

    try:
        qtbot.waitUntil(
            lambda: (
                window.worker.process.state() == QProcess.Running
                and window.progress_label.text() == "Worker ready."
            ),
            timeout=30_000,
        )

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
        qtbot.waitUntil(
            lambda: window.model_scale == 2 and window.btn_upscale.isEnabled(),
            timeout=30_000,
        )

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
            timeout=60_000,
        )

        assert not failures, f"GUI worker job failed: {failures}"
        with Image.open(output_path) as output:
            assert output.size == (32, 32)
            assert output.mode == "RGB"
    finally:
        window.close()

    assert window.worker.process.state() == QProcess.NotRunning
