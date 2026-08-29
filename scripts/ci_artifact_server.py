#!/usr/bin/env python3
"""Authenticated, atomic HTTP receiver for completed CI artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    from artifact_auth import NonceCache, verify_request
except ModuleNotFoundError:  # imported as a repository module in unit tests
    from scripts.artifact_auth import NonceCache, verify_request

GIB = 1024**3
COMPONENT_RE = re.compile(r"^[A-Za-z0-9._+-]+$")
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
UPLOAD_LOCK = threading.Lock()


def now() -> str:
    return datetime.now(UTC).isoformat()


def atomic_json(path: Path, value: object) -> None:
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def safe_component(value: str | None, label: str) -> str:
    if value is None or not COMPONENT_RE.fullmatch(value) or value in {".", ".."}:
        raise ValueError(f"Invalid {label}")
    return value


class ArtifactServer(ThreadingHTTPServer):
    root: Path
    token: bytes
    min_free_gib: float
    min_free_percent: float
    nonces: NonceCache


class Handler(BaseHTTPRequestHandler):
    server: ArtifactServer
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        # BaseHTTPRequestHandler never receives the token as a URL/query value.
        super().log_message(fmt, *args)

    def respond(self, status: HTTPStatus, value: object) -> None:
        body = (json.dumps(value, sort_keys=True) + "\n").encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authenticated(self) -> bool:
        timestamp = self.headers.get("X-Auth-Timestamp", "")
        nonce = self.headers.get("X-Auth-Nonce", "")
        if not verify_request(
            self.server.token,
            self.headers.get("Authorization", ""),
            self.command,
            self.path,
            timestamp,
            nonce,
            self.headers,
        ):
            return False
        return self.server.nonces.claim(nonce)

    def do_GET(self) -> None:  # noqa: N802 - HTTP verb API
        if self.path != "/healthz":
            self.respond(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        total, _used, free = shutil.disk_usage(self.server.root)
        self.respond(
            HTTPStatus.OK,
            {
                "status": "ok",
                "free_bytes": free,
                "free_percent": 100 * free / total,
                "timestamp": now(),
            },
        )

    def do_POST(self) -> None:  # noqa: N802 - HTTP verb API
        if self.path != "/v1/artifacts":
            self.respond(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        if not self.authenticated():
            self.respond(HTTPStatus.UNAUTHORIZED, {"error": "authentication required"})
            return
        try:
            result = self.receive_artifact()
        except ValueError as exc:
            self.respond(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except OSError as exc:
            self.respond(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"storage error: {exc}"})
        else:
            self.respond(HTTPStatus.CREATED, result)

    def receive_artifact(self) -> dict[str, object]:
        length_text = self.headers.get("Content-Length")
        if not length_text or not length_text.isdigit():
            raise ValueError("Content-Length is required")
        length = int(length_text)
        if length < 1 or length > 32 * GIB:
            raise ValueError("Content-Length is outside the allowed range")
        run_id = safe_component(self.headers.get("X-Run-Id"), "run id")
        attempt = safe_component(self.headers.get("X-Run-Attempt"), "run attempt")
        platform = safe_component(self.headers.get("X-Platform"), "platform")
        filename = safe_component(self.headers.get("X-Artifact-Name"), "artifact name")
        expected_digest = self.headers.get("X-Content-SHA256", "").lower()
        if not SHA256_RE.fullmatch(expected_digest):
            raise ValueError("X-Content-SHA256 must be a SHA256 digest")
        if not run_id.isdigit() or not attempt.isdigit():
            raise ValueError("run id and attempt must be numeric")
        if platform not in {"linux", "macos", "windows"}:
            raise ValueError("unsupported platform")

        destination_dir = self.server.root / run_id / attempt / platform
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / filename
        marker = destination_dir / (".copying-" + filename)
        if destination.exists():
            existing = file_sha256(destination)
            if existing == expected_digest and destination.stat().st_size == length:
                return {
                    "status": "already-present",
                    "path": str(destination.relative_to(self.server.root)),
                    "sha256": existing,
                    "size": length,
                }
            raise ValueError(f"destination already exists with different content: {filename}")

        with UPLOAD_LOCK:
            total, _used, free = shutil.disk_usage(self.server.root)
            reserve = max(
                int(self.server.min_free_gib * GIB), int(total * self.server.min_free_percent / 100)
            )
            if free - length < reserve:
                raise ValueError(
                    f"upload would violate HDD reserve: free={free}, content={length}, reserve={reserve}"
                )
            atomic_json(marker, {"started_at": now(), "filename": filename, "size": length})
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".upload-", suffix=".part", dir=destination_dir
            )
            temporary = Path(temporary_name)
            digest = hashlib.sha256()
            remaining = length
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    while remaining:
                        chunk = self.rfile.read(min(4 * 1024 * 1024, remaining))
                        if not chunk:
                            raise ValueError(f"request ended with {remaining} bytes missing")
                        stream.write(chunk)
                        digest.update(chunk)
                        remaining -= len(chunk)
                    stream.flush()
                    os.fsync(stream.fileno())
                actual_digest = digest.hexdigest()
                if actual_digest != expected_digest:
                    raise ValueError(
                        f"content SHA256 mismatch: received={actual_digest}, expected={expected_digest}"
                    )
                os.replace(temporary, destination)
                directory_fd = os.open(destination_dir, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            finally:
                if temporary.exists():
                    temporary.unlink()
                if marker.exists():
                    marker.unlink()

        return {
            "status": "stored",
            "path": str(destination.relative_to(self.server.root)),
            "sha256": expected_digest,
            "size": length,
        }


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--root", type=Path, required=True)
    result.add_argument("--token-file", type=Path, required=True)
    result.add_argument("--bind", default="127.0.0.1")
    result.add_argument("--port", type=int, default=8000)
    result.add_argument("--min-free-gib", type=float, default=100.0)
    result.add_argument("--min-free-percent", type=float, default=20.0)
    return result


def main() -> None:
    args = parser().parse_args()
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    if root == Path(root.anchor):
        raise ValueError("artifact root cannot be a filesystem root")
    token = args.token_file.read_bytes().strip()
    if len(token) < 32:
        raise ValueError("artifact upload token must contain at least 32 bytes")
    server = ArtifactServer((args.bind, args.port), Handler)
    server.root = root
    server.token = token
    server.min_free_gib = args.min_free_gib
    server.min_free_percent = args.min_free_percent
    server.nonces = NonceCache()
    print(f"LocalSR artifact receiver listening on {args.bind}:{args.port}; root={root}")
    server.serve_forever()


if __name__ == "__main__":
    main()
