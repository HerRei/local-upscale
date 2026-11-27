"""Verify that worker startup defers heavy imports without changing protocol behaviour."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DEFERRED_MODULES = (
    "spandrel",
    "cv2",
    "av",
    "coremltools",
    "rawpy",
    "onnxruntime",
    "torch",
    "numpy",
    "localsr.image_models.ddcolor",
    "localsr.video_models.seedvr2",
    "localsr.video_models.flashvsr",
    "localsr.core.media_bridge",
)


def _worker_env() -> dict[str, str]:
    return dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONUTF8="1")


def test_importing_worker_server_defers_heavy_modules():
    code = (
        "import json, sys\n"
        "import localsr.worker.server\n"
        f"targets = {DEFERRED_MODULES!r}\n"
        "loaded = [m for m in targets if m in sys.modules or any(k.startswith(m + '.') for k in sys.modules)]\n"
        "print(json.dumps(loaded))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_worker_env(),
        timeout=30,
        check=True,
    )
    loaded = json.loads(completed.stdout.strip())
    assert loaded == [], f"Unexpected heavy modules loaded on import: {loaded}"


def test_worker_startup_ready_handshake_and_capabilities():
    process = subprocess.Popen(
        [sys.executable, "-u", "-m", "localsr.worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(ROOT),
        env=_worker_env(),
        text=True,
        encoding="utf-8",
    )
    assert process.stdin is not None
    assert process.stdout is not None
    messages: list[dict] = []
    try:
        while True:
            line = process.stdout.readline()
            assert line, process.stderr.read() if process.stderr is not None else ""
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            messages.append(msg)
            kind = msg.get("type")
            if kind == "worker_ready":
                process.stdin.write(
                    json.dumps(
                        {
                            "type": "handshake_request",
                            "data": {
                                "client_name": "LocalSR",
                                "client_version": "0.7.0",
                                "protocol_version": 1,
                            },
                        }
                    )
                    + "\n"
                )
                process.stdin.flush()
            elif kind == "engine_info":
                process.stdin.write(json.dumps({"type": "capabilities_request", "data": {}}) + "\n")
                process.stdin.flush()
            elif kind == "capabilities_info":
                process.stdin.write(json.dumps({"type": "shutdown_request", "data": {}}) + "\n")
                process.stdin.flush()
                break
        returncode = process.wait(timeout=30)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)

    assert returncode == 0
    by_type = {msg["type"]: msg["data"] for msg in messages if "type" in msg}
    assert "worker_ready" in by_type
    info = by_type["engine_info"]
    assert info["engine_id"] == "localsr.pytorch-spandrel"
    assert "image" in info["features"]
    assert "compare_regions" in info["features"]
    assert "preview_regions" in info["features"]
    assert "spandrel_image" in info["video_engines"]
    caps = by_type["capabilities_info"]
    assert any(device["id"] == "cpu" for device in caps["devices"])


def test_worker_server_lazy_model_adapter_and_engine_allow_override():
    from localsr.worker.server import WorkerServer

    server = WorkerServer()
    sentinel_adapter = object()
    sentinel_engine = object()
    server.model_adapter = sentinel_adapter
    server.engine = sentinel_engine
    assert server.model_adapter is sentinel_adapter
    assert server.engine is sentinel_engine
