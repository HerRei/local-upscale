#!/usr/bin/env python3
"""Replay-bounded HMAC authentication shared by CI artifact clients and receiver."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import threading
import time
from collections.abc import Mapping

AUTH_SCHEME = "LocalSR-HMAC-SHA256"
AUTH_WINDOW_SECONDS = 300
NONCE_RE = re.compile(r"^[0-9a-f]{32}$")
SIGNED_HEADERS = (
    "Content-Length",
    "X-Run-Id",
    "X-Run-Attempt",
    "X-Platform",
    "X-Artifact-Name",
    "X-Content-SHA256",
)


def canonical_request(
    method: str,
    path: str,
    timestamp: str,
    nonce: str,
    headers: Mapping[str, str],
) -> bytes:
    value = {
        "method": method,
        "path": path,
        "timestamp": timestamp,
        "nonce": nonce,
        "headers": {name.lower(): headers.get(name, "") for name in SIGNED_HEADERS},
    }
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign_request(
    secret: bytes,
    method: str,
    path: str,
    timestamp: str,
    nonce: str,
    headers: Mapping[str, str],
) -> str:
    return hmac.new(
        secret,
        canonical_request(method, path, timestamp, nonce, headers),
        hashlib.sha256,
    ).hexdigest()


def verify_request(
    secret: bytes,
    authorization: str,
    method: str,
    path: str,
    timestamp: str,
    nonce: str,
    headers: Mapping[str, str],
    *,
    current_time: float | None = None,
) -> bool:
    prefix = AUTH_SCHEME + " "
    if not authorization.startswith(prefix) or not NONCE_RE.fullmatch(nonce):
        return False
    try:
        request_time = int(timestamp)
    except ValueError:
        return False
    now = time.time() if current_time is None else current_time
    if abs(now - request_time) > AUTH_WINDOW_SECONDS:
        return False
    supplied = authorization[len(prefix) :]
    if not re.fullmatch(r"[0-9a-f]{64}", supplied):
        return False
    expected = sign_request(secret, method, path, timestamp, nonce, headers)
    return hmac.compare_digest(supplied, expected)


class NonceCache:
    """Thread-safe, in-memory replay protection for the short authentication window."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seen: dict[str, float] = {}

    def claim(self, nonce: str, *, current_time: float | None = None) -> bool:
        now = time.time() if current_time is None else current_time
        with self._lock:
            self._seen = {value: expiry for value, expiry in self._seen.items() if expiry >= now}
            if nonce in self._seen:
                return False
            self._seen[nonce] = now + AUTH_WINDOW_SECONDS
            return True
