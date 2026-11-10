"""One bad request line must never make the worker deaf, immortal or stuck on a job."""

from __future__ import annotations

import io
import json
import os
import queue
import random
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from localsr.worker.server import WorkerServer

ROOT = Path(__file__).resolve().parents[1]

POISON_LINES = {
    "cancel_data_string": b'{"type":"cancel_request","data":"abc"}\n',
    "cancel_data_null": b'{"type":"cancel_request","data":null}\n',
    "cancel_data_list": b'{"type":"cancel_request","data":[1]}\n',
    "deeply_nested": b'{"type":"x","data":' + b"[" * 100000 + b"]" * 100000 + b"}\n",
    "huge_integer": b'{"type":"x","data":{"n":' + b"9" * 5000 + b"}}\n",
}


class WorkerProcess:
    """A real worker with its home and work folders inside tmp_path."""

    def __init__(self, tmp_path: Path):
        env = dict(
            os.environ,
            HOME=str(tmp_path / "home"),
            LOCALSR_WORK_DIR=str(tmp_path / "work"),
            PYTHONPATH=str(ROOT / "src"),
            PYTHONUTF8="1",
        )
        self.process = subprocess.Popen(
            [sys.executable, "-m", "localsr.worker.server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
        )
        self.messages: queue.Queue = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()
        assert self.wait_for("worker_ready", 120) is not None

    def _read(self):
        for line in self.process.stdout:
            try:
                self.messages.put(json.loads(line))
            except ValueError:
                pass
        self.messages.put({"type": "process_exit", "data": {}})

    def send_raw(self, payload: bytes):
        self.process.stdin.write(payload)
        self.process.stdin.flush()

    def send(self, kind: str, data):
        self.send_raw((json.dumps({"type": kind, "data": data}) + "\n").encode())

    def wait_for(self, kinds, timeout: float):
        kinds = {kinds} if isinstance(kinds, str) else set(kinds)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                message = self.messages.get(timeout=max(0.05, deadline - time.monotonic()))
            except queue.Empty:
                break
            if message["type"] in kinds:
                return message
            if message["type"] == "process_exit":
                return None
        return None

    def handshake_answered(self, timeout: float = 30) -> bool:
        self.send(
            "handshake_request",
            {"client_name": "test", "client_version": "0", "protocol_version": 1},
        )
        return self.wait_for("engine_info", timeout) is not None

    def close(self):
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(10)
        for stream in (self.process.stdin, self.process.stdout):
            try:
                stream.close()
            except OSError:
                pass


@pytest.fixture
def worker(tmp_path):
    instance = WorkerProcess(tmp_path)
    yield instance
    instance.close()


def _image_job(tmp_path: Path, job_id, **overrides):
    data = {
        "job_id": job_id,
        "image_path": str(tmp_path / "in.png"),
        "model_path": str(tmp_path / "no-such-model.safetensors"),
        "output_path": str(tmp_path / "out.png"),
        "output_format": "png",
        "device": "cpu",
        "tile_size": 64,
        "halo": 8,
        "precision": "fp32",
        "jpeg_quality": 90,
        "preserve_metadata": True,
        "safe_memory": True,
        "neural_engine": False,
        "preview_enabled": False,
    }
    data.update(overrides)
    return data


@pytest.mark.parametrize("name", sorted(POISON_LINES))
def test_reader_survives_a_poison_line_and_still_signals_eof(name, monkeypatch):
    stdin = io.TextIOWrapper(
        io.BytesIO(POISON_LINES[name] + b'{"type":"capabilities_request"}\n'), encoding="utf-8"
    )
    monkeypatch.setattr(sys, "stdin", stdin)
    server = WorkerServer()
    server.reader_thread_func()
    types = []
    while not server.message_queue.empty():
        types.append(server.message_queue.get_nowait().get("type"))
    assert "capabilities_request" in types, "the line after the bad one was never read"
    assert types[-1] == "shutdown_request"


def test_reader_replaces_bytes_that_are_not_utf8(monkeypatch):
    stdin = io.TextIOWrapper(
        io.BytesIO(b'{"type":"x","data":"\xff\xfe"}\n{"type":"capabilities_request"}\n'),
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "stdin", stdin)
    server = WorkerServer()
    server.reader_thread_func()
    types = []
    while not server.message_queue.empty():
        types.append(server.message_queue.get_nowait().get("type"))
    assert types == ["x", "capabilities_request", "shutdown_request"]


def test_cancel_with_a_non_text_job_id_cancels_nothing(monkeypatch):
    stdin = io.StringIO('{"type":"cancel_request","data":{"job_id":["a"]}}\n')
    monkeypatch.setattr(sys, "stdin", stdin)
    server = WorkerServer()
    server.reader_thread_func()
    assert server.pending_cancel_job_ids == set()


def _random_value(rng: random.Random, depth: int = 0):
    choices = [
        lambda: None,
        lambda: rng.choice([True, False]),
        lambda: rng.randint(-(10**12), 10**12),
        lambda: rng.random() * rng.choice([1, 1e300, -1e-300]),
        lambda: "".join(rng.choice("ab/\\\x00 é😀\n\"'") for _ in range(rng.randint(0, 20))),
    ]
    if depth < 3:
        choices += [
            lambda: [_random_value(rng, depth + 1) for _ in range(rng.randint(0, 4))],
            lambda: {
                rng.choice(["job_id", "data", "type", "image_path", "jobs", "x"]): _random_value(
                    rng, depth + 1
                )
                for _ in range(rng.randint(0, 4))
            },
        ]
    return rng.choice(choices)()


def _random_lines(rng: random.Random, count: int) -> list[bytes]:
    kinds = [
        "handshake_request",
        "media_probe_request",
        "cancel_request",
        "image_batch_request",
        "job_request",
        "video_job_request",
        "benchmark_request",
        "region_request",
        "region_clear",
        "preview_request",
        "no_such_request",
        None,
        7,
        [1],
    ]
    lines = []
    for _ in range(count):
        style = rng.randrange(6)
        if style == 0:
            lines.append(bytes(rng.randrange(256) for _ in range(rng.randint(1, 60))))
        elif style == 1:
            text = json.dumps({"type": rng.choice(kinds), "data": _random_value(rng)})
            lines.append(text[: rng.randint(1, len(text))].encode())
        elif style == 2:
            lines.append(json.dumps(_random_value(rng)).encode())
        elif style == 3:
            lines.append(b"[" * rng.randint(1000, 60000))
        elif style == 4:
            lines.append(b'{"type":"x","data":' + b"7" * rng.randint(4300, 9000) + b"}")
        else:
            lines.append(
                json.dumps({"type": rng.choice(kinds), "data": _random_value(rng)}).encode()
            )
    return [line.replace(b"\n", b" ") + b"\n" for line in lines]


def test_random_malformed_lines_leave_a_serving_worker_that_exits_on_eof(worker):
    for line in _random_lines(random.Random(20261110), 300):
        worker.send_raw(line)
    assert worker.handshake_answered(), "the worker stopped serving after malformed lines"
    worker.process.stdin.close()
    assert worker.process.wait(30) == 0


@pytest.mark.parametrize("poison", sorted(POISON_LINES))
def test_worker_exits_on_eof_after_a_poison_line(worker, poison):
    worker.send_raw(POISON_LINES[poison])
    assert worker.handshake_answered()
    worker.process.stdin.close()
    assert worker.process.wait(30) == 0


def test_a_job_with_an_unhashable_id_does_not_block_later_jobs(worker, tmp_path):
    for kind in ("job_request", "video_job_request"):
        for bad_id in (["a"], {"a": 1}):
            request = _image_job(tmp_path, bad_id)
            if kind == "video_job_request":
                request.update(
                    video_path=str(tmp_path / "in.mp4"),
                    output_video_path=str(tmp_path / "o.mp4"),
                    container="mp4",
                    crf=18,
                    model_kind="spandrel_image",
                )
            worker.send(kind, request)
    worker.send("job_request", _image_job(tmp_path, "next-job"))
    answer = worker.wait_for({"job_failed", "job_completed", "job_cancelled"}, 60)
    assert answer is not None
    assert answer["data"]["job_id"] == "next-job"
    assert answer["data"]["error_message"] != "A job is already running."
