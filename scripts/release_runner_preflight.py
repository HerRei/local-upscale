#!/usr/bin/env python3
"""Fail before release work when a runner or artifact receiver is not ready."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

GIB = 1024**3
PLATFORMS = ("linux", "windows", "macos")


def validate_health(
    value: object,
    *,
    minimum_free_bytes: int,
    minimum_free_percent: float,
) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("artifact receiver /healthz did not return a JSON object")
    if value.get("status") != "ok":
        raise ValueError("artifact receiver /healthz status is not 'ok'")
    free_bytes = value.get("free_bytes")
    free_percent = value.get("free_percent")
    timestamp = value.get("timestamp")
    if not isinstance(free_bytes, int) or free_bytes < 0:
        raise ValueError("artifact receiver /healthz free_bytes is missing or invalid")
    if not isinstance(free_percent, (int, float)) or not 0 <= free_percent <= 100:
        raise ValueError("artifact receiver /healthz free_percent is missing or invalid")
    if not isinstance(timestamp, str) or not timestamp:
        raise ValueError("artifact receiver /healthz timestamp is missing or invalid")
    if free_bytes < minimum_free_bytes or float(free_percent) < minimum_free_percent:
        raise RuntimeError(
            "artifact receiver reserve is too low: "
            f"free_bytes={free_bytes}, required_bytes={minimum_free_bytes}, "
            f"free_percent={float(free_percent):.1f}, "
            f"required_percent={minimum_free_percent:.1f}"
        )
    return value


def fetch_health(
    server: str,
    *,
    minimum_free_bytes: int,
    minimum_free_percent: float,
    timeout: float = 15.0,
) -> dict[str, object]:
    endpoint = server.rstrip("/") + "/healthz"
    request = Request(endpoint, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - operator URL
            status = int(response.status)
            content_type = response.headers.get_content_type()
            body = response.read(64 * 1024)
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        raise RuntimeError(
            f"artifact receiver health check failed at {endpoint}: {error}"
        ) from error
    if status != 200:
        raise RuntimeError(f"artifact receiver health check returned HTTP {status}")
    if content_type != "application/json":
        raise ValueError(
            f"artifact receiver /healthz returned unexpected content type {content_type!r}"
        )
    try:
        value = json.loads(body)
    except json.JSONDecodeError as error:
        raise ValueError("artifact receiver /healthz returned invalid JSON") from error
    return validate_health(
        value,
        minimum_free_bytes=minimum_free_bytes,
        minimum_free_percent=minimum_free_percent,
    )


def validate_runner(
    platform: str,
    scratch_path: Path,
    minimum_scratch_bytes: int,
    tools: list[str],
) -> dict[str, object]:
    if platform not in PLATFORMS:
        raise ValueError(f"unsupported release platform: {platform!r}")
    if sys.version_info < (3, 11):
        raise RuntimeError("release runner requires Python 3.11 or newer")
    if not scratch_path.exists() or not scratch_path.is_dir():
        raise FileNotFoundError(f"required scratch path is unavailable: {scratch_path}")
    _total, _used, free = shutil.disk_usage(scratch_path)
    if free < minimum_scratch_bytes:
        raise RuntimeError(
            f"local scratch reserve is too low at {scratch_path}: "
            f"free_bytes={free}, required_bytes={minimum_scratch_bytes}"
        )
    missing = sorted(tool for tool in tools if shutil.which(tool) is None)
    if missing:
        raise RuntimeError("release runner is missing required tools: " + ", ".join(missing))
    return {
        "platform": platform,
        "scratch_path": str(scratch_path.resolve()),
        "scratch_free_bytes": free,
        "required_tools": tools,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    # Optional: a hosted runner cannot reach the LAN artifact receiver. Omit it
    # to run the runner-side checks only.
    parser.add_argument("--server")
    parser.add_argument("--platform", choices=PLATFORMS, required=True)
    parser.add_argument("--scratch-path", type=Path, required=True)
    parser.add_argument("--receiver-min-free-gib", type=float, default=100.0)
    parser.add_argument("--receiver-min-free-percent", type=float, default=20.0)
    parser.add_argument("--scratch-min-free-gib", type=float, required=True)
    parser.add_argument("--require-tool", action="append", default=[])
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)

    receiver: dict[str, object]
    if args.server:
        receiver = fetch_health(
            args.server,
            minimum_free_bytes=int(args.receiver_min_free_gib * GIB),
            minimum_free_percent=args.receiver_min_free_percent,
        )
    else:
        receiver = {
            "checked": False,
            "reason": "no --server given; runner-side checks only",
        }
    runner = validate_runner(
        args.platform,
        args.scratch_path,
        int(args.scratch_min_free_gib * GIB),
        args.require_tool,
    )
    report = {"schema_version": 1, "passed": True, "receiver": receiver, "runner": runner}
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
