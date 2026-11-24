"""LocalSR adapter around the vendored MLX FlashVSR v1.1 Tiny video upscaler.

Drives the streaming MLX FlashVSRTinyPipeline over one independent window of
context + new frames at 4x fixed scale, with 128-multiple spatial edge padding,
8n+1 temporal padding, analytic unified-memory tile planning, and linear feather
blending across 128 px tile overlaps.
"""

from __future__ import annotations

import gc
import re
import sys
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
FLOAT16_CANVAS_ELEMENTS = 150_000_000

# FlashVSR outputs 8n - 3 frames when fed 8n + 1 frames (n >= 3).
# Window 37 + 4 repeated tail frames = 41 = 8*5 + 1, yielding 37 outputs with
# zero wasted streaming steps; overlap 8 gives 29 new frames per window.
DEFAULT_TEMPORAL_WINDOW = 37
DEFAULT_TEMPORAL_OVERLAP = 8
MAX_TEMPORAL_WINDOW = 61

# Memory model: budget = 0.85 * mps_recommended_working_set - host_buffers;
# max_tile_area = (effective_budget - FIXED_MEMORY_BYTES) / scaled_per_pixel.
# Fitted from two bfloat16 measurements on Apple M1 Pro (16 GB, 37-frame window / n_pad = 41,
# mx.get_peak_memory) after freeing lq_np and tile_frames upon conversion:
#   512x256 (131,072 px): peak_mlx =  7,297,914,864 B (footprint 12,295,391,872 B, max_rss 5,344,313,344 B, 0 swaps)
#   768x512 (393,216 px): peak_mlx = 10,952,918,736 B (footprint 12,456,282,200 B, max_rss 6,137,610,240 B, 0 swaps)
MPS_CALIBRATED_PAD_FRAMES = 41
FIXED_MEMORY_BYTES = 5_470_412_928
PER_PIXEL_MEMORY_BYTES = 13_942.7

# CUDA / ROCm PyTorch bfloat16 memory model (gather attention, the packaged path): host buffers
# (lq_padded, canvas) live in system RAM, so only VRAM scales with n_pad.
# max_tile_area = (budget - ROCM_FIXED_MEMORY_BYTES) / scaled_per_pixel, budget =
# DISCRETE_BUDGET_FRACTION * free VRAM (torch.cuda.mem_get_info).
# Measured with LocalSR's engine on an AMD Radeon RX 9060 XT (16 GB, gfx1200, PyTorch 2.14.1+rocm7.2),
# 37-frame window (n_pad = 41), torch.cuda.max_memory_allocated:
#   512x256  (  131,072 px): 5.76 GB allocated, 6.00 GB reserved
#   896x512  (  458,752 px): 8.07 GB allocated, 8.37 GB reserved
#   768x1408 (1,081,344 px): 12.10 GB allocated but 16.2 GB reserved, with out-of-memory retries that
#                            made it 10x slower per pixel: the allocator needs headroom beyond the
#                            allocated peak, hence the lower budget fraction for discrete GPUs.
# Linear fit through the first two: 4.84 GB + 7.04 kB per output pixel at n_pad = 41.
ROCM_CALIBRATED_PAD_FRAMES = 41
ROCM_FIXED_MEMORY_BYTES = 4_840_000_000
ROCM_PER_PIXEL_MEMORY_BYTES = 7_040.0
DISCRETE_BUDGET_FRACTION = 0.70

_MEMORY_MODELS = {
    "mps": (FIXED_MEMORY_BYTES, PER_PIXEL_MEMORY_BYTES, MPS_CALIBRATED_PAD_FRAMES, True),
    "rocm": (
        ROCM_FIXED_MEMORY_BYTES,
        ROCM_PER_PIXEL_MEMORY_BYTES,
        ROCM_CALIBRATED_PAD_FRAMES,
        False,
    ),
    "cuda": (
        ROCM_FIXED_MEMORY_BYTES,
        ROCM_PER_PIXEL_MEMORY_BYTES,
        ROCM_CALIBRATED_PAD_FRAMES,
        False,
    ),
}


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


def rocm_free_memory_bytes(device: str = "cuda") -> int:
    """Return available ROCm VRAM in bytes from mem_get_info (free + own allocated, <= total)."""
    import torch

    free, total = torch.cuda.mem_get_info(device)
    allocated = 0
    try:
        if torch.cuda.is_available():
            allocated = int(torch.cuda.memory_allocated(device))
    except (RuntimeError, AttributeError):
        allocated = 0
    return min(int(total), int(free) + allocated)


def _has_rocm() -> bool:
    import torch

    return bool(getattr(getattr(torch, "version", None), "hip", None))


def _has_cuda() -> bool:
    import torch

    return bool(getattr(getattr(torch, "version", None), "cuda", None))


def _low_memory_message(backend: str, detail: str) -> str:
    if backend == "rocm":
        return (
            f"Not enough AMD ROCm GPU memory for FlashVSR: {detail}. "
            "Close other applications or choose Frame-by-frame video."
        )
    if backend == "cuda":
        return (
            f"Not enough NVIDIA CUDA GPU memory for FlashVSR: {detail}. "
            "Close other applications or choose Frame-by-frame video."
        )
    return (
        f"Not enough Apple Silicon unified memory for FlashVSR: {detail}. "
        "Close other applications or choose Frame-by-frame video."
    )


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
    *,
    backend: str = "mps",
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
            _low_memory_message(
                backend,
                "no valid tile size fits within the available memory budget",
            )
        )
    return best_dims


def _host_buffer_bytes(
    out_width: int,
    out_height: int,
    pad_width: int,
    pad_height: int,
    n_in: int,
    *,
    include_canvas: bool,
) -> int:
    lq_bytes = n_in * pad_width * pad_height * 3
    if not include_canvas:
        return lq_bytes
    canvas_elem_bytes = 2 if n_in * out_width * out_height > FLOAT16_CANVAS_ELEMENTS else 4
    canvas_bytes = n_in * out_width * out_height * 3 * canvas_elem_bytes
    weight_bytes = out_width * out_height * 4
    return lq_bytes + canvas_bytes + weight_bytes


def plan_tiles(
    out_width: int,
    out_height: int,
    *,
    n_frames: int = DEFAULT_TEMPORAL_WINDOW,
    backend: str = "mps",
    device: str = "mps",
    budget_bytes: int | None = None,
    max_tile_side: int = MAX_TILE_SIDE,
    tile_size_override: tuple[int, int] | None = None,
) -> FlashVSRTilePlan:
    """Plan 128-aligned output tiles and feather weights from the backend memory budget."""
    if backend not in _MEMORY_MODELS:
        raise ValueError(f"Unsupported FlashVSR memory model backend: {backend!r}")
    fixed_bytes, per_pixel_bytes, calibrated_pad_frames, unified_memory = _MEMORY_MODELS[backend]
    n_pad = padded_frame_count(n_frames)
    scaled_per_pixel = per_pixel_bytes * max(1.0, float(n_pad) / float(calibrated_pad_frames))

    pad_w = ceil_multiple(out_width, SPATIAL_MULTIPLE)
    pad_h = ceil_multiple(out_height, SPATIAL_MULTIPLE)

    if budget_bytes is None:
        raw_budget = (
            rocm_free_memory_bytes(device)
            if backend in ("rocm", "cuda")
            else mps_recommended_working_set_bytes()
        )
        fraction = DISCRETE_BUDGET_FRACTION if backend in ("rocm", "cuda") else BUDGET_FRACTION
        budget_bytes = int(raw_budget * fraction)

    if tile_size_override is not None:
        tw, th = tile_size_override
        if tw <= 0 or th <= 0 or tw % SPATIAL_MULTIPLE != 0 or th % SPATIAL_MULTIPLE != 0:
            raise ValueError(f"Tile size must be positive multiples of 128, got {(tw, th)}")
        xs = compute_axis_starts(pad_w, tw, TILE_OVERLAP)
        ys = compute_axis_starts(pad_h, th, TILE_OVERLAP)
        host_bytes = (
            _host_buffer_bytes(
                out_width,
                out_height,
                pad_w,
                pad_h,
                n_frames,
                include_canvas=len(xs) * len(ys) > 1,
            )
            if unified_memory
            else 0
        )
        max_area = int((budget_bytes - host_bytes - fixed_bytes) / scaled_per_pixel)
    else:
        single_host = (
            _host_buffer_bytes(out_width, out_height, pad_w, pad_h, n_frames, include_canvas=False)
            if unified_memory
            else 0
        )
        single_max_area = int((budget_bytes - single_host - fixed_bytes) / scaled_per_pixel)
        if pad_w <= max_tile_side and pad_h <= max_tile_side and pad_w * pad_h <= single_max_area:
            tw, th = pad_w, pad_h
            max_area = single_max_area
        else:
            multi_host = (
                _host_buffer_bytes(
                    out_width, out_height, pad_w, pad_h, n_frames, include_canvas=True
                )
                if unified_memory
                else 0
            )
            max_area = int((budget_bytes - multi_host - fixed_bytes) / scaled_per_pixel)
            if max_area < MIN_TILE_SIDE * MIN_TILE_SIDE:
                raise RuntimeError(
                    _low_memory_message(
                        backend,
                        "the memory budget leaves less than a 512x512 output tile",
                    )
                )
            tw, th = _choose_tile_dimensions(
                pad_w,
                pad_h,
                max_area,
                max_tile_side,
                backend=backend,
            )
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


def output_size(in_width: int, in_height: int, resolution: int) -> tuple[int, int]:
    """Output size for a requested shorter edge: source proportions, even sides.

    0 means the model's own 4x. Rounding matches the SeedVR2 engine and the worker's
    disk estimate, so every temporal engine reports the same size for a request.
    """
    shorter = min(in_width, in_height)
    if resolution <= 0:
        resolution = shorter * OUTPUT_SCALE
    ratio = resolution / shorter
    return max(2, int(in_width * ratio) // 2 * 2), max(2, int(in_height * ratio) // 2 * 2)


def model_size(in_width: int, in_height: int, out_width: int, out_height: int) -> tuple[int, int]:
    """The size FlashVSR runs at: the output itself up to 4x, beyond that 4x.

    The model restores a bicubic-enlarged clip, so any size up to its training scale
    works directly; a larger request is resized from the 4x result afterwards.
    """
    if out_width <= in_width * OUTPUT_SCALE and out_height <= in_height * OUTPUT_SCALE:
        return out_width, out_height
    return in_width * OUTPUT_SCALE, in_height * OUTPUT_SCALE


def upscale_and_pad_frames(
    frames: Sequence[np.ndarray],
    out_w: int,
    out_h: int,
    pad_width: int,
    pad_height: int,
    *,
    resample: Image.Resampling = Image.Resampling.BICUBIC,
) -> np.ndarray:
    """Resize RGB uint8 frames to (out_h, out_w) and edge-replicate pad to the padded size."""
    in_h, in_w = frames[0].shape[:2]
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


def _to_output(frame: np.ndarray, width: int, height: int) -> np.ndarray:
    """Resize a model-size frame to the requested output (only needed beyond 4x)."""
    if frame.shape[1] != width or frame.shape[0] != height:
        frame = np.asarray(
            Image.fromarray(frame).resize((width, height), Image.Resampling.LANCZOS), np.uint8
        )
    return np.ascontiguousarray(frame)


def _resolve_device_and_backend(device: str) -> tuple[str, str]:
    device_str = str(device)
    backend = device_str.split(":", 1)[0]
    if backend == "auto":
        if sys.platform == "darwin":
            return "mps", "mps"
        if _has_rocm():
            return "cuda", "rocm"
        if _has_cuda():
            return "cuda", "cuda"
        raise ValueError(
            "FlashVSR runs on Apple Silicon GPU (mps), NVIDIA CUDA, or AMD ROCm GPU only. "
            "Choose Frame-by-frame video for CPU or other backends."
        )
    if backend == "mps":
        return "mps", "mps"
    if backend == "cuda":
        if not re.fullmatch(r"cuda(?::\d+)?", device_str):
            raise ValueError(f"Invalid FlashVSR CUDA/ROCm device: {device!r}")
        if _has_rocm():
            return device_str, "rocm"
        if _has_cuda():
            return device_str, "cuda"
        raise ValueError(
            "FlashVSR on GPU requires NVIDIA CUDA or AMD ROCm. "
            "Choose Frame-by-frame video for CPU or other backends."
        )
    raise ValueError(
        "FlashVSR runs on Apple Silicon GPU (mps), NVIDIA CUDA, or AMD ROCm GPU only. "
        "Choose Frame-by-frame video for CPU or other backends."
    )


class FlashVSREngine:
    """One job's worth of FlashVSR v1.1 Tiny state (MLX on MPS or PyTorch on CUDA/ROCm) and tiling."""

    def __init__(
        self,
        bundle_dir: str,
        device: str = "mps",
        precision: str = "bf16",
    ) -> None:
        del precision  # FlashVSR v1.1 runs in bfloat16 on both MLX/Metal and PyTorch/ROCm.
        resolved_device, backend_kind = _resolve_device_and_backend(device)
        self.bundle_dir = Path(bundle_dir)
        self.device = resolved_device
        self._backend = backend_kind
        self.max_temporal_window = MAX_TEMPORAL_WINDOW
        self._budget_bytes_override: int | None = None
        self._tile_size_override: tuple[int, int] | None = None
        self._upscale_resample: Image.Resampling = Image.Resampling.BICUBIC

        if self._backend == "mps":
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
        else:
            import torch

            from .torch_flashvsr.pipeline import FlashVSRTinyPipeline as TorchFlashVSRTinyPipeline

            self.pipeline = TorchFlashVSRTinyPipeline.from_pretrained(
                self.bundle_dir,
                device=self.device,
                dtype=torch.bfloat16,
            )

    def release(self) -> None:
        """Free loaded model weights and clear the GPU allocator cache."""
        backend = getattr(self, "_backend", "mps")
        pipe = getattr(self, "pipeline", None)
        if pipe is not None:
            tcdecoder = getattr(pipe, "TCDecoder", None)
            if tcdecoder is not None:
                if hasattr(tcdecoder, "set_rocm_cache_release"):
                    tcdecoder.set_rocm_cache_release(False)
                if hasattr(tcdecoder, "clean_mem"):
                    tcdecoder.clean_mem()
            dit = getattr(pipe, "dit", None)
            if dit is not None:
                if hasattr(dit, "clear_cross_kv"):
                    dit.clear_cross_kv()
                lq_proj = getattr(dit, "LQ_proj_in", None)
                if lq_proj is not None and hasattr(lq_proj, "clear_cache"):
                    lq_proj.clear_cache()
            pipe.dit = None
            pipe.TCDecoder = None
            pipe.ColorCorrector = None
            pipe.prompt_emb_posi = None
            pipe.t = None
            pipe.t_mod = None
            self.pipeline = None
        gc.collect()
        if backend in ("rocm", "cuda"):
            import torch

            torch.cuda.empty_cache()
        else:
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
            del pad_tail
        else:
            tile_frames = lq_tile_uint8

        # (T, H, W, 3) uint8 -> (1, 3, T, H, W) float32 in [-1, 1]
        lq_np = np.transpose(tile_frames, (3, 0, 1, 2)).astype(np.float32)
        del tile_frames
        lq_np /= 127.5
        lq_np -= 1.0
        lq_np = lq_np[None, ...]
        topk_ratio = 2.0 * 768.0 * 1280.0 / float(tile.height * tile.width)
        backend = getattr(self, "_backend", "mps")

        if getattr(self, "_numpy_stub_arrays", False):
            lq_arr: Any = lq_np
        elif backend in ("rocm", "cuda"):
            import torch

            lq_arr = torch.from_numpy(lq_np).to(device=self.device, dtype=torch.bfloat16)
            del lq_np
        else:
            import mlx.core as mx

            lq_arr = mx.array(lq_np, dtype=mx.float32).astype(mx.bfloat16)
            mx.eval(lq_arr)
            del lq_np

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
        elif backend in ("rocm", "cuda"):
            import torch

            sliced = out_tensor[:, :n_in, :, :]
            arr = sliced.detach().to(device="cpu", dtype=torch.float32).numpy()
            del out_tensor, sliced, lq_arr
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
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
        """Upscale a temporal window of RGB uint8 frames to `resolution` (shorter edge)."""
        del batch_size, temporal_overlap
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
        n_in = len(frames)
        n_pad = padded_frame_count(n_in)
        final_w, final_h = output_size(in_w, in_h, resolution)
        out_w, out_h = model_size(in_w, in_h, final_w, final_h)
        plan = plan_tiles(
            out_w,
            out_h,
            n_frames=n_in,
            backend=getattr(self, "_backend", "mps"),
            device=getattr(self, "device", "mps"),
            budget_bytes=getattr(self, "_budget_bytes_override", None),
            tile_size_override=getattr(self, "_tile_size_override", None),
        )

        report("preparing_clip", 0, 0)
        lq_padded = upscale_and_pad_frames(
            frames,
            out_w,
            out_h,
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
            del lq_padded
            check_cancelled()
            cropped = np.rint(tile_out[:, :out_h, :out_w, :]).astype(np.uint8)
            del tile_out
            if tile_callback is not None:
                tile_callback("enhancing", "completed", 1, 1, 0, 0, out_w, out_h, out_w, out_h)
            report("finishing", 1, 1)
            return [_to_output(cropped[idx], final_w, final_h) for idx in range(n_in)]

        # Keep the blended canvas in float32 unless very large (>= ~1.8 GB) to avoid swap.
        canvas_dtype = np.float16 if n_in * out_h * out_w > FLOAT16_CANVAS_ELEMENTS else np.float32
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
            del lq_slice
            check_cancelled()

            if vis_w > 0 and vis_h > 0:
                w_crop = tile.weights[:vis_h, :vis_w]
                canvas[:, tile.y : tile.y + vis_h, tile.x : tile.x + vis_w, :] += (
                    tile_out[:, :vis_h, :vis_w, :] * w_crop[None, :, :, None]
                ).astype(canvas_dtype)
                weight_sum[tile.y : tile.y + vis_h, tile.x : tile.x + vis_w] += w_crop
            del tile_out

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

        del lq_padded
        if np.any(weight_sum <= 0):
            raise RuntimeError("FlashVSR tile plan left uncovered output pixels.")
        # Normalise one frame at a time: a whole-window float32 copy is several GB at 4K.
        weights = weight_sum[:, :, None]
        result = []
        for idx in range(n_in):
            frame = np.clip(canvas[idx].astype(np.float32) / weights, 0.0, 255.0)
            result.append(_to_output(np.rint(frame).astype(np.uint8), final_w, final_h))
        del canvas, weight_sum, weights
        report("finishing", 1, 1)
        return result
