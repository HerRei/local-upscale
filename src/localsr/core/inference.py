import gc
import threading
from collections.abc import Callable

import numpy as np
import torch
import torch.nn.functional as F

from .output_writer import OutputWriter
from .tiling import generate_tiles


def _apply_gpu_memory_limit(device: torch.device, safe_memory: bool) -> float | None:
    """Apply a hard allocator ceiling and return its fraction of device capacity."""
    if device.type in {"cuda", "xpu"}:
        backend = torch.cuda if device.type == "cuda" else torch.xpu
        free_memory, total_memory = backend.mem_get_info(device)
        if total_memory <= 0:
            return None
        # Cap against memory that was free immediately before model loading, while
        # also ensuring LocalSR never owns more than 90% of total visible VRAM.
        free_share = free_memory / total_memory
        headroom = 0.80 if safe_memory else 0.90
        fraction = max(0.01, min(0.90, free_share * headroom))
        backend.set_per_process_memory_fraction(fraction, device)
        return fraction

    if device.type == "mps":
        # MPS uses unified memory, but Metal allocations still receive a hard
        # ceiling. Non-GPU macOS memory pressure remains advisory in preflight.
        fraction = 0.52 if safe_memory else 0.62
        setter = getattr(torch.mps, "set_per_process_memory_fraction", None)
        if callable(setter):
            setter(fraction)
        return fraction

    return None


class InferenceEngine:
    def __init__(self, model_adapter):
        self.model_adapter = model_adapter
        self._loaded_model = None
        self._loaded_model_path: str | None = None
        self._loaded_device: torch.device | None = None
        self._loaded_precision: torch.dtype | None = None

    def load_model(
        self, model_path: str, device_str: str, precision_str: str, model_info
    ) -> tuple[torch.device, torch.dtype]:
        """Load a model once and cache it for repeated inference calls.

        Video pipelines call this once before the frame loop and then call
        process_frame() per frame. Image pipelines still use process_image(),
        which calls this internally.
        """
        device = torch.device(device_str)
        precision = (
            torch.float16
            if precision_str == "fp16" and model_info.half_supported
            else torch.float32
        )
        if (
            self._loaded_model is not None
            and self._loaded_model_path == model_path
            and self._loaded_device == device
            and self._loaded_precision == precision
        ):
            return device, precision

        # Release any previously loaded model before loading a new one.
        self.release_model()

        _apply_gpu_memory_limit(device, getattr(model_info, "safe_memory", True))
        model, _ = self.model_adapter.load(model_path, device, precision)
        self._loaded_model = model
        self._loaded_model_path = model_path
        self._loaded_device = device
        self._loaded_precision = precision
        return device, precision

    def release_model(self) -> None:
        if self._loaded_model is not None:
            del self._loaded_model
            self._loaded_model = None
            self._loaded_model_path = None
            self._loaded_device = None
            self._loaded_precision = None
            gc.collect()

    def process_frame(
        self,
        img_tensor: torch.Tensor,
        model_info,
        tile_size: int,
        halo: int,
        cancel_event: threading.Event,
        progress_callback: Callable[[int, int, int], None],
        safe_memory: bool = True,
        device: torch.device | None = None,
        precision: torch.dtype | None = None,
        tile_callback: Callable[..., None] | None = None,
    ) -> np.ndarray:
        """Process a single frame using the already-loaded model.

        Returns a uint8 (C, H*scale, W*scale) numpy array. Memory-mapped
        OutputWriter is avoided here because video frames are transient —
        the encoder streams them straight out.
        """
        if device is None:
            device = self._loaded_device
        if precision is None:
            precision = self._loaded_precision
        if device is None or precision is None or self._loaded_model is None:
            raise RuntimeError("Model is not loaded. Call load_model() first.")
        model = self._loaded_model

        _, h, w = img_tensor.shape
        scale = model_info.scale
        out_channels = model_info.out_channels
        out_shape = (out_channels, h * scale, w * scale)
        out_array = np.zeros(out_shape, dtype=np.uint8)

        current_tile_size = tile_size
        min_tile_size = 64 if safe_memory else 4

        while current_tile_size >= min_tile_size:
            try:
                tiles = list(generate_tiles(w, h, current_tile_size, halo, scale))
                total_tiles = len(tiles)

                for i, t in enumerate(tiles):
                    if cancel_event.is_set():
                        raise InterruptedError("Cancelled")

                    if tile_callback is not None:
                        tile_callback(
                            "started",
                            t,
                            None,
                            i,
                            total_tiles,
                            w * scale,
                            h * scale,
                            current_tile_size,
                        )

                    tile_input = img_tensor[
                        :, t.halo_y : t.halo_y + t.halo_h, t.halo_x : t.halo_x + t.halo_w
                    ]

                    if t.pad_left > 0 or t.pad_right > 0 or t.pad_top > 0 or t.pad_bottom > 0:
                        if (
                            t.pad_left >= tile_input.shape[2]
                            or t.pad_right >= tile_input.shape[2]
                            or t.pad_top >= tile_input.shape[1]
                            or t.pad_bottom >= tile_input.shape[1]
                        ):
                            tile_input = F.pad(
                                tile_input,
                                (t.pad_left, t.pad_right, t.pad_top, t.pad_bottom),
                                mode="replicate",
                            )
                        else:
                            tile_input = F.pad(
                                tile_input,
                                (t.pad_left, t.pad_right, t.pad_top, t.pad_bottom),
                                mode="reflect",
                            )

                    mult = model_info.size_requirements_mult
                    min_size = model_info.size_requirements_min
                    square = getattr(model_info, "size_requirements_square", False)

                    th, tw = tile_input.shape[1], tile_input.shape[2]
                    target_w = max(tw, min_size)
                    target_h = max(th, min_size)
                    target_w = ((target_w + mult - 1) // mult) * mult
                    target_h = ((target_h + mult - 1) // mult) * mult
                    if square:
                        target_w = target_h = max(target_w, target_h)
                    pad_w = target_w - tw
                    pad_h = target_h - th

                    if pad_w > 0 or pad_h > 0:
                        tile_input = F.pad(tile_input, (0, pad_w, 0, pad_h), mode="replicate")

                    tile_input_device = tile_input.unsqueeze(0).to(device).to(precision)

                    with torch.no_grad():
                        out_device = model(tile_input_device)

                    if torch.isnan(out_device).any() or torch.isinf(out_device).any():
                        raise ValueError(
                            "Model produced NaN or Infinity. Precision might be unsupported."
                        )

                    out_cpu = out_device.squeeze(0).to(torch.float32).cpu()

                    if pad_w > 0 or pad_h > 0:
                        out_cpu = out_cpu[
                            :,
                            : out_cpu.shape[1] - (pad_h * scale),
                            : out_cpu.shape[2] - (pad_w * scale),
                        ]

                    out_core = out_cpu[
                        :,
                        halo * scale : out_cpu.shape[1] - halo * scale,
                        halo * scale : out_cpu.shape[2] - halo * scale,
                    ]

                    out_core = torch.clamp(out_core, 0, 1) * 255.0
                    out_core_np = out_core.byte().numpy()

                    out_array[:, t.out_y : t.out_y + t.out_h, t.out_x : t.out_x + t.out_w] = (
                        out_core_np
                    )

                    if tile_callback is not None:
                        tile_callback(
                            "completed",
                            t,
                            out_core_np,
                            i + 1,
                            total_tiles,
                            w * scale,
                            h * scale,
                            current_tile_size,
                        )

                    progress_callback(i + 1, total_tiles, current_tile_size)

                    if safe_memory:
                        if device.type == "mps":
                            torch.mps.empty_cache()
                        elif device.type == "cuda":
                            torch.cuda.empty_cache()
                        elif device.type == "xpu":
                            torch.xpu.empty_cache()
                        gc.collect()

                return out_array

            except RuntimeError as e:
                err_str = str(e).lower()
                if (
                    "out of memory" in err_str
                    or "not enough memory" in err_str
                    or "allocate" in err_str
                ):
                    if device.type == "mps":
                        torch.mps.empty_cache()
                    elif device.type == "cuda":
                        torch.cuda.empty_cache()
                    elif device.type == "xpu":
                        torch.xpu.empty_cache()
                    gc.collect()

                    current_tile_size = current_tile_size // 2
                    if current_tile_size < min_tile_size:
                        raise RuntimeError(
                            "Out of memory. Reduced tile size to minimum and still failed."
                        ) from e

                    if tile_callback is not None:
                        tile_callback(
                            "reset",
                            None,
                            None,
                            0,
                            0,
                            w * scale,
                            h * scale,
                            current_tile_size,
                        )
                    # Re-initialize output array cleanly
                    out_array = np.zeros(out_shape, dtype=np.uint8)
                    continue
                raise

        raise RuntimeError("Failed to process frame.")

    def process_image(
        self,
        img_data: dict,
        model_info,
        model_path: str,
        device_str: str,
        precision_str: str,
        tile_size: int,
        halo: int,
        cancel_event: threading.Event,
        progress_callback: Callable[[int, int, int], None],
        safe_memory: bool = True,
        tile_callback: Callable[..., None] | None = None,
    ) -> OutputWriter:

        # load_model applies GPU memory limits and loads the model onto device.
        device, precision = self.load_model(model_path, device_str, precision_str, model_info)
        model = self._loaded_model

        img_tensor = img_data["tensor"]
        _, h, w = img_tensor.shape
        scale = model_info.scale

        writer = OutputWriter((model_info.out_channels, h * scale, w * scale), dtype=np.uint8)

        current_tile_size = tile_size

        min_tile_size = 64 if safe_memory else 4
        try:
            while current_tile_size >= min_tile_size:
                try:
                    tiles = list(generate_tiles(w, h, current_tile_size, halo, scale))
                    total_tiles = len(tiles)

                    for i, t in enumerate(tiles):
                        if cancel_event.is_set():
                            writer.cleanup()
                            raise InterruptedError("Cancelled")

                        if tile_callback is not None:
                            tile_callback(
                                "started",
                                t,
                                None,
                                i,
                                total_tiles,
                                w * scale,
                                h * scale,
                                current_tile_size,
                            )

                        # Extract halo region (clamped to image bounds internally, but tiling.py gives exact coords)
                        tile_input = img_tensor[
                            :, t.halo_y : t.halo_y + t.halo_h, t.halo_x : t.halo_x + t.halo_w
                        ]

                        # Reflection padding for edges
                        if t.pad_left > 0 or t.pad_right > 0 or t.pad_top > 0 or t.pad_bottom > 0:
                            # PyTorch reflect pad has limits: pad size must be < dimension size
                            # If image is smaller than halo, reflect will fail. We use replicate or constant in that case.
                            if (
                                t.pad_left >= tile_input.shape[2]
                                or t.pad_right >= tile_input.shape[2]
                                or t.pad_top >= tile_input.shape[1]
                                or t.pad_bottom >= tile_input.shape[1]
                            ):
                                tile_input = F.pad(
                                    tile_input,
                                    (t.pad_left, t.pad_right, t.pad_top, t.pad_bottom),
                                    mode="replicate",
                                )
                            else:
                                tile_input = F.pad(
                                    tile_input,
                                    (t.pad_left, t.pad_right, t.pad_top, t.pad_bottom),
                                    mode="reflect",
                                )

                        # Divisibility padding
                        mult = model_info.size_requirements_mult
                        min_size = model_info.size_requirements_min
                        square = getattr(model_info, "size_requirements_square", False)

                        th, tw = tile_input.shape[1], tile_input.shape[2]
                        target_w = max(tw, min_size)
                        target_h = max(th, min_size)
                        target_w = ((target_w + mult - 1) // mult) * mult
                        target_h = ((target_h + mult - 1) // mult) * mult
                        if square:
                            target_w = target_h = max(target_w, target_h)
                        pad_w = target_w - tw
                        pad_h = target_h - th

                        if pad_w > 0 or pad_h > 0:
                            tile_input = F.pad(tile_input, (0, pad_w, 0, pad_h), mode="replicate")

                        tile_input_device = tile_input.unsqueeze(0).to(device).to(precision)

                        with torch.no_grad():
                            out_device = model(tile_input_device)

                        if torch.isnan(out_device).any() or torch.isinf(out_device).any():
                            raise ValueError(
                                "Model produced NaN or Infinity. Precision might be unsupported."
                            )

                        out_cpu = out_device.squeeze(0).to(torch.float32).cpu()

                        if pad_w > 0 or pad_h > 0:
                            out_cpu = out_cpu[
                                :,
                                : out_cpu.shape[1] - (pad_h * scale),
                                : out_cpu.shape[2] - (pad_w * scale),
                            ]

                        out_core = out_cpu[
                            :,
                            halo * scale : out_cpu.shape[1] - halo * scale,
                            halo * scale : out_cpu.shape[2] - halo * scale,
                        ]

                        # Quantize
                        out_core = torch.clamp(out_core, 0, 1) * 255.0
                        out_core_np = out_core.byte().numpy()

                        writer.write_tile(out_core_np, t.out_x, t.out_y)

                        if tile_callback is not None:
                            tile_callback(
                                "completed",
                                t,
                                out_core_np,
                                i + 1,
                                total_tiles,
                                w * scale,
                                h * scale,
                                current_tile_size,
                            )

                        progress_callback(i + 1, total_tiles, current_tile_size)

                        if safe_memory:
                            if device.type == "mps":
                                torch.mps.empty_cache()
                            elif device.type == "cuda":
                                torch.cuda.empty_cache()
                            elif device.type == "xpu":
                                torch.xpu.empty_cache()
                            gc.collect()

                    return writer

                except RuntimeError as e:
                    # Check for OOM
                    err_str = str(e).lower()
                    if (
                        "out of memory" in err_str
                        or "not enough memory" in err_str
                        or "allocate" in err_str
                    ):
                        if device.type == "mps":
                            torch.mps.empty_cache()
                        elif device.type == "cuda":
                            torch.cuda.empty_cache()
                        elif device.type == "xpu":
                            torch.xpu.empty_cache()
                        gc.collect()

                        current_tile_size = current_tile_size // 2
                        if current_tile_size < min_tile_size:
                            writer.cleanup()
                            raise RuntimeError(
                                "Out of memory. Reduced tile size to minimum and still failed."
                            ) from e

                        if tile_callback is not None:
                            tile_callback(
                                "reset",
                                None,
                                None,
                                0,
                                0,
                                w * scale,
                                h * scale,
                                current_tile_size,
                            )

                        # Re-initialize output writer cleanly
                        writer.cleanup()
                        writer = OutputWriter(
                            (model_info.out_channels, h * scale, w * scale), dtype=np.uint8
                        )
                        continue
                    else:
                        writer.cleanup()
                        raise

            writer.cleanup()
            raise RuntimeError("Failed to process image.")
        except Exception:
            writer.cleanup()
            raise
