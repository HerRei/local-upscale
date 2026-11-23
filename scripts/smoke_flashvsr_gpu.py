#!/usr/bin/env python3
"""Run one synthetic clip through FlashVSREngine on ROCm and report timing and peak VRAM."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np
import torch

from localsr.video_models.flashvsr.engine import FlashVSREngine


def _synthetic_frames(count: int, width: int, height: int) -> list[np.ndarray]:
    yy, xx = np.mgrid[0:height, 0:width]
    frames: list[np.ndarray] = []
    for idx in range(count):
        r = ((xx * 5 + idx * 9) % 256).astype(np.uint8)
        g = ((yy * 7 + idx * 5) % 256).astype(np.uint8)
        b = (((xx + yy) * 3 + idx * 11) % 256).astype(np.uint8)
        frames.append(np.ascontiguousarray(np.stack([r, g, b], axis=-1)))
    return frames


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--width", type=int, default=224, help="Input frame width (4x=896)")
    parser.add_argument("--height", type=int, default=128, help="Input frame height (4x=512)")
    parser.add_argument("--frames", type=int, default=29)
    parser.add_argument("--resolution", type=int, default=0)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    frames = _synthetic_frames(args.frames, args.width, args.height)
    t_load = time.perf_counter()
    engine = FlashVSREngine(str(args.model_dir), device=args.device, precision="bf16")
    if torch.cuda.is_available():
        torch.cuda.synchronize()
    load_seconds = time.perf_counter() - t_load

    try:
        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
            torch.cuda.synchronize()
        t_run = time.perf_counter()
        out = engine.process_frames(frames, resolution=args.resolution, seed=args.seed)
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        run_seconds = time.perf_counter() - t_run
        peak_alloc = torch.cuda.max_memory_allocated() if torch.cuda.is_available() else 0
        peak_reserved = torch.cuda.max_memory_reserved() if torch.cuda.is_available() else 0
    finally:
        engine.close()

    print(
        f"Loaded in {load_seconds:.2f}s | {len(out)} frames @ {out[0].shape[1]}x{out[0].shape[0]} "
        f"in {run_seconds:.2f}s ({len(out) / run_seconds:.2f} FPS) | "
        f"peak_alloc={peak_alloc / 1e9:.3f} GB peak_reserved={peak_reserved / 1e9:.3f} GB"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
