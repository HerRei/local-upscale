"""Children of the worker must not inherit its stdin (#160).

The worker keeps a read pending on the pipe from the host. On Windows, a child that
inherits that pipe blocks during startup until the read completes, so the edit
runtime check timed out on every edit.
"""

import ast
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "src" / "localsr"
SPAWNERS = {"run", "Popen", "check_output", "check_call", "call"}


def test_every_child_process_gets_its_own_stdin():
    missing = []
    for path in SOURCE.rglob("*.py"):
        if path.name == "desktop_launcher.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and getattr(node.func.value, "id", "") == "subprocess"
                and node.func.attr in SPAWNERS
            ):
                continue
            keywords = {keyword.arg for keyword in node.keywords}
            # ``**options`` is allowed where the function sets a stdin default itself.
            if not keywords & {"stdin", "input", None}:
                missing.append(f"{path.relative_to(SOURCE)}:{node.lineno}")
    assert missing == []


def test_start_gives_children_no_stdin_by_default(monkeypatch):
    from localsr.core import child_processes

    seen = {}

    class Fake:
        pid = 1

        def __init__(self, command, **options):
            seen.update(options)

        def poll(self):
            return 0

    monkeypatch.setattr(child_processes.subprocess, "Popen", Fake)
    child_processes.start(["tool"])
    assert seen["stdin"] is subprocess.DEVNULL
    child_processes.start(["tool"], stdin=subprocess.PIPE)
    assert seen["stdin"] is subprocess.PIPE


@pytest.mark.skipif(sys.platform != "win32", reason="the pipe lock is Windows behaviour")
def test_a_child_starts_while_the_worker_waits_on_its_stdin():
    # The child must set up its own stdin during startup, as the frozen worker does;
    # ``python --help`` exits before that and would not show the hang.
    worker = textwrap.dedent(
        """
        import os, subprocess, sys, threading, time
        from localsr.core import child_processes

        threading.Thread(target=lambda: os.read(sys.stdin.fileno(), 1), daemon=True).start()
        time.sleep(0.5)
        start = time.monotonic()
        child = child_processes.start(
            [sys.executable, "-c", "import sys; sys.stdin; print(1)"],
            stdout=subprocess.PIPE,
            creationflags=0x08000000,
        )
        try:
            child.communicate(timeout=8)
            print(round(time.monotonic() - start, 2), flush=True)
        except subprocess.TimeoutExpired:
            child.kill()
            print("timeout", flush=True)
        os._exit(0)
        """
    )
    host = subprocess.Popen(
        [sys.executable, "-c", worker],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    # Keep the host's end of stdin open and unwritten, as the desktop host does;
    # communicate() would close it and end the pending read.
    timer = threading.Timer(30, host.kill)
    timer.start()
    try:
        line = host.stdout.readline().strip()
    finally:
        timer.cancel()
        host.kill()
        host.wait()
    assert line not in {"", "timeout"}, "a child blocked on the worker's stdin"
    assert float(line) < 8
