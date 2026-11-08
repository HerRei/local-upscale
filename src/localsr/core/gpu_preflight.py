"""A GPU's first run, in a separate process.

A graphics driver can crash the process that uses it, beyond the reach of any handler:
Intel's OpenVINO with a 2020 driver on a UHD 620 crashes while compiling a single
convolution. Before the engine opens its first session on a DirectML or OpenVINO device,
a child process runs a tiny model on it through the same code. A child that dies marks
the GPU as crashed, and the engine stays up; one that finishes lets the engine go ahead.
The result is kept per device, driver and LocalSR version.
"""

import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

logger = logging.getLogger(__name__)

PREFLIGHT_ARGUMENT = "--gpu-preflight"
# Set in the child: it must not start a preflight of its own.
CHILD_ENV = "LOCALSR_GPU_PREFLIGHT_CHILD"
TIMEOUT_SECONDS = 300.0
# The child raised a Python error, for example a check that failed: no crash. The
# engine's own check then decides about the GPU.
FAILED_IN_PYTHON = 3


def child_main(argv: list[str]) -> int:
    """Run a one-convolution model on the device ``argv[0]`` through the engine's ONNX
    route, including its comparison with the CPU."""
    try:
        import spandrel
        import torch

        from .inference import _onnx_route
        from .onnx_runtime import OnnxImageDescriptor

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
        with torch.no_grad():
            converted(torch.rand(1, 3, 64, 64, generator=torch.Generator().manual_seed(0)))
    except Exception:  # noqa: BLE001 - reported through the exit code
        logger.exception("The GPU preflight failed without a crash")
        return FAILED_IN_PYTHON
    return 0


def _command(device: str) -> list[str]:
    command = [sys.executable]
    if not getattr(sys, "frozen", False):
        command += ["-m", "localsr.worker"]
    return [*command, PREFLIGHT_ARGUMENT, device]


def run(device: str, cancel_event: threading.Event | None = None) -> str:
    """ "passed", "crashed" or "unknown" (it did not finish in time or could not start)."""
    from .onnx_runtime import CRASHED_ENGINE_ENV, GATE_CACHE_ENV

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
        if process.returncode in (0, FAILED_IN_PYTHON):
            return "passed"
        tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-12:]
        logger.warning(
            "The GPU preflight on %s crashed with exit code %s:\n%s",
            device,
            process.returncode,
            "\n".join(tail),
        )
        return "crashed"


class Passed:
    """GPUs whose preflight finished, by the same key as the crash notes."""

    def __init__(self, directory: Path):
        self.path = directory / "preflight.json"

    def _read(self) -> dict:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def __contains__(self, key: str) -> bool:
        return key in self._read()

    def add(self, key: str) -> None:
        entries = self._read()
        entries[key] = datetime.now(UTC).isoformat(timespec="seconds")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(f".{os.getpid()}.tmp")
            temporary.write_text(json.dumps(entries, indent=2), encoding="utf-8")
            os.replace(temporary, self.path)
        except OSError:
            logger.warning("Could not store the GPU preflight result", exc_info=True)
