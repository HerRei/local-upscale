"""Measure the Mac photo batch against today's per-photo path on the same photos.

Run on Apple silicon outside any sandbox (Core ML and MPS do not run in one),
from the worktree root, one model at a time:

    PYTHONPATH=$PWD/src .venv/bin/python scripts/benchmark_mac_batch.py \\
        --model "$HOME/Library/Application Support/LocalSR/models/4xNomosUni_span_multijpg.pth" \\
        --scratch /tmp/localsr-batch-span --report span.json

Both paths are the worker's own: every photo goes through ``_run_image_job`` as a
job_request does, or all of them through ``_run_image_batch`` as an
image_batch_request does. Photos are synthetic (the 6 Nov 2026 study's pattern,
1600x1200, FBCNN 4032x3024), never personal ones. Each mode runs once on two
photos first (conversion, gates), untimed; then the modes alternate.

Accuracy: the first photos of every run are compared with PyTorch on MPS in fp32
(the path without Core ML) and with the other mode; the app's gate is 45 dB.

Disk: the scratch folder must be new and is removed at the end, Core ML cache
included. Outputs are deleted as soon as they are saved (the compared ones once
compared). Below ``--min-free-gb`` free the run stops and the report says so.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import shutil
import statistics
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

GATE_DB = 45.0
STUDY_SIZES = {"FBCNN": (4032, 3024)}
DEFAULT_SIZE = (1600, 1200)


def synthetic_photos(directory: Path, count: int, size: tuple[int, int]) -> list[Path]:
    """The study's deterministic gradient, edge and noise photos, as JPEG 92."""
    directory.mkdir(parents=True, exist_ok=True)
    width, height = size
    x = np.arange(width, dtype=np.float32)[None, :] / width
    y = np.arange(height, dtype=np.float32)[:, None] / height
    paths = []
    for index in range(count):
        noise = np.random.default_rng(index + 100).normal(0, 0.025, (height, width))
        noise = noise.astype(np.float32)
        edge = ((x > 0.36 + index * 0.005) & (y > 0.4)).astype(np.float32) * 0.18
        rgb = np.stack(
            np.broadcast_arrays(
                0.15 + 0.6 * x + noise + edge,
                0.2 + 0.55 * y + noise,
                0.2 + 0.28 * x + 0.28 * y + noise - edge,
            ),
            axis=-1,
        )
        path = directory / f"{index:02}.jpg"
        Image.fromarray((np.clip(rgb, 0, 1) * 255).astype("uint8")).save(path, quality=92)
        paths.append(path)
    return paths


def psnr(reference: np.ndarray, candidate: np.ndarray) -> float:
    """PSNR of two exported 8-bit images in dB (inf when identical)."""
    if reference.shape != candidate.shape:
        raise ValueError(f"Output shape {candidate.shape} differs from {reference.shape}")
    error = np.mean((reference.astype(np.float64) - candidate.astype(np.float64)) ** 2)
    return math.inf if error == 0 else 10 * math.log10(255**2 / error)


class DiskGuard:
    """Free space on the scratch folder's volume, with the lowest value seen."""

    def __init__(self, path: Path, minimum_bytes: int):
        self.path, self.minimum = path, minimum_bytes
        self.lowest: int | None = None

    def ok(self) -> bool:
        free = shutil.disk_usage(self.path).free
        self.lowest = free if self.lowest is None else min(self.lowest, free)
        return free >= self.minimum


def summarize(runs: list[dict], gate_db: float = GATE_DB) -> dict:
    modes = sorted({run["mode"] for run in runs}, reverse=True)
    seconds = {
        mode: statistics.median(r["seconds_per_photo"] for r in runs if r["mode"] == mode)
        for mode in modes
    }
    worst = {
        mode: min(
            (value for r in runs if r["mode"] == mode for value in r["psnr_vs_reference_db"]),
            default=math.inf,
        )
        for mode in modes
    }
    return {
        "seconds_per_photo": seconds,
        "speed_up": seconds["serial"] / seconds["batch"]
        if {"serial", "batch"} <= seconds.keys()
        else None,
        "min_psnr_vs_reference_db": worst,
        "accuracy_passes_gate": all(value >= gate_db for value in worst.values()),
    }


def parse_arguments(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--scratch", required=True, type=Path, help="a new folder")
    parser.add_argument("--photos", type=int, default=12)
    parser.add_argument("--size", help="WIDTHxHEIGHT of the synthetic photos")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--compare", type=int, default=2, help="photos checked for accuracy")
    parser.add_argument("--tile-size", type=int, default=256, help="today's tile setting")
    parser.add_argument("--halo", type=int, help="default: the catalog's halo for the model")
    parser.add_argument(
        "--power",
        choices=("auto", "ac", "battery"),
        default="auto",
        help="ac/battery override the power reading for the GPU lane (measurement only)",
    )
    parser.add_argument("--min-free-gb", type=float, default=2.0)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    if args.scratch.exists():
        parser.error(f"{args.scratch} exists; the scratch folder must be new")
    if args.photos < 2 or args.repeat < 1 or not 0 < args.compare <= args.photos:
        parser.error("Use at least two photos, one repeat and 1..photos compared photos.")
    return args


class Bench:
    """Both paths through one worker server, as the desktop drives it."""

    def __init__(self, args, scratch: Path):
        from localsr.core.model_adapter import ModelAdapter
        from localsr.core.model_catalog import CATALOG_BY_FILENAME
        from localsr.worker import image_batch
        from localsr.worker import server as server_module

        self.args, self.scratch = args, scratch
        self.server_module = server_module
        self.server = server_module.WorkerServer()
        self.info = ModelAdapter().inspect(str(args.model))
        catalog = CATALOG_BY_FILENAME.get(args.model.name)
        self.halo = args.halo or (catalog.recommended_halo if catalog else 32)
        self.guard = DiskGuard(scratch, int(args.min_free_gb * 2**30))
        self.model_id = catalog.model_id if catalog else args.model.stem
        self.messages: list = []
        self.keep: set[str] = set()
        self.stopped = False
        self.lane_tile_counts: dict[str, int] = {}
        server_module.send_message = self._receive
        if args.power != "auto":
            forced = args.power == "ac"

            class ForcedPower(image_batch.PowerSource):
                def __init__(self):
                    super().__init__(read=lambda: forced)

            image_batch.PowerSource = ForcedPower
        original = image_batch.ImageBatchRunner.run

        def recording(runner, jobs):
            try:
                return original(runner, jobs)
            finally:
                for unit, count in runner.lane_tile_counts.items():
                    self.lane_tile_counts[unit] = self.lane_tile_counts.get(unit, 0) + count

        image_batch.ImageBatchRunner.run = recording

    def _receive(self, message) -> None:
        self.messages.append(message)
        if type(message).__name__ == "JobCompleted" and message.job_id not in self.keep:
            Path(message.output_path).unlink(missing_ok=True)
        if not self.guard.ok():
            self.stopped = True
            self.server.cancel_event.set()

    def jobs(self, photos: list[Path], folder: Path, *, neural_engine=True) -> list[dict]:
        folder.mkdir(parents=True)
        jobs = []
        for index, photo in enumerate(photos):
            scratch = folder / f".job-{index:02}"
            scratch.mkdir()
            jobs.append(
                {
                    "job_id": f"{folder.name}-{index:02}",
                    "image_path": str(photo),
                    "model_path": str(self.args.model),
                    "output_path": str(folder / f"{index:02}.png"),
                    "scratch_directory": str(scratch),
                    "output_temporary_directory": str(scratch),
                    "output_format": "png",
                    "device": "mps",
                    "tile_size": self.args.tile_size,
                    "halo": self.halo,
                    "precision": "fp32",
                    "jpeg_quality": 98,
                    "preserve_metadata": True,
                    "safe_memory": False,
                    "neural_engine": neural_engine,
                    "preview_enabled": False,
                    "output_scale": self.info.scale,
                    "face_model_path": None,
                    "stages": [
                        {
                            "kind": "restore" if self.info.scale == 1 else "upscale",
                            "model_id": self.model_id,
                            "model_path": str(self.args.model),
                        }
                    ],
                }
            )
        return jobs

    def run(self, mode: str, photos: list[Path], name: str, compare: int) -> dict:
        if self.stopped or not self.guard.ok():
            raise RuntimeError("Free disk space is below the limit; stopped before a run.")
        folder = self.scratch / name
        jobs = self.jobs(photos, folder, neural_engine=mode != "reference")
        self.keep = {job["job_id"] for job in jobs[:compare]}
        self.messages, self.lane_tile_counts = [], {}
        started = time.perf_counter()
        if mode == "batch":
            self.server._run_image_batch(jobs)
        else:
            for job in jobs:
                if self.stopped:
                    break
                self.server._run_image_job(job)
        seconds = time.perf_counter() - started
        names = {type(m).__name__ for m in self.messages}
        completed = {m.job_id for m in self.messages if type(m).__name__ == "JobCompleted"}
        problems = [
            f"{type(m).__name__} {m.job_id}: {getattr(m, 'error_message', '')}"
            for m in self.messages
            if type(m).__name__ in {"JobFailed", "JobCancelled"}
        ]
        if problems or len(completed) != len(jobs):
            low = " (free disk space fell below the limit)" if self.stopped else ""
            raise RuntimeError(f"{mode} did not finish every photo{low}: {problems[:3]}")
        outputs = []
        for job in jobs[:compare]:
            with Image.open(job["output_path"]) as image:
                outputs.append(np.asarray(image.convert("RGB")))
        shutil.rmtree(folder)
        return {
            "mode": mode,
            "photos": len(jobs),
            "seconds": seconds,
            "seconds_per_photo": seconds / len(jobs),
            "lane_tile_counts": dict(self.lane_tile_counts),
            "fell_back_to_normal_jobs": sum(
                1 for m in self.messages if type(m).__name__ == "JobStarted"
            )
            - len(jobs)
            if mode == "batch"
            else 0,
            "messages": sorted(names),
            "outputs": outputs,
        }


def main(argv=None) -> int:
    args = parse_arguments(argv)
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        print("This benchmark needs Apple silicon.", file=sys.stderr)
        return 2
    args.model = args.model.expanduser().resolve(strict=True)
    scratch = args.scratch.expanduser().resolve()
    scratch.mkdir(parents=True)
    os.environ["LOCALSR_COREML_CACHE"] = str(scratch / "coreml")
    os.environ["LOCALSR_WORK_DIR"] = str(scratch / "work")
    from localsr.worker.image_batch import read_ac_power

    report: dict = {
        "model": args.model.name,
        "machine": platform.platform(),
        "on_ac_power": read_ac_power(),
        "power_setting": args.power,
        "load_average_at_start": os.getloadavg(),
        "png_level": "Pillow default (6)",
        "runs": [],
    }
    try:
        bench = Bench(args, scratch)
        size = (
            tuple(int(v) for v in args.size.lower().split("x"))
            if args.size
            else STUDY_SIZES.get(bench.info.architecture, DEFAULT_SIZE)
        )
        report.update(architecture=bench.info.architecture, halo=bench.halo, photo_size=size)
        photos = synthetic_photos(scratch / "photos", args.photos, size)
        compare = args.compare
        print(f"{args.model.name}: {len(photos)} photos {size}, halo {bench.halo}", flush=True)
        reference = bench.run("reference", photos[:compare], "reference", compare)
        report["reference_seconds_per_photo"] = reference["seconds_per_photo"]
        print(f"MPS fp32 reference: {reference['seconds_per_photo']:.2f} s/photo", flush=True)
        report["warmup"] = []
        for mode in ("serial", "batch"):
            warm = bench.run(mode, photos[:2], f"warmup-{mode}", 1)
            warm.pop("outputs")
            report["warmup"].append(warm)
            print(f"Warm-up {mode}: {warm['seconds']:.1f} s", flush=True)
        for repeat in range(args.repeat):
            results = {}
            for mode in ("serial", "batch") if repeat % 2 == 0 else ("batch", "serial"):
                result = bench.run(mode, photos, f"{repeat}-{mode}", compare)
                results[mode] = result
                result["psnr_vs_reference_db"] = [
                    psnr(expected, actual)
                    for expected, actual in zip(
                        reference["outputs"], result["outputs"], strict=True
                    )
                ]
                print(
                    f"Run {repeat + 1} {mode}: {result['seconds_per_photo']:.3f} s/photo, "
                    f"PSNR vs MPS fp32 {min(result['psnr_vs_reference_db']):.1f} dB, "
                    f"lanes {result['lane_tile_counts']}",
                    flush=True,
                )
            results["batch"]["psnr_vs_serial_db"] = [
                psnr(expected, actual)
                for expected, actual in zip(
                    results["serial"]["outputs"], results["batch"]["outputs"], strict=True
                )
            ]
            for result in results.values():
                result.pop("outputs")
                report["runs"].append(result)
        report["summary"] = summarize(report["runs"])
        print(json.dumps(report["summary"], indent=2), flush=True)
        return 0 if report["summary"]["accuracy_passes_gate"] else 1
    except Exception as error:  # noqa: BLE001 - the report says what stopped the run
        report["error"] = f"{type(error).__name__}: {error}"
        print(report["error"], file=sys.stderr, flush=True)
        return 1
    finally:
        report["lowest_free_disk_gb"] = (
            None
            if "bench" not in locals() or bench.guard.lowest is None
            else round(bench.guard.lowest / 2**30, 2)
        )
        import resource

        report["peak_rss_gb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**30, 2)
        text = json.dumps(report, indent=2, default=str, allow_nan=True)
        if args.report:
            args.report.write_text(text + "\n")
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
