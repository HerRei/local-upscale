"""LocalSR adapter around the vendored MLX FlashVSR v1.1 Tiny video upscaler.

Drives the streaming MLX FlashVSRTinyPipeline over one independent window of
context + new frames at 4x fixed scale, with 128-multiple spatial edge padding,
8n+1 temporal padding, analytic unified-memory tile planning, and linear feather
blending across 128 px tile overlaps.
"""

from __future__ import annotations

import gc
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from localsr.core.hardware import _mps_memory, _system_memory

OUTPUT_SCALE = 4
SPATIAL_MULTIPLE = 128
MAX_TILE_SIDE = 1408
MIN_TILE_SIDE = 512
TILE_OVERLAP = 128
BUDGET_FRACTION = 0.85

# FlashVSR outputs 8n - 3 frames when fed 8n + 1 frames (n >= 3).
# Window 37 + 4 repeated tail frames = 41 = 8*5 + 1, yielding 37 outputs with
# zero wasted streaming steps; overlap 8 gives 29 new frames per window.
DEFAULT_TEMPORAL_WINDOW = 37
DEFAULT_TEMPORAL_OVERLAP = 8
MAX_TEMPORAL_WINDOW = 61

# Memory model: budget = 0.85 * mps_recommended_working_set;
# max_tile_area = (budget - FIXED_MEMORY_BYTES) / PER_PIXEL_MEMORY_BYTES.
# Fitted from two bfloat16 measurements on Apple M1 Pro (16 GB, 25 frames, mx.get_peak_memory):
#   512x256 (131,072 px): peak_mlx =  7,076,796,368 B (footprint 11,303,308,336 B, 0 swaps)
#   896x512 (458,752 px): peak_mlx = 11,235,842,640 B (footprint 12,770,265,560 B, 0 swaps)
FIXED_MEMORY_BYTES = 5_413_178_122
PER_PIXEL_MEMORY_BYTES = 12_692.4


@dataclass(frozen=True)
class FlashVSRTile:
    """One output-space tile (multiple of 128 px) and its 2D blend weights."""

    x: int
    y: int
    width: int
    height: int
    weights: np.ndarray


@dataclass(frozen=True)
class FlashVSRTilePlan:
    """Spatial tiling and 128-multiple padding plan for an output frame."""

    out_width: int
    out_height: int
    padded_width: int
    padded_height: int
    tile_width: int
    tile_height: int
    budget_bytes: int
    max_tile_area: int
    tiles: tuple[FlashVSRTile, ...]


def ceil_multiple(value: int, multiple: int = SPATIAL_MULTIPLE) -> int:
    if value <= 0:
        raise ValueError(f"Dimension must be positive, got {value}")
    return ((value + multiple - 1) // multiple) * multiple


def padded_frame_count(n_frames: int) -> int:
    """Smallest 8n + 1 (n >= 3, >= 25) whose decoded output covers n_frames.

    FlashVSRTinyPipeline produces F - 4 output frames when given F = 8n + 1
    input frames, so F must satisfy F >= max(25, n_frames + 4).
    """
    if n_frames <= 0:
        raise ValueError(f"frames must be non-empty, got {n_frames}")
    n = max(3, (n_frames + 10) // 8)
    return 8 * n + 1


def mps_recommended_working_set_bytes() -> int:
    """Return Apple Silicon recommended GPU working set using hardware.py."""
    total_ram, available_ram = _system_memory()
    recommended, _free = _mps_memory(total_ram, available_ram)
    return int(recommended)


def compute_axis_starts(length: int, tile_len: int, overlap: int = TILE_OVERLAP) -> list[int]:
    """Return 1D tile start offsets covering [0, length) with uniform tile_len.

    Edge tiles are shifted inward so every tile has exact width tile_len, and
    adjacent tiles overlap by at least `overlap` pixels when tiled.
    """
    if tile_len >= length:
        return [0]
    stride = tile_len - overlap
    if stride <= 0:
        raise ValueError(f"tile_len ({tile_len}) must exceed overlap ({overlap})")
    starts: list[int] = []
    pos = 0
    while True:
        if pos + tile_len >= length:
            starts.append(length - tile_len)
            break
        starts.append(pos)
        pos += stride
    return starts


def _axis_feather_weights(starts: Sequence[int], tile_len: int) -> list[np.ndarray]:
    """Compute 1D linear feather weights per tile along one axis so weights sum to 1."""
    count = len(starts)
    weights: list[np.ndarray] = []
    for idx in range(count):
        w = np.ones(tile_len, dtype=np.float32)
        if idx > 0:
            ov_left = (starts[idx - 1] + tile_len) - starts[idx]
            if ov_left > 0:
                w[:ov_left] = (np.arange(ov_left, dtype=np.float32) + 0.5) / float(ov_left)
        if idx < count - 1:
            ov_right = (starts[idx] + tile_len) - starts[idx + 1]
            if ov_right > 0:
                w[tile_len - ov_right :] = 1.0 - (
                    (np.arange(ov_right, dtype=np.float32) + 0.5) / float(ov_right)
                )
        weights.append(w)
    return weights


def _choose_tile_dimensions(
    pad_w: int,
    pad_h: int,
    max_area: int,
    max_tile_side: int,
) -> tuple[int, int]:
    """Select uniform tile dimensions (tw, th) in multiples of 128 px."""
    if pad_w <= max_tile_side and pad_h <= max_tile_side and pad_w * pad_h <= max_area:
        return pad_w, pad_h

    min_w = MIN_TILE_SIDE if pad_w >= MIN_TILE_SIDE else pad_w
    min_h = MIN_TILE_SIDE if pad_h >= MIN_TILE_SIDE else pad_h
    max_w = min(max_tile_side, pad_w)
    max_h = min(max_tile_side, pad_h)

    widths = list(range(min_w, max_w + 1, SPATIAL_MULTIPLE)) if max_w >= min_w else [pad_w]
    heights = list(range(min_h, max_h + 1, SPATIAL_MULTIPLE)) if max_h >= min_h else [pad_h]

    best_score: tuple[int, int, int, int] | None = None
    best_dims: tuple[int, int] | None = None
    for tw in widths:
        for th in heights:
            if tw * th > max_area:
                continue
            xs = compute_axis_starts(pad_w, tw, TILE_OVERLAP)
            ys = compute_axis_starts(pad_h, th, TILE_OVERLAP)
            nx, ny = len(xs), len(ys)
            score = (nx * ny, min(nx, ny), abs(tw - th), -(tw * th))
            if best_score is None or score < best_score:
                best_score = score
                best_dims = (tw, th)

    if best_dims is None:
        raise RuntimeError(
            "Not enough Apple Silicon unified memory for FlashVSR: no valid tile size fits "
            "within the available memory budget. Close other applications or choose "
            "Frame-by-frame video."
        )
    return best_dims


def plan_tiles(
    out_width: int,
    out_height: int,
    *,
    budget_bytes: int | None = None,
    max_tile_side: int = MAX_TILE_SIDE,
    tile_size_override: tuple[int, int] | None = None,
) -> FlashVSRTilePlan:
    """Plan 128-aligned output tiles and feather weights from the MPS memory budget."""
    pad_w = ceil_multiple(out_width, SPATIAL_MULTIPLE)
    pad_h = ceil_multiple(out_height, SPATIAL_MULTIPLE)

    if budget_bytes is None:
        budget_bytes = int(mps_recommended_working_set_bytes() * BUDGET_FRACTION)
    max_area = int((budget_bytes - FIXED_MEMORY_BYTES) / PER_PIXEL_MEMORY_BYTES)
    if tile_size_override is None and max_area < MIN_TILE_SIDE * MIN_TILE_SIDE:
        raise RuntimeError(
            "Not enough Apple Silicon unified memory for FlashVSR: the memory budget leaves "
            "less than a 512x512 output tile, which would swap heavily. Close other "
            "applications or choose Frame-by-frame video."
        )

    if tile_size_override is not None:
        tw, th = tile_size_override
        if tw <= 0 or th <= 0 or tw % SPATIAL_MULTIPLE != 0 or th % SPATIAL_MULTIPLE != 0:
            raise ValueError(f"Tile size must be positive multiples of 128, got {(tw, th)}")
    else:
        tw, th = _choose_tile_dimensions(pad_w, pad_h, max_area, max_tile_side)

    xs = compute_axis_starts(pad_w, tw, TILE_OVERLAP)
    ys = compute_axis_starts(pad_h, th, TILE_OVERLAP)
    wx_list = _axis_feather_weights(xs, tw)
    wy_list = _axis_feather_weights(ys, th)

    tiles: list[FlashVSRTile] = []
    for row_idx, y in enumerate(ys):
        wy = wy_list[row_idx]
        for col_idx, x in enumerate(xs):
            wx = wx_list[col_idx]
            weights = np.outer(wy, wx).astype(np.float32)
            tiles.append(FlashVSRTile(x=x, y=y, width=tw, height=th, weights=weights))

    return FlashVSRTilePlan(
        out_width=out_width,
        out_height=out_height,
        padded_width=pad_w,
        padded_height=pad_h,
        tile_width=tw,
        tile_height=th,
        budget_bytes=budget_bytes,
        max_tile_area=max(0, max_area),
        tiles=tuple(tiles),
    )


def upscale_and_pad_frames(
    frames: Sequence[np.ndarray],
    pad_width: int,
    pad_height: int,
    *,
    resample: Image.Resampling = Image.Resampling.BICUBIC,
) -> np.ndarray:
    """Upscale RGB uint8 frames by 4x and edge-replicate pad to (pad_height, pad_width)."""
    in_h, in_w = frames[0].shape[:2]
    out_w = in_w * OUTPUT_SCALE
    out_h = in_h * OUTPUT_SCALE
    pad_right = pad_width - out_w
    pad_bottom = pad_height - out_h

    stacked = np.empty((len(frames), pad_height, pad_width, 3), dtype=np.uint8)
    for idx, frame in enumerate(frames):
        if frame.shape != (in_h, in_w, 3):
            raise ValueError(
                f"Expected frame shape {(in_h, in_w, 3)}, got {frame.shape} at index {idx}"
            )
        up = np.asarray(Image.fromarray(frame).resize((out_w, out_h), resample), dtype=np.uint8)
        if pad_right > 0 or pad_bottom > 0:
            up = np.pad(up, ((0, pad_bottom), (0, pad_right), (0, 0)), mode="edge")
        stacked[idx] = up
    return stacked


class FlashVSREngine:
    """One job's worth of MLX FlashVSR v1.1 Tiny state and tiled frame execution."""

    fixed_scale = OUTPUT_SCALE
    default_temporal_window = DEFAULT_TEMPORAL_WINDOW
    default_temporal_overlap = DEFAULT_TEMPORAL_OVERLAP

    def __init__(
        self,
        bundle_dir: str,
        device: str = "mps",
        precision: str = "bf16",
    ) -> None:
        del precision  # FlashVSR v1.1 MLX weights and Metal kernel run in bfloat16.
        backend = str(device).split(":", 1)[0]
        if backend not in {"mps", "auto"}:
            raise ValueError(
                "FlashVSR runs on Apple Silicon GPU (mps) only. Choose Frame-by-frame video "
                "for CPU or other backends."
            )
        self.bundle_dir = Path(bundle_dir)
        self.device = "mps"
        self.max_temporal_window = MAX_TEMPORAL_WINDOW
        self._budget_bytes_override: int | None = None
        self._tile_size_override: tuple[int, int] | None = None
        self._upscale_resample: Image.Resampling = Image.Resampling.BICUBIC

        import mlx.core as mx

        from .mlx_flashvsr.pipeline import FlashVSRTinyPipeline

        self.pipeline: Any = FlashVSRTinyPipeline.from_pretrained(
            self.bundle_dir,
            dtype="bfloat16",
        )
        # Materialize every weight and precomputed tensor on the constructing
        # thread so WorkerServer's bounded_preparation helper thread never
        # leaves lazy MLX ops bound to a thread-local stream.
        to_eval: list[Any] = [self.pipeline.dit.parameters(), self.pipeline.dit.freqs]
        if self.pipeline.TCDecoder is not None:
            to_eval.append(self.pipeline.TCDecoder.parameters())
        if self.pipeline.prompt_emb_posi is not None:
            to_eval.append(self.pipeline.prompt_emb_posi)
        mx.eval(*to_eval)

    def release(self) -> None:
        """Free loaded MLX weights and clear the Metal allocator cache."""
        pipe = getattr(self, "pipeline", None)
        if pipe is not None:
            pipe.dit = None
            pipe.TCDecoder = None
            pipe.ColorCorrector = None
            pipe.prompt_emb_posi = None
            pipe.t = None
            pipe.t_mod = None
            self.pipeline = None
        gc.collect()
        try:
            import mlx.core as mx

            mx.clear_cache()
        except ImportError:
            pass

    def close(self) -> None:
        self.release()

    def _run_tile(
        self,
        lq_tile_uint8: np.ndarray,
        n_in: int,
        n_pad: int,
        tile: FlashVSRTile,
        seed: int,
        step_callback: Callable[[int, int], None],
    ) -> np.ndarray:
        """Run FlashVSRTinyPipeline on one (n_in, tile_h, tile_w, 3) uint8 tile."""
        if n_pad > n_in:
            pad_tail = np.repeat(lq_tile_uint8[-1:], n_pad - n_in, axis=0)
            tile_frames = np.concatenate([lq_tile_uint8, pad_tail], axis=0)
        else:
            tile_frames = lq_tile_uint8

        # (T, H, W, 3) uint8 -> (1, 3, T, H, W) float32 in [-1, 1]
        lq_np = (np.transpose(tile_frames, (3, 0, 1, 2)).astype(np.float32) / 127.5 - 1.0)[
            None, ...
        ]
        topk_ratio = 2.0 * 768.0 * 1280.0 / float(tile.height * tile.width)

        try:
            import mlx.core as mx

            has_mlx = True
        except ImportError:
            has_mlx = False

        if has_mlx and not getattr(self, "_numpy_stub_arrays", False):
            lq_arr: Any = mx.array(lq_np, dtype=mx.float32).astype(mx.bfloat16)
            mx.eval(lq_arr)
        else:
            lq_arr = lq_np

        out_tensor = self.pipeline(
            height=tile.height,
            width=tile.width,
            num_frames=n_pad,
            seed=seed,
            LQ_video=lq_arr,
            is_full_block=False,
            if_buffer=True,
            topk_ratio=topk_ratio,
            kv_ratio=3.0,
            local_range=11,
            color_fix=True,
            progress_callback=step_callback,
        )

        if isinstance(out_tensor, np.ndarray):
            arr = out_tensor[:, :n_in, :, :].astype(np.float32)
        else:
            import mlx.core as mx

            sliced = out_tensor[:, :n_in, :, :]
            mx.eval(sliced)
            arr = np.asarray(sliced.astype(mx.float32))
            del out_tensor, sliced, lq_arr
            mx.clear_cache()

        # (3, T, H, W) in [-1, 1] -> (T, H, W, 3) in [0, 255]
        arr = np.transpose(arr, (1, 2, 3, 0))
        return np.clip((arr + 1.0) * 127.5, 0.0, 255.0)

    def process_frames(
        self,
        frames: list[np.ndarray],
        *,
        resolution: int = 0,
        batch_size: int = DEFAULT_TEMPORAL_WINDOW,
        temporal_overlap: int = DEFAULT_TEMPORAL_OVERLAP,
        seed: int = 42,
        cancel_event: threading.Event | None = None,
        progress_callback: Callable[[str, int, int], None] | None = None,
        tile_callback: Callable[..., None] | None = None,
    ) -> list[np.ndarray]:
        """Upscale a temporal window of RGB uint8 frames by 4x via MLX FlashVSR."""
        del resolution, batch_size, temporal_overlap
        if self.pipeline is None:
            raise RuntimeError("FlashVSREngine has been released.")

        def check_cancelled() -> None:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("FlashVSR job cancelled")

        def report(stage: str, completed: int = 0, total: int = 0) -> None:
            check_cancelled()
            if progress_callback is not None:
                progress_callback(stage, completed, total)

        check_cancelled()
        if not frames:
            return []

        in_h, in_w = frames[0].shape[:2]
        out_w = in_w * OUTPUT_SCALE
        out_h = in_h * OUTPUT_SCALE
        plan = plan_tiles(
            out_w,
            out_h,
            budget_bytes=getattr(self, "_budget_bytes_override", None),
            tile_size_override=getattr(self, "_tile_size_override", None),
        )

        n_in = len(frames)
        n_pad = padded_frame_count(n_in)
        report("preparing_clip", 0, 0)
        lq_padded = upscale_and_pad_frames(
            frames,
            plan.padded_width,
            plan.padded_height,
            resample=getattr(self, "_upscale_resample", Image.Resampling.BICUBIC),
        )

        num_tiles = len(plan.tiles)
        if num_tiles == 1:
            tile = plan.tiles[0]
            check_cancelled()
            if tile_callback is not None:
                tile_callback("enhancing", "started", 0, 1, 0, 0, out_w, out_h, out_w, out_h)

            def single_step(step: int, total_steps: int) -> None:
                report("enhancing", step, total_steps)

            tile_out = self._run_tile(lq_padded, n_in, n_pad, tile, seed, single_step)
            check_cancelled()
            cropped = tile_out[:, :out_h, :out_w, :].astype(np.uint8)
            if tile_callback is not None:
                tile_callback("enhancing", "completed", 1, 1, 0, 0, out_w, out_h, out_w, out_h)
            report("finishing", 1, 1)
            return [np.ascontiguousarray(cropped[idx]) for idx in range(n_in)]

        # Keep the blended canvas in float32 unless very large (>= ~1.8 GB) to avoid swap.
        canvas_dtype = np.float16 if n_in * out_h * out_w > 150_000_000 else np.float32
        canvas = np.zeros((n_in, out_h, out_w, 3), dtype=canvas_dtype)
        weight_sum = np.zeros((out_h, out_w), dtype=np.float32)

        for tile_idx, tile in enumerate(plan.tiles):
            check_cancelled()
            vis_w = min(tile.width, out_w - tile.x)
            vis_h = min(tile.height, out_h - tile.y)
            if tile_callback is not None and vis_w > 0 and vis_h > 0:
                tile_callback(
                    "enhancing",
                    "started",
                    tile_idx,
                    num_tiles,
                    tile.x,
                    tile.y,
                    vis_w,
                    vis_h,
                    out_w,
                    out_h,
                )

            def tile_step(step: int, total_steps: int, idx: int = tile_idx) -> None:
                report("enhancing", idx * total_steps + step, num_tiles * total_steps)

            lq_slice = lq_padded[:, tile.y : tile.y + tile.height, tile.x : tile.x + tile.width, :]
            tile_out = self._run_tile(lq_slice, n_in, n_pad, tile, seed, tile_step)
            check_cancelled()

            if vis_w > 0 and vis_h > 0:
                w_crop = tile.weights[:vis_h, :vis_w]
                canvas[:, tile.y : tile.y + vis_h, tile.x : tile.x + vis_w, :] += (
                    tile_out[:, :vis_h, :vis_w, :] * w_crop[None, :, :, None]
                ).astype(canvas_dtype)
                weight_sum[tile.y : tile.y + vis_h, tile.x : tile.x + vis_w] += w_crop

            if tile_callback is not None and vis_w > 0 and vis_h > 0:
                tile_callback(
                    "enhancing",
                    "completed",
                    tile_idx + 1,
                    num_tiles,
                    tile.x,
                    tile.y,
                    vis_w,
                    vis_h,
                    out_w,
                    out_h,
                )

        if np.any(weight_sum <= 0):
            raise RuntimeError("FlashVSR tile plan left uncovered output pixels.")
        canvas_f32 = canvas.astype(np.float32) / weight_sum[None, :, :, None]
        out_uint8 = np.clip(canvas_f32, 0.0, 255.0).astype(np.uint8)
        report("finishing", 1, 1)
        return [np.ascontiguousarray(out_uint8[idx]) for idx in range(n_in)]
