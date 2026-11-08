"""Keep a graphics driver that crashes from taking the engine down again.

A native crash in a graphics driver ends the process before any handler runs: Intel's
OpenVINO with a 2020 driver on a UHD 620 crashes while compiling a single convolution.
Two guards, both keyed by the whole GPU (device and driver identity, provider and LocalSR
version):

- Crash notes. Every GPU call (opening a session, each run) leaves a small file while it
  runs. When the engine dies of a native fault, the app starts the next engine with the
  dead engine's process ID; at startup that engine turns the dead engine's notes into a
  crash record and removes all other notes, which belong to engines the app stopped
  itself. A crash on the CPU side, or a kill (the app, the OOM killer, Task Manager),
  never counts.
- A preflight. Before the engine opens its first session on a DirectML or OpenVINO
  device, it runs itself with --gpu-preflight: the child runs a one-convolution model on
  that device through the same code, with its own crash notes. A child that dies of a
  native fault during a GPU call marks the GPU as crashed, and the engine stays up.

Standard library only: the engine runs ``settle`` before it imports anything heavy.
"""

import hashlib
import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

GATE_CACHE_ENV = "LOCALSR_ONNX_GATE_CACHE"
# Set by the app on the engine it starts after one that died of a native fault.
CRASHED_ENGINE_ENV = "LOCALSR_CRASHED_ENGINE"
PREFLIGHT_ARGUMENT = "--gpu-preflight"
# Set in the preflight child: it must not start a preflight of its own.
CHILD_ENV = "LOCALSR_GPU_PREFLIGHT_CHILD"
TIMEOUT_SECONDS = 300.0
# The child could not run its test (a Python error before or around the GPU call).
UNTESTED = 2
# Crash records in this process, in case the cache cannot be written.
_CRASHED: dict[str, str] = {}
# GPUs whose preflight ran in this process, whatever its outcome.
_PREFLIGHT_TRIED: set[str] = set()


def gate_cache_directory() -> Path:
    override = os.environ.get(GATE_CACHE_ENV)
    if override:
        return Path(override)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "LocalSR" / "gpu-checks"
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "LocalSR" / "gpu-checks"
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "LocalSR" / "gpu-checks"


def native_fault(returncode: int | None) -> bool:
    """Whether a process died of a fault in native code: an access violation, an illegal
    instruction, an abort. Kills (SIGKILL from the OOM killer, TerminateProcess) are not."""
    if returncode is None:
        return False
    if os.name == "nt":
        # NTSTATUS error codes (0xC0000005 access violation, 0xC0000409 fast fail, ...)
        # and 3, the exit code of abort().
        return returncode == 3 or returncode & 0xFFFFFFFF >= 0xC0000000
    faults = {signal.SIGSEGV, signal.SIGBUS, signal.SIGILL, signal.SIGFPE, signal.SIGABRT}
    return returncode < 0 and -returncode in faults


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


class CrashNotes:
    def __init__(self, directory: Path):
        self.directory = directory
        self.path = directory / "crashes.json"
        self.depth: dict[str, int] = {}

    def _note(self, key: str) -> Path:
        return self.directory / f"running-{hashlib.sha256(key.encode()).hexdigest()[:16]}.json"

    def crashed(self, key: str) -> str | None:
        """How this GPU crashed before ("engine" or "preflight"), or None."""
        if key in _CRASHED:
            return _CRASHED[key]
        entry = _read_json(self.path).get(key)
        return entry.get("cause", "engine") if isinstance(entry, dict) else None

    def mark(self, key: str, cause: str) -> None:
        _CRASHED[key] = cause
        entries = _read_json(self.path)
        entries[key] = {"cause": cause, "recorded": datetime.now(UTC).isoformat(timespec="seconds")}
        try:
            _write_json(self.path, entries)
        except OSError:
            logger.warning("Could not store the GPU crash record", exc_info=True)

    def running(self) -> list[dict]:
        notes = []
        for path in self.directory.glob("running-*.json"):
            notes.append({**_read_json(path), "path": path})
        return notes

    def settle(self, crashed_pid: int) -> None:
        """At engine startup: the dead engine's notes become crash records; every other
        note is left by an engine the app stopped itself and is removed."""
        for note in self.running():
            if note.get("pid") == crashed_pid and isinstance(note.get("key"), str):
                logger.warning("The engine crashed during a GPU call before: %s", note["key"])
                self.mark(note["key"], "engine")
            note["path"].unlink(missing_ok=True)

    @contextmanager
    def watch(self, key: str):
        # Re-entrant, so nested GPU calls keep one note.
        if self.depth.get(key):
            self.depth[key] += 1
            try:
                yield
            finally:
                self.depth[key] -= 1
            return
        self.depth[key] = 1
        note = self._note(key)
        try:
            _write_json(note, {"key": key, "pid": os.getpid()})
        except OSError:
            logger.warning("Could not note a GPU call", exc_info=True)
        try:
            yield
        finally:
            self.depth[key] = 0
            note.unlink(missing_ok=True)


def settle() -> None:
    """Run once at engine startup, before any GPU is used."""
    try:
        crashed_pid = int(os.environ.pop(CRASHED_ENGINE_ENV, "0"))
    except ValueError:
        crashed_pid = 0
    try:
        CrashNotes(gate_cache_directory()).settle(crashed_pid)
    except OSError:
        logger.warning("Could not read the GPU crash notes", exc_info=True)


def child_main(argv: list[str]) -> int:
    """Run a one-convolution model on the device ``argv[0]`` through the engine's ONNX
    route, including its comparison with the CPU. Exit 0 when the GPU ran, UNTESTED when
    the test could not run."""
    import faulthandler

    # A hard limit that holds even while a driver call blocks the interpreter, so an
    # orphaned child cannot hang on.
    faulthandler.dump_traceback_later(TIMEOUT_SECONDS + 30, exit=True)
    try:
        from types import SimpleNamespace

        import spandrel
        import torch

        from .inference import _onnx_route
        from .onnx_runtime import GpuRouteRejected, OnnxImageDescriptor

        route = _onnx_route(argv[0])
        if route is None:
            raise RuntimeError(f"{argv[0]} is not an ONNX Runtime GPU")
        network = torch.nn.Conv2d(3, 3, 3, padding=1)
        descriptor = spandrel.ImageModelDescriptor(
            network,
            state_dict=network.state_dict(),
            architecture=SimpleNamespace(name="GPU preflight"),
            purpose="Restoration",
            tags=[],
            supports_half=False,
            supports_bfloat16=False,
            scale=1,
            input_channels=3,
            output_channels=3,
            size_requirements=spandrel.SizeRequirements(minimum=8, multiple_of=8),
        )
        converted = OnnxImageDescriptor(descriptor, route=route)
        try:
            with torch.no_grad():
                converted(torch.rand(1, 3, 64, 64, generator=torch.Generator().manual_seed(0)))
        except GpuRouteRejected:
            pass  # it ran without crashing; the engine's own check decides the rest
    except Exception:  # noqa: BLE001 - reported through the exit code
        logger.exception("The GPU preflight could not run")
        return UNTESTED
    return 0


def _command(device: str) -> list[str]:
    command = [sys.executable]
    if not getattr(sys, "frozen", False):
        command += ["-m", "localsr.worker"]
    return [*command, PREFLIGHT_ARGUMENT, device]


def run(device: str, cancel_event: threading.Event | None = None) -> str:
    """ "passed", "crashed" (a native fault during a GPU call) or "unknown"."""

    def check_cancelled():
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("Cancelled")

    check_cancelled()
    with tempfile.TemporaryDirectory(
        prefix="localsr-gpu-preflight-", ignore_cleanup_errors=True
    ) as directory:
        env = dict(os.environ, **{CHILD_ENV: "1", GATE_CACHE_ENV: directory})
        env.pop(CRASHED_ENGINE_ENV, None)
        log_path = Path(directory) / "child.log"
        with log_path.open("wb") as log:
            try:
                process = subprocess.Popen(
                    _command(device),
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    env=env,
                    creationflags=0x08000000 if os.name == "nt" else 0,
                )
            except OSError:
                logger.warning("Could not start the GPU preflight", exc_info=True)
                return "unknown"
            deadline = time.monotonic() + TIMEOUT_SECONDS
            while process.poll() is None:
                if cancel_event is not None and cancel_event.is_set():
                    process.kill()
                    process.wait()
                    raise InterruptedError("Cancelled")
                if time.monotonic() > deadline:
                    process.kill()
                    process.wait()
                    logger.warning("The GPU preflight on %s did not finish in time", device)
                    return "unknown"
                time.sleep(0.1)
        check_cancelled()
        code = process.returncode
        if code == 0:
            return "passed"
        during_gpu_call = bool(CrashNotes(Path(directory)).running())
        tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-12:]
        logger.warning(
            "The GPU preflight on %s ended with exit code %s%s:\n%s",
            device,
            code,
            " during a GPU call" if during_gpu_call else "",
            "\n".join(tail),
        )
        return "crashed" if during_gpu_call and native_fault(code) else "unknown"


class Passed:
    """GPUs whose preflight finished, by the same key as the crash records."""

    def __init__(self, directory: Path):
        self.path = directory / "preflight.json"

    def __contains__(self, key: str) -> bool:
        return key in _read_json(self.path)

    def add(self, key: str) -> None:
        entries = _read_json(self.path)
        entries[key] = datetime.now(UTC).isoformat(timespec="seconds")
        try:
            _write_json(self.path, entries)
        except OSError:
            logger.warning("Could not store the GPU preflight result", exc_info=True)
