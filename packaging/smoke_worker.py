"""Exercise a packaged LocalSR worker without relying on a visible GUI."""

import json
import subprocess
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: smoke_worker.py PATH_TO_LOCALSR_WORKER", file=sys.stderr)
        return 2
    worker = Path(sys.argv[1])
    if not worker.is_file():
        print(f"Packaged worker not found: {worker}", file=sys.stderr)
        return 2

    requests = "\n".join(
        (
            json.dumps({"type": "capabilities_request", "data": {}}),
            json.dumps({"type": "shutdown_request", "data": {}}),
            "",
        )
    )
    completed = subprocess.run(
        [str(worker)],
        input=requests,
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
    )
    message_types = []
    for line in completed.stdout.splitlines():
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(message, dict):
            message_types.append(message.get("type"))

    required = {"worker_ready", "capabilities_info"}
    if completed.returncode != 0 or not required.issubset(message_types):
        print(completed.stdout, file=sys.stderr)
        print(completed.stderr, file=sys.stderr)
        print(
            f"Worker smoke test failed (exit {completed.returncode}, messages {message_types}).",
            file=sys.stderr,
        )
        return 1
    print("Packaged worker startup, capability IPC, and shutdown succeeded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
