"""Tests for the subprocess decoder fuzzing harness (#159)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_fuzzer():
    path = ROOT / "scripts" / "fuzz_decoders.py"
    spec = importlib.util.spec_from_file_location("fuzz_decoders", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_python_decoders_survive_mutated_tiny_files_in_bounded_subprocesses() -> None:
    fuzzer = _load_fuzzer()
    outcomes = fuzzer.run_fuzz_campaign(
        decoders=fuzzer.DECODERS,
        iterations=3,
        seed=42,
        timeout_seconds=15.0,
        memory_limit_bytes=768 * 1024 * 1024,
    )
    assert len(outcomes) == len(fuzzer.DECODERS) * 3
    failures = [item for item in outcomes if not item.ok]
    assert not failures, f"unexpected decoder failures: {failures}"


def test_fuzz_harness_enforces_timeout_memory_and_crash_detection(tmp_path: Path) -> None:
    fuzzer = _load_fuzzer()
    dummy = tmp_path / "dummy.bin"
    dummy.write_bytes(b"data")

    slow = fuzzer.run_decoder_subprocess(
        "image_load",
        dummy,
        timeout_seconds=0.15,
        command=[sys.executable, "-c", "import time; time.sleep(5)"],
    )
    assert not slow.ok
    assert slow.reason == "timeout"

    hungry = fuzzer.run_decoder_subprocess(
        "image_load",
        dummy,
        timeout_seconds=5.0,
        memory_limit_bytes=32 * 1024 * 1024,
        command=[
            sys.executable,
            "-c",
            "import time; buf = bytearray(96 * 1024 * 1024); buf[::4096] = b'x' * (len(buf) // 4096); time.sleep(2)",
        ],
    )
    assert not hungry.ok
    assert hungry.reason == "memory_limit"

    # A launcher stub spawning a child process (as Windows venv python.exe does)
    # must still have its descendant RSS caught and killed.
    child_code = (
        "import time; buf = bytearray(96 * 1024 * 1024); "
        "buf[::4096] = b'x' * (len(buf) // 4096); time.sleep(2)"
    )
    nested_hungry = fuzzer.run_decoder_subprocess(
        "image_load",
        dummy,
        timeout_seconds=5.0,
        memory_limit_bytes=32 * 1024 * 1024,
        command=[
            sys.executable,
            "-c",
            f"import subprocess, sys; raise SystemExit(subprocess.call([sys.executable, '-c', {child_code!r}]))",
        ],
    )
    assert not nested_hungry.ok
    assert nested_hungry.reason == "memory_limit"

    crash = fuzzer.run_decoder_subprocess(
        "video_probe",
        dummy,
        timeout_seconds=5.0,
        command=[sys.executable, "-c", "import os, signal; os.kill(os.getpid(), signal.SIGABRT)"],
    )
    assert not crash.ok
    assert crash.reason in {"signal", "crash"}
