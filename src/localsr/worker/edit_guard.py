"""Small supervisor: worker pipe EOF, pressure or cancellation releases native weights.

This entry point deliberately never imports torch or the inference server.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from localsr.core.edit_memory import EditPlan, check_edit_pressure, read_host_memory


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


def process_memory(process) -> int:
    """The editor's own memory: physical footprint on macOS, RSS elsewhere.

    On macOS RSS includes the memory-mapped weights, clean pages the system drops
    under pressure at no cost; the footprint is what Activity Monitor shows. A
    FLUX.2 klein 4B edit on a 16 GB Mac peaked at 6.4 GiB RSS and 2.6 GiB footprint.
    """
    if sys.platform == "darwin":
        import ctypes

        class RUsageInfo(ctypes.Structure):
            _fields_ = [("uuid", ctypes.c_uint8 * 16)] + [
                (name, ctypes.c_uint64)
                for name in (
                    "user_time",
                    "system_time",
                    "idle_wakeups",
                    "interrupt_wakeups",
                    "pageins",
                    "wired_size",
                    "resident_size",
                    "phys_footprint",
                    "start_abstime",
                    "exit_abstime",
                )
            ]

        usage = RUsageInfo()
        try:
            libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
            if libproc.proc_pid_rusage(process.pid, 0, ctypes.byref(usage)) == 0:
                return int(usage.phys_footprint)
        except OSError:
            pass
    return process.memory_info().rss


def reset_dll_search():
    if os.name == "nt" and getattr(sys, "frozen", False):
        import ctypes

        ctypes.windll.kernel32.SetDllDirectoryW(None)


def probe(args) -> int:
    """Run help/device enumeration without importing the inference worker."""
    if len(args) != 2 or args[1] not in {"--help", "--list-devices"}:
        return 2
    reset_dll_search()
    try:
        return subprocess.run(
            args,
            env=native_environment(),
            creationflags=0x08000000 if os.name == "nt" else 0,
            timeout=20 if args[1] == "--list-devices" else 5,
        ).returncode
    except subprocess.TimeoutExpired:
        return 124


def main(args=None) -> int:
    import psutil

    args = sys.argv[1:] if args is None else args
    spec = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    plan = EditPlan(**spec["plan"])
    cancelled = threading.Event()

    def read_cancel():
        # The worker keeps this pipe open. A forced restart or crash closes it.
        # Read the raw descriptor: a daemon thread parked inside the buffered
        # reader holds its lock at interpreter shutdown and aborts the process
        # after the child has already finished.
        try:
            descriptor = sys.stdin.fileno()
        except (AttributeError, OSError, ValueError):
            sys.stdin.buffer.read(1)
        else:
            try:
                os.read(descriptor, 1)
            except OSError:
                pass
        cancelled.set()

    threading.Thread(target=read_cancel, daemon=True).start()
    reset_dll_search()
    process = None
    try:
        if cancelled.is_set():
            return 130
        # Recheck immediately before spawning, including time spent verifying files.
        memory = read_host_memory(spec["unified"])
        check_edit_pressure(memory, plan, spec["swap_used"], 0)
        if memory.free_ram < plan.required_ram + plan.reserve_ram:
            raise MemoryError("Available memory changed before loading. Close apps and retry.")
        process = subprocess.Popen(
            spec["command"],
            stdin=subprocess.DEVNULL,
            env=native_environment(),
            creationflags=0x08000000 if os.name == "nt" else 0,
        )
        watched = psutil.Process(process.pid)
        warning_since = None
        critical_samples = 0
        while process.poll() is None:
            if cancelled.wait(0.25):
                return 130
            try:
                used = process_memory(watched)
            except psutil.NoSuchProcess:
                break
            memory = read_host_memory(spec["unified"])
            now = time.monotonic()
            raised = memory.pressure in {"warning", "critical"}
            warning_since = (warning_since or now) if raised else None
            critical_samples = critical_samples + 1 if memory.pressure == "critical" else 0
            check_edit_pressure(
                memory,
                plan,
                spec["swap_used"],
                used,
                warning_seconds=now - warning_since if warning_since else 0.0,
                critical_samples=critical_samples,
            )
        return process.wait()
    except MemoryError as error:
        print(str(error), file=sys.stderr, flush=True)
        return 75
    finally:
        if process is not None:
            stop_process(process)


if __name__ == "__main__":
    raise SystemExit(main())
