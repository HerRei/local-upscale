#!/usr/bin/env python3
"""Bounded loopback verification of the proposed Caddy download configuration."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def verify(caddy: Path, report_path: Path) -> dict:
    report = {
        "passed": False,
        "scope": "loopback HTTP; no public HTTPS or host reboot",
        "checks": [],
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="localsr-host-test-") as temporary:
        scratch = Path(temporary)
        public = scratch / "public"
        releases = public / "releases" / "test"
        releases.mkdir(parents=True)
        (public / "updates").mkdir()
        (public / "updates" / "beta.json").write_text('{"published":false}\n')
        (public / "health.txt").write_text("LocalSR test\n")
        (public / ".secret").write_text("not public")
        (public / "candidate.partial").write_text("not complete")
        (scratch / "outside.txt").write_text("outside root")
        payload = releases / "fixture.bin"
        block = bytes(range(256)) * 4096
        with payload.open("wb") as stream:
            for _ in range(64):
                stream.write(block)
        with payload.open("rb") as stream:
            expected = hashlib.file_digest(stream, "sha256").hexdigest()
        large = releases / "large.bin"
        large_size = 3 * 1024**3 + 127
        with large.open("wb") as stream:
            stream.truncate(large_size)
            stream.seek(large_size - 4)
            stream.write(b"TAIL")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        env = dict(
            os.environ,
            LOCALSR_RELEASE_ADDRESS=base,
            LOCALSR_RELEASE_ROOT=str(public),
            XDG_DATA_HOME=str(scratch / "state"),
            XDG_CONFIG_HOME=str(scratch / "config"),
        )
        config = ROOT / "packaging/hosting/Caddyfile"
        log_path = report_path.with_suffix(".server.log")

        def request(path, *, headers=None, method="GET"):
            req = urllib.request.Request(base + path, headers=headers or {}, method=method)
            try:
                return urllib.request.urlopen(req, timeout=15)
            except urllib.error.HTTPError as error:
                return error

        def launch(log):
            process = subprocess.Popen(
                [str(caddy), "run", "--config", str(config), "--adapter", "caddyfile"],
                env=env,
                stdout=log,
                stderr=log,
            )
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError("Caddy exited before accepting requests")
                try:
                    with request("/health.txt") as response:
                        if response.status == 200:
                            return process
                except urllib.error.URLError:
                    pass
                time.sleep(0.05)
            process.terminate()
            process.wait(timeout=10)
            raise TimeoutError("Loopback listener startup")

        def stop(process):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

        def check(name, **details):
            report["checks"].append({"name": name, "passed": True, **details})

        process = None
        with log_path.open("wb") as log:
            try:
                subprocess.run(
                    [str(caddy), "validate", "--config", str(config), "--adapter", "caddyfile"],
                    env=env,
                    stdout=log,
                    stderr=log,
                    check=True,
                    timeout=20,
                )
                process = launch(log)
                with request("/releases/test/large.bin", method="HEAD") as response:
                    assert response.status == 200
                    assert int(response.headers["Content-Length"]) == large_size
                    assert response.headers["Accept-Ranges"] == "bytes"
                with request("/releases/test/large.bin", headers={"Range": "bytes=-4"}) as response:
                    assert response.status == 206 and response.read() == b"TAIL"
                    assert (
                        response.headers["Content-Range"]
                        == f"bytes {large_size - 4}-{large_size - 1}/{large_size}"
                    )
                check("range-beyond-2gib", logical_bytes=large_size)

                with request("/releases/test/fixture.bin") as response:
                    etag = response.headers["ETag"]
                    assert "immutable" in response.headers["Cache-Control"]
                    prefix = response.read(1024**2)
                # Close the transfer early and resume against the same ETag.
                digest = hashlib.sha256(prefix)
                with request(
                    "/releases/test/fixture.bin",
                    headers={"Range": f"bytes={len(prefix)}-", "If-Range": etag},
                ) as response:
                    assert response.status == 206
                    while chunk := response.read(1024**2):
                        digest.update(chunk)
                assert digest.hexdigest() == expected
                check("interrupted-transfer-resume", bytes=payload.stat().st_size, sha256=expected)

                for path, status in [
                    ("/", 404),
                    ("/.secret", 404),
                    ("/candidate.partial", 404),
                    ("/../outside.txt", 404),
                    ("/releases/test/missing", 404),
                ]:
                    with request(path) as response:
                        assert response.status == status, (path, response.status)
                        assert "not public" not in response.read().decode()
                with request("/releases/test/missing") as response:
                    assert response.headers["Cache-Control"] == "no-store"
                with request("/health.txt", method="POST") as response:
                    assert response.status == 405
                with request("/updates/beta.json") as response:
                    assert (
                        response.headers["Cache-Control"] == "public, max-age=60, must-revalidate"
                    )
                with request(
                    "/releases/test/fixture.bin", headers={"If-None-Match": etag}
                ) as response:
                    assert response.status == 304
                with request(
                    "/releases/test/fixture.bin", headers={"Range": "bytes=999999999999-"}
                ) as response:
                    assert response.status == 416
                check("read-only-missing-hidden-cache-and-conditional-requests")

                def download(_):
                    digest = hashlib.sha256()
                    with request("/releases/test/fixture.bin") as response:
                        while chunk := response.read(1024**2):
                            digest.update(chunk)
                    assert digest.hexdigest() == expected

                started = time.monotonic()
                with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                    list(pool.map(download, range(4)))
                check(
                    "four-concurrent-downloads",
                    total_bytes=4 * payload.stat().st_size,
                    seconds=time.monotonic() - started,
                    measurement="loopback, not WAN",
                )
                stop(process)
                process = launch(log)
                with request(
                    "/releases/test/fixture.bin", headers={"Range": "bytes=0-255"}
                ) as response:
                    assert response.status == 206 and response.read() == bytes(range(256))
                    assert response.headers["ETag"] == etag
                check("process-restart-and-resume", host_reboot_tested=False)
                report["passed"] = True
            except Exception as error:
                report["error"] = repr(error)
                raise
            finally:
                if process is not None:
                    stop(process)
                report["server_stopped"] = True
                report_path.write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--caddy", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.caddy.resolve(), args.report.resolve()), indent=2))
