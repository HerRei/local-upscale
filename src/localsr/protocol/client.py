import json
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, Signal

from localsr.protocol.messages import ShutdownRequest


def worker_command() -> tuple[str, list[str]]:
    """Return the isolated worker command for source and packaged builds."""
    if not getattr(sys, "frozen", False):
        return sys.executable, ["-u", "-m", "localsr.worker.__main__"]

    executable = Path(sys.executable)
    suffix = ".exe" if sys.platform == "win32" else ""
    packaged_worker = executable.with_name(f"LocalSRWorker{suffix}")
    if packaged_worker.is_file():
        return str(packaged_worker), []
    # Compatibility fallback for single-executable development bundles.
    return sys.executable, ["--worker"]


class WorkerClient(QObject):
    worker_ready = Signal()
    model_info_received = Signal(dict)
    capabilities_received = Signal(dict)
    job_started = Signal(str)
    progress_updated = Signal(dict)
    tile_updated = Signal(dict)
    preview_ready = Signal(dict)
    preview_failed = Signal(dict)
    job_completed = Signal(str, object)
    job_cancelled = Signal(str)
    job_failed = Signal(str, str)
    warning = Signal(str)
    log_received = Signal(dict)
    job_result = Signal(dict)
    worker_error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_shutting_down = False
        self.process = QProcess(self)
        self.process.readyReadStandardOutput.connect(self.handle_stdout)
        self.process.readyReadStandardError.connect(self.handle_stderr)
        self.process.errorOccurred.connect(self.handle_error)
        self.process.finished.connect(self.handle_finished)
        self.buffer = ""

    def start(self):
        if self.process.state() != QProcess.NotRunning:
            return
        self._is_shutting_down = False
        program, arguments = worker_command()
        self.process.start(program, arguments)

    def send_request(self, req) -> bool:
        if self.process.state() != QProcess.Running:
            return False

        payload = (req.to_json() + "\n").encode("utf-8")
        queued = self.process.write(payload)
        if queued != len(payload):
            self.worker_error.emit(
                f"Worker request write was incomplete: queued {queued} of {len(payload)} bytes."
            )
            return False

        # QProcess writes are asynchronous.  On Windows in particular, leaving
        # the command only in Qt's userspace buffer can strand a request even
        # while stdout from the child remains healthy.  Drain the small JSON
        # command into the OS pipe before returning to the GUI event loop.
        while self.process.bytesToWrite() > 0:
            if not self.process.waitForBytesWritten(5000):
                self.worker_error.emit(
                    "Worker request timed out while writing to the process input pipe."
                )
                return False
        return True

    def handle_stdout(self):
        data = self.process.readAllStandardOutput().data().decode("utf-8", errors="replace")
        self.buffer += data
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            line = line.strip()
            if not line:
                continue

            try:
                msg = json.loads(line)
                if not isinstance(msg, dict):
                    self.log_received.emit({"level": "stdout", "message": line})
                    continue
                t = msg.get("type")
                d = msg.get("data")
                if not isinstance(d, dict):
                    d = {}

                if t == "worker_ready":
                    self.worker_ready.emit()
                elif t == "model_info":
                    self.model_info_received.emit(d)
                elif t == "capabilities_info":
                    self.capabilities_received.emit(d)
                elif t == "job_started":
                    self.job_started.emit(d.get("job_id", ""))
                elif t == "progress":
                    self.progress_updated.emit(d)
                elif t == "tile_update":
                    self.tile_updated.emit(d)
                elif t == "preview_ready":
                    self.preview_ready.emit(d)
                elif t == "preview_failed":
                    self.preview_failed.emit(d)
                elif t == "log":
                    self.log_received.emit(d)
                elif t == "warning":
                    self.warning.emit(d.get("message", ""))
                elif t == "job_completed":
                    job_id = d.get("job_id", "")
                    res = {"type": "job_completed", "success": True, **d}
                    self.job_completed.emit(job_id, res)
                    self.job_result.emit(res)
                elif t == "job_cancelled":
                    job_id = d.get("job_id", "")
                    res = {"type": "job_cancelled", **d}
                    self.job_cancelled.emit(job_id)
                    self.job_result.emit(res)
                elif t == "job_failed":
                    job_id = d.get("job_id", "")
                    err_msg = d.get("error_message", d.get("error", ""))
                    res = {"type": "job_failed", "error_message": err_msg, **d}
                    self.job_failed.emit(job_id, err_msg)
                    self.job_result.emit(res)
                elif t == "job_result":
                    self.job_result.emit(d)
                else:
                    self.log_received.emit({"level": "unknown", "message": line})
            except json.JSONDecodeError:
                # Normal stderr or print statement from PyTorch
                self.log_received.emit({"level": "stdout", "message": line})

    def handle_stderr(self):
        data = self.process.readAllStandardError().data().decode("utf-8", errors="replace")
        for line in data.splitlines():
            if line.strip():
                self.log_received.emit({"level": "stderr", "message": line.strip()})

    def handle_error(self, error):
        if self._is_shutting_down:
            return
        self.worker_error.emit(f"Worker process error: {error}")

    def handle_finished(self, exitCode, exitStatus):
        if self._is_shutting_down or exitCode == 0:
            return
        self.worker_error.emit(f"Worker process exited with code {exitCode}")

    def stop(self):
        self._is_shutting_down = True
        state = self.process.state()
        if state == QProcess.NotRunning:
            return
        if state == QProcess.Starting:
            self.process.waitForStarted(1000)
            state = self.process.state()
        if state == QProcess.Running:
            self.send_request(ShutdownRequest())
            self.process.waitForBytesWritten(1000)
            if self.process.waitForFinished(3000):
                return
        self.process.kill()
        if not self.process.waitForFinished(3000):
            self.worker_error.emit("Worker process could not be stopped.")
