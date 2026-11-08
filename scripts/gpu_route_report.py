#!/usr/bin/env python3
"""Measure how a GPU route (DirectML or OpenVINO) compares with PyTorch on the CPU.

For one model, one photo and one device, every comparison uses the same exported graph:
PyTorch on the CPU (the reference), PyTorch with the export rewrites, ONNX Runtime on the
CPU without and with graph optimizations, then each configuration the route can use. A
second part renders the whole photo through LocalSR's engine on the device and on the CPU.
The JSON report records versions, the device, the graph hash and every number, so a run on
one machine can be compared with a run on another. Use it on hardware that has no recorded
result yet (docs/platforms.md) and attach the report.

    python scripts/gpu_route_report.py --model 4xNomos.pth --image photo.jpg \
        --device directml:0 --output report.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import sys
import threading
import time
from pathlib import Path

import numpy as np
import torch


def metrics(candidate: np.ndarray, reference: np.ndarray) -> dict:
    """Differences on the clamped [0, 1] output, in 8-bit steps where that reads better."""
    difference = np.abs(np.clip(candidate, 0, 1) - np.clip(reference, 0, 1))
    mean_square = float(np.mean(difference**2))
    return {
        "psnr": None if mean_square == 0 else round(10 * math.log10(1 / mean_square), 2),
        "max_255": round(float(difference.max()) * 255, 3),
        "p999_255": round(float(np.quantile(difference, 0.999)) * 255, 3),
        "finite": bool(np.isfinite(candidate).all()),
        "raw_range": [round(float(np.nanmin(candidate)), 4), round(float(np.nanmax(candidate)), 4)],
    }


def _tiles(height: int, width: int, side: int) -> list[tuple[int, int]]:
    """Three sample tiles (centre, upper left area, lower right area), inside the image."""
    side = min(side, height, width)
    spots = ((0.5, 0.5), (0.25, 0.25), (0.75, 0.65))
    return [
        (
            min(max(0, int(height * y) - side // 2), height - side),
            min(max(0, int(width * x) - side // 2), width - side),
        )
        for y, x in spots
    ]


def _route(device: str):
    from localsr.core.inference import _onnx_route

    route = _onnx_route(device)
    if route is None:
        raise SystemExit(f"{device} is not a DirectML or OpenVINO device on this machine")
    return route


def chain(descriptor, route, tensor: torch.Tensor, side: int) -> list[dict]:
    import onnxruntime as ort

    from localsr.core.onnx_runtime import ROUTE_CONFIGS, OnnxImageDescriptor, _RawImage

    raw = _RawImage(descriptor).eval()
    probe = OnnxImageDescriptor(descriptor, route=route).model
    _, height, width = tensor.shape
    results = []
    for top, left in _tiles(height, width, side):
        tile = tensor[:, top : top + side, left : left + side].unsqueeze(0).contiguous().float()
        with torch.inference_mode():
            reference = descriptor._call_fn(descriptor.model, tile).float().numpy()
            prepared = raw(tile).float().numpy()
        graph = probe._export(tile)
        entry = {
            "tile": [top, left, side],
            "graph_sha256": hashlib.sha256(graph).hexdigest(),
            "torch_export_rewrites": metrics(prepared, reference),
        }
        for level, name in (
            (ort.GraphOptimizationLevel.ORT_DISABLE_ALL, "ort_cpu_unoptimized"),
            (ort.GraphOptimizationLevel.ORT_ENABLE_ALL, "ort_cpu_optimized"),
        ):
            options = ort.SessionOptions()
            options.graph_optimization_level = level
            session = ort.InferenceSession(graph, options, providers=["CPUExecutionProvider"])
            entry[name] = metrics(session.run(None, {"image": tile.numpy()})[0], reference)
        for config in ROUTE_CONFIGS[route.provider]:
            try:
                started = time.perf_counter()
                session, trace = probe._create(graph, config)
                created = time.perf_counter() - started
                session.run(None, {"image": tile.numpy()})
                started = time.perf_counter()
                output = session.run(None, {"image": tile.numpy()})[0]
                warm = time.perf_counter() - started
                trace.cleanup()
                entry[f"route_{config[0]}"] = {
                    **metrics(output, reference),
                    "session_seconds": round(created, 3),
                    "warm_tile_seconds": round(warm, 4),
                }
            except Exception as error:  # noqa: BLE001 - a failing configuration is a result
                entry[f"route_{config[0]}"] = {"error": f"{type(error).__name__}: {error}"[:300]}
        results.append(entry)
    return results


def whole_image(model_path: str, device: str, tensor: torch.Tensor, tile: int, halo: int) -> dict:
    from localsr.core.inference import InferenceEngine
    from localsr.core.model_adapter import ModelAdapter

    adapter = ModelAdapter(allow_unverified_checkpoints=True)
    info = adapter.inspect(model_path)
    runs = {}
    for label, target in (("route_first", device), ("route_warm", device), ("cpu", "cpu")):
        engine = InferenceEngine(adapter)
        messages: list[str] = []
        engine.status_callback = messages.append
        started = time.perf_counter()
        try:
            writer = engine.process_image(
                {"tensor": tensor.clone()},
                info,
                model_path,
                target,
                "fp32",
                tile,
                halo,
                threading.Event(),
                lambda *arguments: None,
            )
            array = writer.get_array().astype(np.float64) / 255
            writer.cleanup()
            runs[label] = {"seconds": round(time.perf_counter() - started, 2), "messages": messages}
            runs[label]["_array"] = array
        except Exception as error:  # noqa: BLE001
            runs[label] = {
                "seconds": round(time.perf_counter() - started, 2),
                "messages": messages,
                "error": f"{type(error).__name__}: {error}"[:300],
            }
        engine.release_all()
        adapter.release()
    route, cpu = runs["route_warm"].pop("_array", None), runs["cpu"].pop("_array", None)
    runs["route_first"].pop("_array", None)
    if route is not None and cpu is not None:
        runs["route_vs_cpu"] = metrics(route, cpu)
    return runs


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--device", required=True, help="a directml:N or openvino:<id> device")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--tile", type=int, default=256)
    parser.add_argument("--halo", type=int, default=32)
    parser.add_argument(
        "--crop", type=int, default=512, help="side of the photo crop rendered whole"
    )
    args = parser.parse_args()

    import onnxruntime as ort

    from localsr import __version__
    from localsr.core.hardware import get_capability_report
    from localsr.core.image_io import ImageManager
    from localsr.core.model_adapter import ModelAdapter

    route = _route(args.device)
    tensor = ImageManager().load(str(args.image))["tensor"]
    _, height, width = tensor.shape
    side = min(args.crop, height, width)
    top, left = (height - side) // 2, (width - side) // 2
    crop = tensor[:, top : top + side, left : left + side].contiguous()
    adapter = ModelAdapter(allow_unverified_checkpoints=True)
    descriptor, _ = adapter.load(str(args.model), torch.device("cpu"), torch.float32)
    device = next(
        (entry for entry in get_capability_report()["devices"] if entry["id"] == args.device), None
    )
    report = {
        "localsr": __version__,
        "torch": torch.__version__,
        "onnxruntime": ort.__version__,
        "python": platform.python_version(),
        "system": platform.platform(),
        "device": {k: v for k, v in (device or {}).items() if k != "recommended_tile_sizes"},
        "route": {"provider": route.provider, "identity": route.identity, "label": route.label},
        "model": {
            "file": args.model.name,
            "architecture": descriptor.architecture.name,
            "sha256": hashlib.sha256(args.model.read_bytes()).hexdigest(),
        },
        "image": args.image.name,
        "tile": args.tile,
        "halo": args.halo,
        "chain": chain(descriptor, route, tensor, args.tile + 2 * args.halo),
        "whole_image": whole_image(str(args.model), args.device, crop, args.tile, args.halo),
    }
    adapter.release()
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    summary = report["whole_image"].get("route_vs_cpu")
    print(f"Report written to {args.output}; whole image against the CPU: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
