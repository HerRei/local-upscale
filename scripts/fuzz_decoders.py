#!/usr/bin/env python3
"""Subprocess fuzzing harness for LocalSR's untrusted-input Python decoders.

Exercises image load, result preview, video probe and camera RAW development on
mutated tiny files inside isolated child processes bounded by wall-clock time
and RSS memory limits.
"""

from __future__ import annotations

import argparse
import os
import random
import subprocess
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
import psutil
from PIL import Image

DECODERS = ("image_load", "result_preview", "video_probe", "raw_develop")
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_MEMORY_LIMIT_MIB = 768
MAX_MUTATED_BYTES = 65_536


@dataclass(frozen=True)
class FuzzOutcome:
    """Outcome of running one decoder subprocess on one input file."""

    ok: bool
    decoder: str
    path: Path
    reason: str
    detail: str = ""
    returncode: int | None = None


def _apply_os_memory_limit(memory_limit_bytes: int) -> None:
    """Apply an OS virtual address-space guard when the kernel permits lowering it."""
    try:
        import resource
    except ImportError:
        return
    limit = getattr(resource, "RLIMIT_AS", None)
    if limit is None:
        return
    # RLIMIT_AS caps virtual address space rather than RSS; PyTorch, OpenBLAS, and
    # LibRaw map multi-gigabyte shared/arena regions before touching physical pages.
    virtual_limit = max(memory_limit_bytes * 8, 4 * 1024 * 1024 * 1024)
    try:
        _, hard = resource.getrlimit(limit)
        target = virtual_limit if hard < 0 else min(virtual_limit, hard)
        resource.setrlimit(limit, (target, hard))
    except (OSError, ValueError):
        # macOS Darwin refuses RLIMIT_AS below existing Mach VM regions.
        pass


def _tree_rss_bytes(ps_proc: psutil.Process) -> int:
    """Sum RSS across ``ps_proc`` and its children (Windows venv launchers spawn a child)."""
    processes = [ps_proc]
    try:
        processes.extend(ps_proc.children(recursive=True))
    except psutil.Error:
        pass
    total = 0
    for member in processes:
        try:
            total += member.memory_info().rss
        except psutil.Error:
            pass
    return total


def _kill_process_tree(proc: subprocess.Popen[str], ps_proc: psutil.Process | None) -> None:
    if ps_proc is not None:
        try:
            children = ps_proc.children(recursive=True)
        except psutil.Error:
            children = []
        for child in children:
            try:
                child.kill()
            except psutil.Error:
                pass
    proc.kill()
    proc.communicate()


def _run_one(decoder: str, path: str, memory_limit_bytes: int) -> int:
    _apply_os_memory_limit(memory_limit_bytes)
    try:
        if decoder == "image_load":
            from localsr.core.image_io import ImageManager, UnreadableImageError

            try:
                ImageManager().load(path)
            except (UnreadableImageError, ValueError, OSError):
                return 0
            return 0

        if decoder == "result_preview":
            from localsr.protocol.messages import PreviewFailed, PreviewReady
            from localsr.worker.result_preview import render_result_preview

            message = render_result_preview({"image_path": path, "max_dimension": 128})
            if not isinstance(message, (PreviewReady, PreviewFailed)):
                print(f"unexpected preview result: {type(message)!r}", file=sys.stderr)
                return 3
            return 0

        if decoder == "video_probe":
            from localsr.core.media_codecs import UndecodableVideoError
            from localsr.core.video_io import probe_video

            try:
                probe_video(path)
            except (UndecodableVideoError, ValueError, OSError, av.FFmpegError):
                return 0
            return 0

        if decoder == "raw_develop":
            from localsr.core.image_io import UnreadableImageError, _develop_raw

            try:
                _develop_raw(path)
            except (UnreadableImageError, ValueError, OSError):
                return 0
            return 0

        print(f"unknown decoder: {decoder}", file=sys.stderr)
        return 2
    except Exception:
        traceback.print_exc()
        return 2


def build_seed_files(directory: Path) -> dict[str, list[Path]]:
    """Create tiny valid/minimal seed files for each decoder under ``directory``."""
    directory.mkdir(parents=True, exist_ok=True)
    rgb = np.zeros((16, 16, 3), dtype=np.uint8)
    rgb[4:12, 4:12] = [200, 120, 40]
    picture = Image.fromarray(rgb, mode="RGB")

    image_seeds: list[Path] = []
    for name, fmt in (
        ("seed.png", "PNG"),
        ("seed.jpg", "JPEG"),
        ("seed.tif", "TIFF"),
        ("seed.webp", "WEBP"),
        ("seed.gif", "GIF"),
    ):
        target = directory / name
        picture.save(target, format=fmt)
        image_seeds.append(target)

    preview_seeds = image_seeds[:3]

    mp4_seed = directory / "seed.mp4"
    with av.open(str(mp4_seed), mode="w", format="mp4") as container:
        stream = container.add_stream("libvpx-vp9", rate=24)
        stream.width = 16
        stream.height = 16
        stream.pix_fmt = "yuv420p"
        stream.options = {"lossless": "1", "lag-in-frames": "0"}
        for index in range(2):
            frame = av.VideoFrame.from_ndarray(rgb, format="rgb24")
            frame.pts = index
            frame.time_base = Fraction(1, 24)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)

    mkv_seed = directory / "seed.mkv"
    with av.open(str(mkv_seed), mode="w", format="matroska") as container:
        stream = container.add_stream("ffv1", rate=24)
        stream.width = 16
        stream.height = 16
        stream.pix_fmt = "yuv420p"
        frame = av.VideoFrame.from_ndarray(rgb, format="rgb24")
        frame.pts = 0
        frame.time_base = Fraction(1, 24)
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)

    # Minimal little-endian TIFF/DNG header with one IFD entry for LibRaw fuzzing.
    dng_seed = directory / "seed.dng"
    dng_seed.write_bytes(
        b"II*\x00\x08\x00\x00\x00\x01\x00\x00\x01\x03\x00\x01\x00\x00\x00\x10\x00\x00\x00\x00\x00\x00\x00"
    )

    return {
        "image_load": image_seeds,
        "result_preview": preview_seeds,
        "video_probe": [mp4_seed, mkv_seed],
        "raw_develop": [dng_seed],
    }


def mutate_bytes(data: bytes, rng: random.Random, *, max_bytes: int = MAX_MUTATED_BYTES) -> bytes:
    """Apply bounded random byte/header mutations to a tiny seed buffer."""
    buf = bytearray(data[:max_bytes])
    if not buf:
        return rng.randbytes(rng.randint(1, 64))
    for _ in range(rng.randint(1, 4)):
        op = rng.randrange(5)
        if op == 0 and buf:
            idx = rng.randrange(len(buf))
            buf[idx] ^= 1 << rng.randrange(8)
        elif op == 1 and len(buf) > 1:
            start = rng.randrange(len(buf))
            end = min(len(buf), start + rng.randint(1, min(16, len(buf) - start)))
            buf[start:end] = rng.randbytes(end - start)
        elif op == 2 and len(buf) > 1:
            cut = rng.randint(0, len(buf) - 1)
            del buf[cut:]
        elif op == 3:
            extra = rng.randbytes(rng.randint(1, 32))
            pos = rng.randrange(len(buf) + 1)
            buf[pos:pos] = extra
        else:
            # Stamp extreme 32-bit values (dimension/size edge cases).
            extreme = rng.choice(
                (b"\x00\x00\x00\x00", b"\xff\xff\xff\xff", b"\x7f\xff\xff\xff", b"\xff\xff\x00\x00")
            )
            pos = rng.randrange(max(1, len(buf) - 3))
            buf[pos : pos + 4] = extreme
    return bytes(buf[:max_bytes])


def run_decoder_subprocess(
    decoder: str,
    path: Path,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    memory_limit_bytes: int = DEFAULT_MEMORY_LIMIT_MIB * 1024 * 1024,
    command: list[str] | None = None,
) -> FuzzOutcome:
    """Run one decoder invocation in an isolated subprocess with time and RSS bounds."""
    if command is None:
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--run-one",
            decoder,
            str(path),
            "--memory-limit-bytes",
            str(memory_limit_bytes),
        ]
    env = dict(os.environ)
    src_dir = str(Path(__file__).resolve().parents[1] / "src")
    pythonpath = env.get("PYTHONPATH", "")
    if src_dir not in pythonpath.split(os.pathsep):
        env["PYTHONPATH"] = f"{src_dir}{os.pathsep}{pythonpath}" if pythonpath else src_dir
    # Keep OpenMP / OpenBLAS / glibc thread arenas single-threaded in the fuzz child so
    # virtual address space stays small under RLIMIT_AS on Linux.
    env.setdefault("OMP_NUM_THREADS", "1")
    env.setdefault("OPENBLAS_NUM_THREADS", "1")
    env.setdefault("MKL_NUM_THREADS", "1")
    env.setdefault("MALLOC_ARENA_MAX", "2")

    proc = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    ps_proc: psutil.Process | None = None
    try:
        ps_proc = psutil.Process(proc.pid)
    except psutil.Error:
        ps_proc = None

    started = time.monotonic()
    while True:
        code = proc.poll()
        if code is not None:
            stdout, stderr = proc.communicate()
            if code == 0:
                return FuzzOutcome(ok=True, decoder=decoder, path=path, reason="ok", returncode=0)
            reason = "signal" if code < 0 or code >= 128 else "crash"
            detail = (stderr or stdout).strip()[-400:]
            return FuzzOutcome(
                ok=False,
                decoder=decoder,
                path=path,
                reason=reason,
                detail=detail,
                returncode=code,
            )

        if ps_proc is not None:
            rss = _tree_rss_bytes(ps_proc)
            if rss > memory_limit_bytes:
                _kill_process_tree(proc, ps_proc)
                return FuzzOutcome(
                    ok=False,
                    decoder=decoder,
                    path=path,
                    reason="memory_limit",
                    detail=f"RSS {rss} bytes exceeded limit {memory_limit_bytes}",
                    returncode=None,
                )

        if time.monotonic() - started > timeout_seconds:
            _kill_process_tree(proc, ps_proc)
            return FuzzOutcome(
                ok=False,
                decoder=decoder,
                path=path,
                reason="timeout",
                detail=f"exceeded {timeout_seconds:.2f}s",
                returncode=None,
            )
        time.sleep(0.02)


def run_fuzz_campaign(
    *,
    decoders: tuple[str, ...] = DECODERS,
    iterations: int = 10,
    seed: int = 0,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    memory_limit_bytes: int = DEFAULT_MEMORY_LIMIT_MIB * 1024 * 1024,
) -> list[FuzzOutcome]:
    """Generate mutated tiny files and run each decoder in a bounded subprocess."""
    rng = random.Random(seed)
    outcomes: list[FuzzOutcome] = []
    with tempfile.TemporaryDirectory(prefix="localsr-fuzz-") as temp_dir:
        work = Path(temp_dir)
        seeds = build_seed_files(work / "seeds")
        mutated_dir = work / "mutated"
        mutated_dir.mkdir(parents=True, exist_ok=True)
        for decoder in decoders:
            decoder_seeds = seeds[decoder]
            for index in range(iterations):
                source = decoder_seeds[index % len(decoder_seeds)]
                mutated = mutated_dir / f"{decoder}-{index}{source.suffix}"
                mutated.write_bytes(mutate_bytes(source.read_bytes(), rng))
                outcomes.append(
                    run_decoder_subprocess(
                        decoder,
                        mutated,
                        timeout_seconds=timeout_seconds,
                        memory_limit_bytes=memory_limit_bytes,
                    )
                )
    return outcomes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decoders", nargs="+", choices=DECODERS, default=list(DECODERS))
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--memory-mib", type=int, default=DEFAULT_MEMORY_LIMIT_MIB)
    parser.add_argument("--run-one", nargs=2, metavar=("DECODER", "PATH"))
    parser.add_argument(
        "--memory-limit-bytes", type=int, default=DEFAULT_MEMORY_LIMIT_MIB * 1024**2
    )
    args = parser.parse_args(argv)

    if args.run_one is not None:
        return _run_one(args.run_one[0], args.run_one[1], args.memory_limit_bytes)

    outcomes = run_fuzz_campaign(
        decoders=tuple(args.decoders),
        iterations=args.iterations,
        seed=args.seed,
        timeout_seconds=args.timeout,
        memory_limit_bytes=args.memory_mib * 1024 * 1024,
    )
    failures = [item for item in outcomes if not item.ok]
    if failures:
        for item in failures:
            print(
                f"FAIL {item.decoder} ({item.reason}, rc={item.returncode}): {item.detail}",
                file=sys.stderr,
            )
        return 1
    print(f"Fuzzing passed: {len(outcomes)} runs across {args.decoders} with 0 crashes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
