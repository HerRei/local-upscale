#!/usr/bin/env python3
"""Repeatable local wall-clock measurement for the live preview transport.

This intentionally is not a CI pass/fail benchmark.  It loads the trusted
Quick model and alternates production ``process_frame`` runs with preview
disabled/enabled so maintainers can record the median overhead for a concrete
machine and commit.
"""

from __future__ import annotations

import argparse
import json
import statistics
import threading
import time
from pathlib import Path

from localsr.core.benchmark import deterministic_input
from localsr.core.inference import InferenceEngine
from localsr.core.live_preview import LatestPreviewEncoder
from localsr.core.model_adapter import ModelAdapter
from localsr.core.model_catalog import CATALOG_BY_ID, ModelStore


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--model-root", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    model = CATALOG_BY_ID["span_photo_x4"]
    store = ModelStore(args.model_root)
    path = store.path_for(model)
    if not store.is_installed(model):
        raise SystemExit(f"trusted Quick model is not installed at {path}")
    adapter = ModelAdapter()
    info = adapter.inspect(str(path))
    engine = InferenceEngine(adapter)
    engine.load_model(str(path), args.device, "fp32", info)
    tensor = deterministic_input(256, 256)
    cancel = threading.Event()

    def one(enabled: bool) -> tuple[float, int]:
        packets = []
        encoder = LatestPreviewEncoder(packets.append, enabled=enabled, max_fps=2.0)

        def tile(phase, geometry, pixels, completed, total, width, height, _size):
            if phase == "completed" and pixels is not None:
                encoder.submit(
                    job_id="measurement",
                    preview_kind="tile",
                    pixels=pixels,
                    force=completed == total,
                    output_x=geometry.out_x,
                    output_y=geometry.out_y,
                    output_width=geometry.out_w,
                    output_height=geometry.out_h,
                    image_width=width,
                    image_height=height,
                )

        started = time.perf_counter()
        engine.process_frame(
            img_tensor=tensor,
            model_info=info,
            tile_size=128,
            halo=16,
            cancel_event=cancel,
            progress_callback=lambda *_: None,
            safe_memory=False,
            tile_callback=tile,
        )
        elapsed = time.perf_counter() - started
        encoder.close()
        return elapsed, len(packets)

    # Warm the allocator and kernels outside measurements.
    one(False)
    disabled: list[float] = []
    enabled: list[float] = []
    for _ in range(max(3, args.runs)):
        disabled.append(one(False)[0])
        enabled.append(one(True)[0])
    base = statistics.median(disabled)
    live = statistics.median(enabled)
    report = {
        "measurement": "localsr-live-preview-overhead-v1",
        "model": model.model_id,
        "device": args.device,
        "input": [256, 256],
        "tile_size": 128,
        "runs": max(3, args.runs),
        "preview_transport": "bounded async JPEG/Base64 JSON event",
        "preview_max_fps": 2.0,
        "disabled_median_seconds": round(base, 6),
        "enabled_median_seconds": round(live, 6),
        "overhead_percent": round((live / base - 1.0) * 100.0, 3),
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
