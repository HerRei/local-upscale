"""Threaded JSON-line client used by the Slint frontend.

The UI process deliberately does not import Torch or Spandrel.  Messages are
read on background threads and delivered through a standard-library queue;
the Slint event-loop timer drains that queue on the UI thread.
"""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
from pathlib import Path

from localsr.protocol.messages import ShutdownRequest


def worker_command() -> tuple[str, list[str]]:
    if not getattr(sys, "frozen", False):
        return sys.executable, ["-u", "-m", "localsr.worker.__main__"]

    executable = Path(sys.executable)
    suffix = ".exe" if sys.platform == "win32" else ""
    packaged_worker = executable.with_name(f"LocalSRWorker{suffix}")
    if packaged_worker.is_file():
        return str(packaged_worker), []
    return sys.executable, ["--worker"]


class SlintWorkerClient:
    """Own the isolated inference worker without depending on a Qt event loop."""

    def __init__(self, events: queue.Queue[tuple[str, dict]]):
        self.events = events
        self.process: subprocess.Popen[str] | None = None
        self._write_lock = threading.Lock()
        self._stopping = False

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self) -> None:
        if self.running:
            return
        self._stopping = False
        program, arguments = worker_command()
        try:
            self.process = subprocess.Popen(
                [program, *arguments],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except OSError as error:
            self.events.put(("worker_error", {"message": f"Could not start worker: {error}"}))
            return

        threading.Thread(
            target=self._read_stdout, daemon=True, name="localsr-worker-stdout"
        ).start()
        threading.Thread(
            target=self._read_stderr, daemon=True, name="localsr-worker-stderr"
        ).start()
        threading.Thread(target=self._watch_exit, daemon=True, name="localsr-worker-watch").start()

    def send_request(self, request) -> bool:
        process = self.process
        if process is None or process.poll() is not None or process.stdin is None:
            return False
        payload = request.to_json() + "\n"
        try:
            with self._write_lock:
                process.stdin.write(payload)
                process.stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as error:
            if not self._stopping:
                self.events.put(("worker_error", {"message": f"Worker pipe failed: {error}"}))
            return False
        return True

    def _read_stdout(self) -> None:
        process = self.process
        if process is None or process.stdout is None:
            return
        for raw_line in process.stdout:
            line = raw_line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                self.events.put(("log", {"level": "stdout", "message": line}))
                continue
            if not isinstance(message, dict):
                self.events.put(("log", {"level": "stdout", "message": line}))
                continue
            event_type = str(message.get("type", "unknown"))
            data = message.get("data")
            self.events.put((event_type, data if isinstance(data, dict) else {}))

    def _read_stderr(self) -> None:
        process = self.process
        if process is None or process.stderr is None:
            return
        for raw_line in process.stderr:
            line = raw_line.strip()
            if line:
                self.events.put(("log", {"level": "stderr", "message": line}))

    def _watch_exit(self) -> None:
        process = self.process
        if process is None:
            return
        exit_code = process.wait()
        if not self._stopping:
            self.events.put(
                ("worker_error", {"message": f"Worker process exited with code {exit_code}."})
            )

    def stop(self) -> None:
        process = self.process
        if process is None:
            return
        self._stopping = True
        if process.poll() is None:
            self.send_request(ShutdownRequest())
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        self.process = None
