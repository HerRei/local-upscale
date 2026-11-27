"""Verify that worker startup defers heavy imports without changing protocol behaviour."""

from __future__ import annotations

import ast
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


def _function_level_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(function):
            if isinstance(node, ast.Import):
                found.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.add(node.module)
    return {name for name in found if name.split(".")[0] not in sys.stdlib_module_names}


def test_the_import_check_covers_every_module_the_server_defers():
    from localsr.worker.server import DEFERRED_IMPORTS

    server = ROOT / "src" / "localsr" / "worker" / "server.py"
    assert set(DEFERRED_IMPORTS) == _function_level_imports(server)


def test_the_import_check_imports_them_all_through_the_worker_entry_point():
    completed = subprocess.run(
        [sys.executable, "-m", "localsr.worker", "--import-check"],
        env=_worker_env(),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    from localsr.worker.server import DEFERRED_IMPORTS

    report = json.loads(completed.stdout.strip().splitlines()[-1])
    assert report == {"checked": len(DEFERRED_IMPORTS), "failed": {}}


def test_the_import_check_names_every_module_that_is_missing(capsys):
    from localsr.worker.server import import_check

    assert import_check(("json", "localsr.no_such_module", "no_such_package")) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["checked"] == 3
    assert sorted(report["failed"]) == ["localsr.no_such_module", "no_such_package"]
    assert report["failed"]["no_such_package"].startswith("ModuleNotFoundError")
