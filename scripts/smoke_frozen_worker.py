#!/usr/bin/env python3
"""Smoke-test a frozen LocalSR worker through its JSON-lines protocol."""

from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path


def _reader(stream, output: queue.Queue[tuple[str, str]], channel: str) -> None:
    try:
        for line in stream:
            output.put((channel, line.rstrip("\r\n")))
    finally:
        output.put((channel, "__EOF__"))


def _write_json(process: subprocess.Popen[str], value: dict[str, object]) -> None:
    if process.stdin is None:
        raise RuntimeError("Worker stdin is unavailable")
    process.stdin.write(json.dumps(value, separators=(",", ":")) + "\n")
    process.stdin.flush()


def _worker_path(bundle: Path) -> Path:
    suffix = ".exe" if os.name == "nt" else ""
    worker = bundle / f"LocalSRWorker{suffix}"
    if not worker.is_file():
        raise FileNotFoundError(f"Frozen worker not found: {worker}")
    return worker


def smoke(bundle: Path, timeout: float = 180.0) -> dict[str, object]:
    """Start the frozen worker, query capabilities, and require a clean exit."""
    worker = _worker_path(bundle.resolve())
    started = time.monotonic()
    deadline = started + timeout
    messages: list[dict[str, object]] = []
    stderr_lines: list[str] = []
    events: queue.Queue[tuple[str, str]] = queue.Queue()
    process = subprocess.Popen(
        [str(worker)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    print(f"smoke: started {worker.name}; waiting for worker_ready", file=sys.stderr, flush=True)
    assert process.stdout is not None
    assert process.stderr is not None
    threads = [
        threading.Thread(target=_reader, args=(process.stdout, events, "stdout"), daemon=True),
        threading.Thread(target=_reader, args=(process.stderr, events, "stderr"), daemon=True),
    ]
    for thread in threads:
        thread.start()

    ready = False
    capabilities: dict[str, object] | None = None
    try:
        while capabilities is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Frozen worker smoke test exceeded {timeout:.0f}s")
            try:
                channel, line = events.get(timeout=min(1.0, remaining))
            except queue.Empty:
                if process.poll() is not None:
                    raise RuntimeError(
                        f"Worker exited with {process.returncode} before capabilities response"
                    ) from None
                continue
            if line == "__EOF__":
                if process.poll() is not None and capabilities is None:
                    raise RuntimeError(
                        f"Worker exited with {process.returncode} before capabilities response"
                    )
                continue
            if channel == "stderr":
                stderr_lines.append(line)
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                stderr_lines.append(f"non-JSON stdout: {line}")
                continue
            if not isinstance(message, dict):
                continue
            messages.append(message)
            message_type = message.get("type")
            if message_type == "worker_ready" and not ready:
                ready = True
                print(
                    "smoke: worker_ready received; requesting capabilities",
                    file=sys.stderr,
                    flush=True,
                )
                _write_json(process, {"type": "capabilities_request", "data": {}})
            elif message_type == "capabilities_info":
                if not ready:
                    raise RuntimeError("Worker reported capabilities before worker_ready")
                data = message.get("data")
                if not isinstance(data, dict):
                    raise RuntimeError("capabilities_info data is not an object")
                capabilities = data
                print("smoke: capabilities_info received", file=sys.stderr, flush=True)

        _write_json(process, {"type": "shutdown_request", "data": {}})
        if process.stdin:
            process.stdin.close()
        remaining = max(1.0, deadline - time.monotonic())
        returncode = process.wait(timeout=remaining)
        if returncode != 0:
            raise RuntimeError(f"Worker shutdown exit code was {returncode}")
        print("smoke: clean worker shutdown", file=sys.stderr, flush=True)
    except Exception:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        raise

    return {
        "schema_version": 1,
        "result": "PASS",
        "executable": worker.name,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "worker_ready": ready,
        "capabilities": capabilities,
        "message_types": [message.get("type") for message in messages],
        "stderr": stderr_lines[-100:],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = smoke(args.bundle, args.timeout)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)


if __name__ == "__main__":
    main()
