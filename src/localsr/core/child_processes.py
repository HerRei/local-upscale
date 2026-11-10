"""Keep the programs the worker starts (FFmpeg, the system codec helper) from outliving it.

They are started in the worker's own process group, never in a new one: the
desktop host starts the worker as the leader of a group (POSIX) or in a
kill-on-close Job Object (Windows) and ends the whole of it, which only reaches
programs that stayed inside. This module covers the exits the host does not:
the worker ends or is asked to terminate while a program still runs.
"""

from __future__ import annotations

import atexit
import os
import signal
import subprocess
import threading
import weakref

_running: weakref.WeakSet[subprocess.Popen] = weakref.WeakSet()
_lock = threading.Lock()


def start(command: list[str], **options) -> subprocess.Popen:
    """Start a program that is stopped again when this process ends."""
    process = subprocess.Popen(command, **options)
    with _lock:
        _running.add(process)
    return process


def stop(process: subprocess.Popen, grace: float = 5.0) -> None:
    """End a program: ask it to stop, and kill it if it has not within ``grace`` seconds."""
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def stop_all() -> None:
    with _lock:
        remaining = list(_running)
    for process in remaining:
        try:
            stop(process, grace=2.0)
        except OSError:
            pass


atexit.register(stop_all)


def stop_children_on_termination() -> None:
    """POSIX: a terminate or hangup signal stops the running programs before the process ends.

    Python's default for these signals ends the process at once, without
    ``atexit``. Call this from the main thread of a worker process.
    """
    if os.name == "posix":

        def handler(number: int, _frame) -> None:
            stop_all()
            signal.signal(number, signal.SIG_DFL)
            os.kill(os.getpid(), number)

        signal.signal(signal.SIGTERM, handler)
        signal.signal(signal.SIGHUP, handler)
