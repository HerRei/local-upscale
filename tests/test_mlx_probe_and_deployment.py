"""Issue #183: MLX that cannot load must not end the engine, and the release gate must
refuse native files newer than the app's minimum macOS."""

import struct
import subprocess
import sys
from pathlib import Path

import pytest

from localsr.core import video_engines

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import verify_macos_deployment as gate  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_probe():
    video_engines._mlx_child_check.cache_clear()
    yield
    video_engines._mlx_child_check.cache_clear()


def _apple_silicon(monkeypatch):
    monkeypatch.setattr(video_engines.sys, "platform", "darwin")
    monkeypatch.setattr(video_engines.platform, "machine", lambda: "arm64")
    monkeypatch.delitem(sys.modules, "mlx.core", raising=False)
    monkeypatch.setattr(video_engines, "find_spec", lambda name: object())


def test_trapping_mlx_turns_flashvsr_off_instead_of_ending_the_engine(monkeypatch):
    _apple_silicon(monkeypatch)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        # What 0.6.0 did on macOS 15: SIGTRAP while loading mlx.metallib.
        return subprocess.CompletedProcess(command, -5, b"", b"brk #0x1\n")

    monkeypatch.setattr(video_engines.subprocess, "run", run)
    issue = video_engines.flashvsr_runtime_issue()
    assert issue is not None and "could not start MLX" in issue
    assert calls and calls[0][-1] == video_engines.MLX_PROBE_ARGUMENT
    # The answer is kept: the handshake asks several times.
    video_engines.flashvsr_runtime_issue()
    assert len(calls) == 1


def test_working_mlx_keeps_flashvsr(monkeypatch):
    _apple_silicon(monkeypatch)
    monkeypatch.setattr(
        video_engines.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, b"", b""),
    )
    assert video_engines.flashvsr_runtime_issue() is None


def test_probe_that_hangs_counts_as_unusable(monkeypatch):
    _apple_silicon(monkeypatch)

    def run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs.get("timeout"))

    monkeypatch.setattr(video_engines.subprocess, "run", run)
    assert video_engines.mlx_usable() is False


@pytest.mark.skipif(
    sys.platform != "darwin" or video_engines.platform.machine() != "arm64",
    reason="needs MLX on Apple silicon",
)
def test_real_probe_child_runs():
    pytest.importorskip("mlx")
    assert video_engines._mlx_child_check() is True


def _macho(minos: tuple[int, int], platform: int = 1) -> bytes:
    packed = (minos[0] << 16) | (minos[1] << 8)
    command = struct.pack("<IIIIII", 0x32, 24, platform, packed, packed, 0)
    header = struct.pack("<IiiIIIII", 0xFEEDFACF, 0x0100000C, 0, 6, 1, len(command), 0, 0)
    return header + command


def _metallib(major: int, minor: int) -> bytes:
    return (
        b"MTLB"
        + bytes.fromhex("01800200090000 81".replace(" ", ""))
        + struct.pack("<HH", major, minor)
        + b"\0" * 16
    )


def test_header_parsers():
    assert gate.macho_minimum(_macho((26, 2))) == (26, 2)
    assert gate.macho_minimum(_macho((14, 0))) == (14, 0)
    assert gate.macho_minimum(_macho((17, 0), platform=2)) is None  # iOS slice
    assert gate.metallib_target(_metallib(26, 2)) == (26, 2)
    assert gate.metallib_target(b"not a metallib") is None


def test_fat_file_uses_its_newest_slice():
    slices = [_macho((14, 0)), _macho((15, 0))]
    offset = 4096
    table = b""
    body = b""
    for data in slices:
        table += struct.pack(">IIIII", 0x0100000C, 0, offset + len(body), len(data), 12)
        body += data
    fat = struct.pack(">II", 0xCAFEBABE, len(slices)) + table
    fat = fat.ljust(offset, b"\0") + body
    assert gate.macho_minimum(fat) == (15, 0)


def test_gate_refuses_files_newer_than_the_minimum(tmp_path, capsys):
    app = tmp_path / "LocalSR.app"
    lib = app / "Contents" / "Resources" / "engine" / "_internal"
    lib.mkdir(parents=True)
    (lib / "libok.dylib").write_bytes(_macho((14, 0)))
    (lib / "notes.txt").write_text("text")
    assert gate.main(["--tree", str(app), "--minimum", "14.0"]) == 0

    (lib / "mlx.metallib").write_bytes(_metallib(26, 2))
    (lib / "libmlx.dylib").write_bytes(_macho((26, 2)))
    assert gate.main(["--tree", str(app), "--minimum", "14.0"]) == 1
    output = capsys.readouterr().out
    assert "mlx.metallib" in output and "libmlx.dylib" in output
    assert "libok.dylib" not in output
