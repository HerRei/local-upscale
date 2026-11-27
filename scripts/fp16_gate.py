#!/usr/bin/env python3
"""Measure MPS FP16 vs FP32 accuracy and speed for installed catalog image models."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import platform
import subprocess
import threading
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageDraw

from localsr.core.inference import InferenceEngine
from localsr.core.model_adapter import ModelAdapter

PASS_PSNR_DB = 45.0
PASS_SPEEDUP = 1.15
# PSNR averages a few badly wrong pixels away; no output value may move further than this.
PASS_MAX_ABS_ERROR = 16
IMAGE_SIDE = 512
TILED_TILE_SIZE = 256
TILED_HALO = 32
WARMUP_PASSES = 1
TIMED_PASSES = 3

DEFAULT_MODELS_DIR = Path.home() / "Library" / "Application Support" / "LocalSR" / "models"
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "src" / "localsr" / "core" / "fp16_gate.json"
CHECKPOINT_SUFFIXES = {".pth", ".pt", ".ckpt", ".safetensors"}


class _GateAdapter(ModelAdapter):
    """Force fp16 weights even when Spandrel marks supports_half=False so the gate measures it."""

    def load(
        self,
        path: str,
        device: torch.device,
        precision: torch.dtype,
        *,
        onnx_route=None,
        coreml_units: tuple[str, ...] = (),
    ):
        del onnx_route, coreml_units
        normalized_path = str(Path(path).resolve())
        if self.parsed is None or self.parsed_path != normalized_path:
            _, self.parsed = self._read(normalized_path)
            self.parsed_path = normalized_path
        descriptor = self.parsed.to(device)
        descriptor.model.to(precision)
        descriptor.eval()
        return descriptor, self.parsed


def build_test_images() -> dict[str, torch.Tensor]:
    """Return 6 deterministic CHW float32 [0, 1] images covering distinct signal regimes."""
    height, width = IMAGE_SIDE, IMAGE_SIDE
    x = np.linspace(0.0, 1.0, width, dtype=np.float32)[None, :]
    y = np.linspace(0.0, 1.0, height, dtype=np.float32)[:, None]
    rng = np.random.default_rng(42)

    images: dict[str, np.ndarray] = {}

    photo_base = np.stack(
        np.broadcast_arrays(
            0.20 + 0.50 * x + 0.10 * np.sin(10.0 * y),
            0.30 + 0.40 * y,
            0.25 + 0.30 * x + 0.20 * y,
        ),
        axis=0,
    )
    images["photo_noise"] = np.clip(
        photo_base + rng.normal(0.0, 0.04, (3, height, width)).astype(np.float32),
        0.0,
        1.0,
    )

    images["flat_gradient"] = np.clip(
        np.stack(
            np.broadcast_arrays(0.05 + 0.90 * x, 0.05 + 0.90 * y, 0.50 + 0.40 * (x - y)),
            axis=0,
        ),
        0.0,
        1.0,
    )

    text_img = Image.new("RGB", (width, height), (235, 232, 226))
    draw = ImageDraw.Draw(text_img)
    for row in range(12, height - 16, 24):
        draw.text((16, row), "LocalSR FP16 gate 0123456789 ABCxyz", fill=(24, 26, 30))
    images["text"] = np.asarray(text_img, dtype=np.float32).transpose(2, 0, 1) / 255.0

    # Anti-aliased high-contrast geometry avoids binary step spikes that exceed SPAN's raw contract in FP32.
    hi_img = Image.new("RGB", (width * 2, height * 2), (240, 240, 240))
    hi_draw = ImageDraw.Draw(hi_img)
    for col in range(0, width * 2, 96):
        hi_draw.rectangle([col, 0, col + 40, height * 2], fill=(15, 15, 15))
    for row in range(0, height * 2, 128):
        hi_draw.line([(0, row), (width * 2, row + 240)], fill=(220, 30, 30), width=10)
    hi_draw.ellipse([160, 160, 840, 840], outline=(20, 60, 220), width=14)
    images["high_contrast"] = (
        np.asarray(
            hi_img.resize((width, height), Image.Resampling.BILINEAR), dtype=np.float32
        ).transpose(2, 0, 1)
        / 255.0
    )

    images["highlights_shadows"] = np.clip(
        np.stack(
            np.broadcast_arrays(
                0.85 + 0.15 * np.cos(12.0 * x),
                0.10 + 0.80 * (y**2),
                0.02 + 0.20 * x * y,
            ),
            axis=0,
        ),
        0.0,
        1.0,
    )

    stripes = (0.50 + 0.45 * np.sin(2.0 * np.pi * (x * 40.0 + y * 15.0))).astype(np.float32)
    images["fine_texture"] = np.stack(
        [stripes, np.roll(stripes, 2, axis=1), 1.0 - stripes * 0.70],
        axis=0,
    )

    return {name: torch.from_numpy(arr.copy()) for name, arr in images.items()}


def _psnr_uint8(reference: np.ndarray, candidate: np.ndarray) -> float:
    mse = float(np.mean((reference.astype(np.float64) - candidate.astype(np.float64)) ** 2))
    return math.inf if mse == 0.0 else 10.0 * math.log10((255.0**2) / mse)


def _chip_name() -> str:
    try:
        out = subprocess.check_output(
            ["sysctl", "-n", "machdep.cpu.brand_string"], text=True, stderr=subprocess.DEVNULL
        ).strip()
        if out:
            return out
    except (OSError, subprocess.SubprocessError):
        pass
    return platform.machine() or "unknown"


def _discover_models(models_dir: Path) -> list[Path]:
    if not models_dir.is_dir():
        raise FileNotFoundError(f"Models directory does not exist: {models_dir}")
    models = [
        path
        for path in sorted(models_dir.iterdir(), key=lambda p: p.name.lower())
        if path.is_file() and path.suffix.lower() in CHECKPOINT_SUFFIXES
    ]
    if not models:
        raise FileNotFoundError(f"No image checkpoints found in {models_dir}")
    return models


def _run_mode(
    engine: InferenceEngine,
    model_path: str,
    model_info,
    tensor: torch.Tensor,
    precision_str: str,
    *,
    tile_size: int,
    halo: int,
) -> np.ndarray:
    writer = engine.process_image(
        {"tensor": tensor},
        model_info,
        model_path,
        "mps",
        precision_str,
        tile_size,
        halo,
        threading.Event(),
        lambda *_args: None,
        safe_memory=False,
    )
    try:
        return np.array(writer.get_array(), copy=True)
    finally:
        writer.cleanup()


def _benchmark_timing(
    engine: InferenceEngine,
    model_path: str,
    model_info,
    images: list[torch.Tensor],
    precision_str: str,
) -> float:
    # Load once and time in-memory frame inference (whole-frame and tiled) without disk memmaps.
    engine.load_model(model_path, "mps", precision_str, model_info, safe_memory=False)
    cancel = threading.Event()
    noop = lambda *_args: None  # noqa: E731
    for mode_tile, mode_halo in ((max(images[0].shape[-2:]), 0), (TILED_TILE_SIZE, TILED_HALO)):
        for _ in range(WARMUP_PASSES):
            out = engine.process_frame(
                images[0], model_info, mode_tile, mode_halo, cancel, noop, safe_memory=False
            )
            del out
        torch.mps.synchronize()

    elapsed_runs: list[float] = []
    for _ in range(TIMED_PASSES):
        torch.mps.synchronize()
        started = time.perf_counter()
        for tensor in images:
            for mode_tile, mode_halo in (
                (max(tensor.shape[-2:]), 0),
                (TILED_TILE_SIZE, TILED_HALO),
            ):
                out = engine.process_frame(
                    tensor, model_info, mode_tile, mode_halo, cancel, noop, safe_memory=False
                )
                del out
        torch.mps.synchronize()
        elapsed_runs.append(time.perf_counter() - started)
    return float(np.median(elapsed_runs))


def measure_model(model_path: Path, images: dict[str, torch.Tensor]) -> dict:
    adapter = _GateAdapter(allow_unverified_checkpoints=True)
    engine = InferenceEngine(adapter)
    engine.neural_engine_enabled = False
    info = adapter.inspect(str(model_path))
    forced_info = replace(info, half_supported=True)

    min_psnr = math.inf
    max_abs_err = 0
    nan_inf = False

    engine.release_all()
    fp32_outputs: dict[tuple[str, str], np.ndarray] = {}
    for img_name, tensor in images.items():
        for mode_name, tile_size, halo in (
            ("whole", max(tensor.shape[-2:]), 0),
            ("tiled", TILED_TILE_SIZE, TILED_HALO),
        ):
            fp32_outputs[(img_name, mode_name)] = _run_mode(
                engine,
                str(model_path),
                info,
                tensor,
                "fp32",
                tile_size=tile_size,
                halo=halo,
            )

    engine.release_all()
    for img_name, tensor in images.items():
        for mode_name, tile_size, halo in (
            ("whole", max(tensor.shape[-2:]), 0),
            ("tiled", TILED_TILE_SIZE, TILED_HALO),
        ):
            try:
                out16 = _run_mode(
                    engine,
                    str(model_path),
                    forced_info,
                    tensor,
                    "fp16",
                    tile_size=tile_size,
                    halo=halo,
                )
            except ValueError as error:
                if "NaN or Infinity" in str(error):
                    nan_inf = True
                    continue
                raise
            out32 = fp32_outputs[(img_name, mode_name)]
            psnr = _psnr_uint8(out32, out16)
            diff = int(np.max(np.abs(out32.astype(np.int32) - out16.astype(np.int32))))
            min_psnr = min(min_psnr, psnr)
            max_abs_err = max(max_abs_err, diff)

    timing_tensors = list(images.values())
    engine.release_all()
    if nan_inf:
        fp32_s = 0.0
        speedup = 0.0
    else:
        fp32_s = _benchmark_timing(engine, str(model_path), info, timing_tensors, "fp32")
        engine.release_all()
        try:
            fp16_s = _benchmark_timing(engine, str(model_path), forced_info, timing_tensors, "fp16")
            speedup = fp32_s / fp16_s if fp16_s > 0 else 0.0
        except ValueError as error:
            if "NaN or Infinity" in str(error):
                nan_inf = True
                speedup = 0.0
            else:
                raise
    engine.release_all()

    passed = (
        (not nan_inf)
        and min_psnr >= PASS_PSNR_DB
        and max_abs_err <= PASS_MAX_ABS_ERROR
        and speedup >= PASS_SPEEDUP
    )
    return {
        "model": model_path.name,
        "architecture": info.architecture,
        "half_supported": info.half_supported,
        "passed": passed,
        "min_psnr": None if nan_inf or math.isinf(min_psnr) else round(min_psnr, 2),
        "max_abs_error": max_abs_err,
        "nan_inf": nan_inf,
        "speedup": round(speedup, 3),
        "fp32_seconds": round(fp32_s, 3),
    }


def aggregate_by_architecture(results: list[dict], measured_on: str, today: str) -> dict[str, dict]:
    grouped: dict[str, list[dict]] = {}
    for entry in results:
        grouped.setdefault(entry["architecture"], []).append(entry)

    gate: dict[str, dict] = {}
    for arch, entries in sorted(grouped.items()):
        nan_inf = any(item["nan_inf"] for item in entries)
        psnr_values = [item["min_psnr"] for item in entries if item["min_psnr"] is not None]
        min_psnr = None if (nan_inf or not psnr_values) else min(psnr_values)
        max_abs_error = max(item["max_abs_error"] for item in entries)
        min_speedup = min(item["speedup"] for item in entries)
        passed = all(item["passed"] for item in entries)
        gate[arch] = {
            "passed": passed,
            "min_psnr": min_psnr,
            "max_abs_error": max_abs_error,
            "nan_inf": nan_inf,
            "speedup": min_speedup,
            "measured_on": measured_on,
            "date": today,
        }
    return gate


def print_table(results: list[dict], gate: dict[str, dict]) -> None:
    print("\nPer-model measurements (MPS, Neural Engine off, 6 images x whole + tiled):")
    print(
        f"{'Model':42} {'Arch':8} {'Half':5} {'PSNR(dB)':9} {'MaxErr':7} {'NaN/Inf':8} {'Speedup':8} {'Pass'}"
    )
    for row in results:
        psnr_text = "NaN/Inf" if row["min_psnr"] is None else f"{row['min_psnr']:.2f}"
        print(
            f"{row['model']:42} {row['architecture']:8} {str(row['half_supported']):5} "
            f"{psnr_text:9} {row['max_abs_error']:<7} {str(row['nan_inf']):8} "
            f"{row['speedup']:<8.3f} {row['passed']}"
        )

    print("\nPer-architecture gate:")
    print(f"{'Arch':10} {'Passed':7} {'MinPSNR':9} {'MaxErr':7} {'NaN/Inf':8} {'Speedup':8}")
    for arch, entry in gate.items():
        psnr_text = "NaN/Inf" if entry["min_psnr"] is None else f"{entry['min_psnr']:.2f}"
        print(
            f"{arch:10} {str(entry['passed']):7} {psnr_text:9} "
            f"{entry['max_abs_error']:<7} {str(entry['nan_inf']):8} {entry['speedup']:<8.3f}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models-dir", type=Path, default=DEFAULT_MODELS_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    if not torch.backends.mps.is_available():
        raise RuntimeError("MPS is required to run the Apple GPU FP16 gate.")

    models = _discover_models(args.models_dir)
    images = build_test_images()
    measured_on = _chip_name()
    today = dt.date.today().isoformat()

    results = [measure_model(path, images) for path in models]
    gate = aggregate_by_architecture(results, measured_on=measured_on, today=today)
    args.output.write_text(json.dumps(gate, indent=2) + "\n", encoding="utf-8")
    print_table(results, gate)
    print(f"\nWrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
