"""A hardware refresh asked for while a job runs is answered at once, not after the job."""

from __future__ import annotations

import io
import json
import sys
import threading

from localsr.worker import server as worker_server
from localsr.worker.server import WorkerServer

REPORT = {
    "system_ram_total": 16,
    "system_ram_available": 8,
    "devices": [{"id": "mps"}],
    "system_memory_pressure_percent": 10.0,
    "system_memory_pressure_level": "low",
}
LINE = json.dumps({"type": "capabilities_request", "data": {}})


def _server(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    server = WorkerServer()
    sent = []
    monkeypatch.setattr(worker_server, "send_message", sent.append)
    monkeypatch.setattr(
        worker_server,
        "with_current_memory",
        lambda report: {**report, "system_memory_pressure_percent": 80.0},
    )
    return server, sent


def _wait_for_answers(server: WorkerServer) -> None:
    for thread in threading.enumerate():
        if thread.name == "capabilities-refresh":
            thread.join(5)


def test_a_running_job_does_not_hold_back_the_answer(monkeypatch):
    server, sent = _server(monkeypatch)
    server.last_capabilities = REPORT
    server.active_job_id = "job-1"
    server._read_line(LINE)
    _wait_for_answers(server)
    assert server.message_queue.empty()
    assert len(sent) == 1
    # The device list is the one reported before; only the memory readings are new.
    assert sent[0].devices == REPORT["devices"]
    assert sent[0].system_memory_pressure_percent == 80.0


def test_an_idle_worker_still_queues_the_request_for_its_main_loop(monkeypatch):
    server, sent = _server(monkeypatch)
    server.last_capabilities = REPORT
    server._read_line(LINE)
    assert server.message_queue.get_nowait()["type"] == "capabilities_request"
    assert sent == []


def test_without_an_earlier_report_a_running_worker_queues_the_request(monkeypatch):
    server, sent = _server(monkeypatch)
    server.active_job_id = "job-1"
    server._read_line(LINE)
    assert server.message_queue.get_nowait()["type"] == "capabilities_request"
    assert sent == []
