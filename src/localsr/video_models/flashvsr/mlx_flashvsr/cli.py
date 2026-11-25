# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Hermes Reisner. Part of the MLX port of FlashVSR (https://github.com/OpenImagingLab/FlashVSR).

import argparse
import os
import re
import time
from pathlib import Path
from typing import List, Optional, Tuple, Union

import imageio
import mlx.core as mx
import numpy as np
from PIL import Image

from .convert import resolve_dtype
from .pipeline import FlashVSRTinyPipeline

_VIDEO_EXTS = (".mp4", ".mov", ".avi", ".mkv")
_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".PNG", ".JPG", ".JPEG")


def natural_key(name: str):
    """Natural sort key matching examples/WanVSR/infer_flashvsr_v1.1_tiny.py."""
    return [
        int(t) if t.isdigit() else t.lower() for t in re.split(r"([0-9]+)", os.path.basename(name))
    ]


def list_images_natural(folder: str) -> List[str]:
    fs = [os.path.join(folder, f) for f in os.listdir(folder) if f.endswith(_IMAGE_EXTS)]
    fs.sort(key=natural_key)
    return fs


def largest_8n1_leq(n: int) -> int:
    """Largest 8n+1 <= n matching examples/WanVSR/infer_flashvsr_v1.1_tiny.py:31."""
    return 0 if n < 1 else ((n - 1) // 8) * 8 + 1


def is_video(path: str) -> bool:
    return os.path.isfile(path) and path.lower().endswith(_VIDEO_EXTS)


def compute_scaled_and_target_dims(
    w0: int, h0: int, scale: float = 4.0, multiple: int = 128
) -> Tuple[int, int, int, int]:
    """Ported from examples/WanVSR/infer_flashvsr_v1.1_tiny.py:49-67."""
    if w0 <= 0 or h0 <= 0:
        raise ValueError("Invalid original size")
    if scale <= 0:
        raise ValueError("scale must be > 0")

    sW = int(round(w0 * scale))
    sH = int(round(h0 * scale))

    tW = (sW // multiple) * multiple
    tH = (sH // multiple) * multiple

    if tW == 0 or tH == 0:
        raise ValueError(
            f"Scaled size too small ({sW}x{sH}) for multiple={multiple}. "
            f"Increase scale (got {scale})."
        )

    return sW, sH, tW, tH


def upscale_then_center_crop(img: Image.Image, scale: float, tW: int, tH: int) -> Image.Image:
    """Ported from examples/WanVSR/infer_flashvsr_v1.1_tiny.py:70-84."""
    w0, h0 = img.size
    sW = int(round(w0 * scale))
    sH = int(round(h0 * scale))

    if tW > sW or tH > sH:
        raise ValueError(
            f"Target crop ({tW}x{tH}) exceeds scaled size ({sW}x{sH}). Increase scale."
        )

    up = img.resize((sW, sH), Image.BICUBIC)
    left = (sW - tW) // 2
    top = (sH - tH) // 2
    return up.crop((left, top, left + tW, top + tH))


def _pil_to_array_neg1_1(img: Image.Image) -> np.ndarray:
    arr = np.asarray(img, dtype=np.float32)  # HWC
    arr = np.transpose(arr, (2, 0, 1)) / 255.0 * 2.0 - 1.0  # CHW in [-1, 1]
    return arr


def _count_video_frames(reader, meta: dict) -> int:
    nf = meta.get("nframes", None)
    if isinstance(nf, int) and nf > 0:
        return nf
    try:
        cnt = reader.count_frames()
        if isinstance(cnt, int) and cnt > 0:
            return cnt
    except Exception:
        pass
    n = 0
    try:
        while True:
            reader.get_data(n)
            n += 1
    except Exception:
        return n


def prepare_input_tensor(
    path: str,
    scale: float = 4.0,
    dtype: Union[str, mx.Dtype] = "bfloat16",
    max_frames: Optional[int] = None,
) -> Tuple[mx.array, int, int, int, int]:
    """Ported from examples/WanVSR/infer_flashvsr_v1.1_tiny.py:87-172."""
    target_dtype = resolve_dtype(dtype)
    if max_frames is not None and max_frames <= 0:
        raise ValueError(f"--max-frames must be positive, got {max_frames}")

    if os.path.isdir(path):
        paths0 = list_images_natural(path)
        if not paths0:
            raise FileNotFoundError(f"No images in {path}")
        if max_frames is not None:
            paths0 = paths0[:max_frames]

        with Image.open(paths0[0]) as img0:
            w0, h0 = img0.size
        n0 = len(paths0)
        print(
            f"[{os.path.basename(path)}] Original Resolution: {w0}x{h0} | Original Frames: {n0}",
            flush=True,
        )

        sW, sH, tW, tH = compute_scaled_and_target_dims(w0, h0, scale=scale, multiple=128)
        print(
            f"[{os.path.basename(path)}] Scaled (x{scale:.2f}): {sW}x{sH} -> Target (128-multiple): {tW}x{tH}",
            flush=True,
        )

        paths = paths0 + [paths0[-1]] * 4
        F = largest_8n1_leq(len(paths))
        if F == 0:
            raise RuntimeError(f"Not enough frames after padding in {path}. Got {len(paths)}.")
        paths = paths[:F]
        print(
            f"[{os.path.basename(path)}] Target Frames (8n-3): {F - 4} (padded input: {F})",
            flush=True,
        )

        frames = []
        for p in paths:
            with Image.open(p).convert("RGB") as img:
                img_out = upscale_then_center_crop(img, scale=scale, tW=tW, tH=tH)
            frames.append(_pil_to_array_neg1_1(img_out))
        vid_np = np.stack(frames, axis=1)[None, ...]  # (1, 3, F, H, W)
        vid = mx.array(vid_np, dtype=mx.float32).astype(target_dtype)
        mx.eval(vid)
        return vid, tH, tW, F, 30

    if is_video(path):
        rdr = imageio.get_reader(path)
        try:
            first = Image.fromarray(rdr.get_data(0)).convert("RGB")
            w0, h0 = first.size

            meta = {}
            try:
                meta = rdr.get_meta_data()
            except Exception:
                pass
            fps_val = meta.get("fps", 30)
            fps = int(round(fps_val)) if isinstance(fps_val, (int, float)) else 30

            total = _count_video_frames(rdr, meta)
            if total <= 0:
                raise RuntimeError(f"Cannot read frames from {path}")
            if max_frames is not None:
                total = min(total, max_frames)

            print(
                f"[{os.path.basename(path)}] Original Resolution: {w0}x{h0} | Original Frames: {total} | FPS: {fps}",
                flush=True,
            )
            sW, sH, tW, tH = compute_scaled_and_target_dims(w0, h0, scale=scale, multiple=128)
            print(
                f"[{os.path.basename(path)}] Scaled (x{scale:.2f}): {sW}x{sH} -> Target (128-multiple): {tW}x{tH}",
                flush=True,
            )

            idx = list(range(total)) + [total - 1] * 4
            F = largest_8n1_leq(len(idx))
            if F == 0:
                raise RuntimeError(f"Not enough frames after padding in {path}. Got {len(idx)}.")
            idx = idx[:F]
            print(
                f"[{os.path.basename(path)}] Target Frames (8n-3): {F - 4} (padded input: {F})",
                flush=True,
            )

            frames = []
            last_idx = -1
            last_img = None
            for i in idx:
                if i != last_idx:
                    last_img = Image.fromarray(rdr.get_data(i)).convert("RGB")
                    last_idx = i
                img_out = upscale_then_center_crop(last_img, scale=scale, tW=tW, tH=tH)
                frames.append(_pil_to_array_neg1_1(img_out))
        finally:
            try:
                rdr.close()
            except Exception:
                pass

        vid_np = np.stack(frames, axis=1)[None, ...]  # (1, 3, F, H, W)
        vid = mx.array(vid_np, dtype=mx.float32).astype(target_dtype)
        mx.eval(vid)
        return vid, tH, tW, F, fps

    raise ValueError(f"Unsupported input: {path}")


def tensor2video(frames: mx.array) -> np.ndarray:
    """Convert (3, T, H, W) tensor in [-1, 1] to uint8 (T, H, W, 3) array."""
    mx.eval(frames)
    arr = np.asarray(frames.astype(mx.float32))
    arr = np.transpose(arr, (1, 2, 3, 0))  # T, H, W, C
    return np.clip((arr + 1.0) * 127.5, 0.0, 255.0).astype(np.uint8)


def save_video(
    frames_uint8: np.ndarray, save_path: Union[str, os.PathLike], fps: int = 30, quality: int = 6
):
    out_path = Path(save_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    writer = imageio.get_writer(str(out_path), fps=fps, quality=quality)
    try:
        for idx, frame in enumerate(frames_uint8):
            writer.append_data(frame)
            if (idx + 1) % 16 == 0 or (idx + 1) == len(frames_uint8):
                print(f"Writing video: {idx + 1}/{len(frames_uint8)} frames", flush=True)
    finally:
        writer.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="FlashVSR v1.1 Tiny MLX video super-resolution CLI"
    )
    parser.add_argument("input", help="Input video file or image folder")
    parser.add_argument("output", help="Output MP4 path")
    parser.add_argument(
        "--model-dir",
        default="models/FlashVSR-v1.1",
        help="Path to FlashVSR-v1.1 weights directory (default: models/FlashVSR-v1.1)",
    )
    parser.add_argument(
        "--dtype",
        choices=("bfloat16", "float16"),
        default="bfloat16",
        help="Compute and weight dtype (default: bfloat16)",
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        default=None,
        help="Optional maximum number of input frames to read",
    )
    parser.add_argument(
        "--scale",
        type=float,
        default=4.0,
        help="Spatial scale factor before 128-multiple center crop (default: 4.0)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=0,
        help="Random seed for initial latent noise (default: 0)",
    )
    parser.add_argument(
        "--sparse-ratio",
        type=float,
        default=2.0,
        help="Sparse attention ratio (default: 2.0)",
    )
    parser.add_argument(
        "--kv-ratio",
        type=float,
        default=3.0,
        help="Streaming KV cache window ratio (default: 3.0)",
    )
    parser.add_argument(
        "--local-range",
        type=int,
        default=11,
        help="Local attention window range (default: 11)",
    )
    parser.add_argument(
        "--no-color-fix",
        action="store_true",
        help="Disable color correction",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    t_total0 = time.perf_counter()
    print(f"Loading FlashVSR-v1.1 ({args.dtype}) from {args.model_dir}...", flush=True)
    t_load0 = time.perf_counter()
    pipe = FlashVSRTinyPipeline.from_pretrained(args.model_dir, dtype=args.dtype)
    load_s = time.perf_counter() - t_load0
    print(f"Loaded model in {load_s:.2f}s", flush=True)

    lq_video, th, tw, num_frames, fps = prepare_input_tensor(
        args.input,
        scale=args.scale,
        dtype=args.dtype,
        max_frames=args.max_frames,
    )

    def _on_step(step: int, total_steps: int):
        print(f"Denoising step {step}/{total_steps}", flush=True)

    print(f"Running pipeline ({tw}x{th}, num_frames={num_frames})...", flush=True)
    t_infer0 = time.perf_counter()
    out_tensor = pipe(
        height=th,
        width=tw,
        num_frames=num_frames,
        seed=args.seed,
        LQ_video=lq_video,
        is_full_block=False,
        if_buffer=True,
        topk_ratio=args.sparse_ratio * 768 * 1280 / (th * tw),
        kv_ratio=args.kv_ratio,
        local_range=args.local_range,
        color_fix=not args.no_color_fix,
        progress_callback=_on_step,
    )
    mx.eval(out_tensor)
    infer_s = time.perf_counter() - t_infer0

    out_frames = tensor2video(out_tensor)
    save_video(out_frames, args.output, fps=fps, quality=6)

    total_s = time.perf_counter() - t_total0
    n_out = int(out_frames.shape[0])
    infer_fps = n_out / infer_s if infer_s > 0 else 0.0
    total_fps = n_out / total_s if total_s > 0 else 0.0
    print(
        f"Done: {n_out} frames -> {args.output} | "
        f"infer={infer_s:.2f}s ({infer_fps:.2f} fps) | "
        f"total={total_s:.2f}s ({total_fps:.2f} fps)",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
