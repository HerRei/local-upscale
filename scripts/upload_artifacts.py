#!/usr/bin/env python3
"""Stream verified artifacts and sidecars to the Mac mini's atomic receiver."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlparse

try:
    from artifact_auth import AUTH_SCHEME, sign_request
except ModuleNotFoundError:  # imported as a repository module in unit tests
    from scripts.artifact_auth import AUTH_SCHEME, sign_request


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def connection_for(parsed):
    if parsed.scheme == "http":
        return http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=120)
    if parsed.scheme == "https":
        return http.client.HTTPSConnection(parsed.hostname, parsed.port or 443, timeout=120)
    raise ValueError(f"Unsupported server URL: {parsed.geturl()}")


def upload(
    server_url: str,
    token: str,
    run_id: str,
    attempt: str,
    platform: str,
    path: Path,
) -> dict[str, object]:
    parsed = urlparse(server_url)
    endpoint = (parsed.path.rstrip("/") if parsed.path else "") + "/v1/artifacts"
    digest = sha256(path)
    length = path.stat().st_size
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(16)
    signed_headers = {
        "Content-Length": str(length),
        "X-Run-Id": run_id,
        "X-Run-Attempt": attempt,
        "X-Platform": platform,
        "X-Artifact-Name": path.name,
        "X-Content-SHA256": digest,
    }
    signature = sign_request(token.encode(), "POST", endpoint, timestamp, nonce, signed_headers)
    connection = connection_for(parsed)
    connection.putrequest("POST", endpoint)
    connection.putheader("Authorization", f"{AUTH_SCHEME} {signature}")
    connection.putheader("X-Auth-Timestamp", timestamp)
    connection.putheader("X-Auth-Nonce", nonce)
    connection.putheader("Content-Type", "application/octet-stream")
    for name, value in signed_headers.items():
        connection.putheader(name, value)
    connection.endheaders()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            connection.send(chunk)
    response = connection.getresponse()
    body = response.read()
    connection.close()
    try:
        value = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"artifact server returned HTTP {response.status}: {body[:500]!r}"
        ) from exc
    if response.status not in {200, 201}:
        raise RuntimeError(f"artifact server returned HTTP {response.status}: {value}")
    if value.get("sha256") != digest:
        raise RuntimeError(f"artifact server acknowledged an unexpected digest: {value}")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", required=True)
    parser.add_argument("--token-env", default="CI_ARTIFACT_TOKEN")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--attempt", required=True)
    parser.add_argument("--platform", choices=("linux", "macos", "windows"), required=True)
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args()
    token = os.environ.get(args.token_env)
    if not token:
        raise RuntimeError(f"Required token environment variable is empty: {args.token_env}")
    for path in args.files:
        if not path.is_file():
            raise FileNotFoundError(path)
        last_error: Exception | None = None
        for retry in range(3):
            try:
                result = upload(args.server, token, args.run_id, args.attempt, args.platform, path)
                print(json.dumps(result, sort_keys=True))
                break
            except (OSError, RuntimeError) as exc:
                last_error = exc
                if retry == 2:
                    raise
                time.sleep(2**retry)
        else:  # pragma: no cover - loop always breaks or raises
            raise RuntimeError(last_error)


if __name__ == "__main__":
    main()
