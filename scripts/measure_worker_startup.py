#!/usr/bin/env python3
"""Measure LocalSR worker startup time from process launch to ready and handshake."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def measure_once(python_executable: Path, source_root: Path) -> tuple[float, float, dict]:
    env = dict(os.environ, PYTHONPATH=str(source_root), PYTHONUTF8="1")
    started = time.perf_counter()
    process = subprocess.Popen(
        [str(python_executable), "-u", "-m", "localsr.worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(ROOT),
        env=env,
        text=True,
        encoding="utf-8",
    )
    assert process.stdin is not None
    assert process.stdout is not None
    ready_ms: float | None = None
    engine_info_ms: float | None = None
    engine_info_data: dict = {}
    try:
        while True:
            line = process.stdout.readline()
            if not line:
                stderr_text = process.stderr.read() if process.stderr is not None else ""
                raise RuntimeError(
                    f"Worker exited before completing handshake (code={process.poll()}): {stderr_text}"
                )
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg_type = message.get("type")
            if msg_type == "worker_ready" and ready_ms is None:
                ready_ms = (time.perf_counter() - started) * 1000.0
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
            elif msg_type == "engine_info":
                engine_info_ms = (time.perf_counter() - started) * 1000.0
                engine_info_data = message.get("data", {})
                process.stdin.write(json.dumps({"type": "shutdown_request", "data": {}}) + "\n")
                process.stdin.flush()
                break
        process.wait(timeout=15)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
    assert ready_ms is not None and engine_info_ms is not None
    return ready_ms, engine_info_ms, engine_info_data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    args = parser.parse_args()

    source_root = ROOT / "src"
    ready_samples: list[float] = []
    engine_info_samples: list[float] = []
    last_info: dict = {}

    for index in range(args.runs):
        ready_ms, info_ms, info = measure_once(args.python, source_root)
        ready_samples.append(ready_ms)
        engine_info_samples.append(info_ms)
        last_info = info
        print(f"run {index + 1}: worker_ready={ready_ms:.1f} ms, engine_info={info_ms:.1f} ms")

    print(
        f"median ({args.runs} runs): "
        f"worker_ready={statistics.median(ready_samples):.1f} ms, "
        f"engine_info={statistics.median(engine_info_samples):.1f} ms"
    )
    print(f"engine_info={json.dumps(last_info, sort_keys=True)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
