"""Keep the programs the worker starts (FFmpeg, the system codec helper) from outliving it.

They are started in the worker's own process group, never in a new one: the
desktop host starts the worker as the leader of a group (POSIX) or in a
kill-on-close Job Object (Windows) and ends the whole of it, which only reaches
programs that stayed inside. This module covers the exits the host does not:
the worker ends or is asked to terminate while a program still runs.
"""

from __future__ import annotations

import atexit
import ctypes
import os
import signal
import subprocess
import sys
import threading
import weakref

_running: weakref.WeakSet[subprocess.Popen] = weakref.WeakSet()
_lock = threading.Lock()
_low_priority = False

_PRIO_DARWIN_PROCESS = 4
_PRIO_DARWIN_BG = 0x1000
_LOW_NICE = 10


def _darwin_libc():
    try:
        libc = ctypes.CDLL("libc.dylib")
        libc.setpriority.restype = ctypes.c_int
        libc.setpriority.argtypes = [ctypes.c_int, ctypes.c_uint, ctypes.c_int]
        return libc
    except OSError:
        return None


def _apply_pid_priority(pid: int, enabled: bool, *, is_self: bool = False) -> None:
    if pid <= 0:
        return
    if sys.platform == "darwin":
        libc = _darwin_libc()
        if libc is not None:
            who = 0 if is_self else pid
            libc.setpriority(_PRIO_DARWIN_PROCESS, who, _PRIO_DARWIN_BG if enabled else 0)
        # No nice on macOS: it could not be raised back, and background QoS is reversible.
        return
    if os.name == "nt":
        try:
            import psutil

            proc = psutil.Process(pid)
            proc.nice(
                psutil.BELOW_NORMAL_PRIORITY_CLASS if enabled else psutil.NORMAL_PRIORITY_CLASS
            )
        except Exception:
            pass
        return
    if sys.platform.startswith("linux"):
        if enabled:
            try:
                os.setpriority(os.PRIO_PROCESS, 0 if is_self else pid, _LOW_NICE)
            except OSError:
                pass
        try:
            import psutil

            proc = psutil.Process(pid)
            if hasattr(proc, "ionice"):
                proc.ionice(psutil.IOPRIO_CLASS_IDLE if enabled else psutil.IOPRIO_CLASS_NONE)
        except Exception:
            pass


def set_low_priority(enabled: bool) -> None:
    """Apply or clear low scheduling priority on this worker and its running children."""
    enabled = bool(enabled)
    with _lock:
        global _low_priority
        _low_priority = enabled
        tracked_pids = [process.pid for process in _running if process.poll() is None]
    _apply_pid_priority(os.getpid(), enabled, is_self=True)
    seen = {os.getpid()}
    for pid in tracked_pids:
        if pid not in seen:
            seen.add(pid)
            _apply_pid_priority(pid, enabled)
    try:
        import psutil

        for child in psutil.Process(os.getpid()).children(recursive=True):
            if child.pid not in seen:
                seen.add(child.pid)
                _apply_pid_priority(child.pid, enabled)
    except Exception:
        pass


def track(process: subprocess.Popen) -> subprocess.Popen:
    """Track an externally spawned child process for shutdown and priority updates."""
    with _lock:
        _running.add(process)
        enabled = _low_priority
    if enabled and process.poll() is None:
        _apply_pid_priority(process.pid, True)
    return process


def start(command: list[str], **options) -> subprocess.Popen:
    """Start a program that is stopped again when this process ends.

    The worker keeps reading its own stdin from the host. On Windows a child that
    inherits that pipe blocks at startup while the read is pending (#160), so a
    child gets no stdin unless the caller passes one.
    """
    options.setdefault("stdin", subprocess.DEVNULL)
    process = subprocess.Popen(command, **options)
    return track(process)


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


def native_environment() -> dict:
    environment = os.environ.copy()
    if getattr(sys, "frozen", False):
        for key in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
            original = environment.get(key + "_ORIG", "")
            if original:
                environment[key] = original
            else:
                environment.pop(key, None)
    return environment


def stop_process(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
