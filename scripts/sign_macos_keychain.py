#!/usr/bin/env python3
"""Sign a prepared macOS bundle inside out using an existing Keychain identity.

No private key export, Keychain configuration changes, or GUI automation.
Notarization and distribution sealing happen after the signed worker is tested.
"""

from __future__ import annotations

import argparse
import json
import plistlib
import re
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MACHO = {
    bytes.fromhex(value)
    for value in (
        "cffaedfe",
        "cefaedfe",
        "feedfacf",
        "feedface",
        "cafebabe",
        "cafebabf",
    )
}


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=True, timeout=300)


def sign(path: Path, identity: str, *, worker: bool = False) -> None:
    command = ["codesign", "--force", "--sign", identity, "--options", "runtime", "--timestamp"]
    if worker:
        command += ["--entitlements", str(ROOT / "packaging/macos/worker-entitlements.plist")]
    command.append(str(path))
    for attempt in range(3):
        try:
            run(command)
            return
        except subprocess.CalledProcessError as error:
            message = error.stderr or ""
            if attempt == 2 or not any(
                word in message.lower()
                for word in (
                    "timestamp service",
                    "timestamp server",
                    "timestamps differ",
                    "temporarily unavailable",
                )
            ):
                raise RuntimeError(f"Signing failed for {path.name}: {message.strip()}") from None
            time.sleep(2 * (attempt + 1))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    parser.add_argument("--identity", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    app = args.app.resolve(strict=True)
    if app.suffix != ".app" or not args.identity.startswith("Developer ID Application:"):
        parser.error("An existing .app and Developer ID Application identity are required")
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    for field in ("CFBundleShortVersionString", "CFBundleVersion"):
        if not re.fullmatch(r"\d+(?:\.\d+){0,2}", info.get(field, "")):
            parser.error(f"{field} must use the numeric Apple bundle version")
    binaries, nested = [], []
    for path in app.rglob("*"):
        if path.is_symlink():
            continue
        if path.is_dir():
            if path.suffix in {".app", ".framework", ".xpc", ".bundle"}:
                nested.append(path)
        elif path.is_file():
            with path.open("rb") as stream:
                if stream.read(4) in MACHO:
                    binaries.append(path)
    for index, path in enumerate(sorted(binaries), 1):
        sign(path, args.identity, worker=path.name == "localsr-worker")
        if index % 20 == 0 or index == len(binaries):
            print(f"Signed {index}/{len(binaries)} native files", flush=True)
    for path in sorted(nested, key=lambda p: len(p.parts), reverse=True):
        sign(path, args.identity)
    sign(app, args.identity)
    run(["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app)])
    detail = run(["codesign", "--display", "--verbose=4", str(app)]).stderr
    if (
        f"Authority={args.identity}" not in detail
        or "Timestamp=" not in detail
        or "runtime" not in detail
    ):
        raise RuntimeError(
            "The final bundle lacks its expected identity, timestamp or hardened runtime"
        )
    report = {
        "status": "developer-id-signed-awaiting-notarization",
        "identity": args.identity,
        "app": str(app),
        "native_files_signed": len(binaries),
        "nested_bundles_signed": len(nested),
        "hardened_runtime": True,
        "timestamp": next(
            line[10:] for line in detail.splitlines() if line.startswith("Timestamp=")
        ),
        "deep_strict_verification": True,
        "worker_entitlements": ["allow-jit", "allow-unsigned-executable-memory"],
        "library_validation_disabled": False,
        "notarized": False,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print("Complete bundle signature verified; notarization is next.", flush=True)


if __name__ == "__main__":
    main()
