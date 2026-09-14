import gc
import logging
import threading
from collections.abc import Callable

import numpy as np
import torch
import torch.nn.functional as F

from .output_writer import OutputWriter
from .tiling import generate_tiles, make_tile


class UnstableRestorationError(ValueError):
    """Raw restoration values are invalid before descriptor range clipping."""


def _span_output_is_unstable(output: torch.Tensor) -> bool:
    # The normalized RGB contract is [0, 1]. Allow ordinary ringing/overshoot,
    # but reject the extreme values reproduced on high-contrast periodic crops.
    # Clamping these hides the failure as saturated coloured blocks.
    return bool(not torch.isfinite(output).all() or output.amin() < -4 or output.amax() > 5)


def _introduced_periodic_signal(output: torch.Tensor, source: torch.Tensor) -> torch.Tensor:
    """Measure new coherent two-pixel patterns, apart from existing texture.

    Some NAFNet failures create faint coloured checks while staying in range.
    Average the three alternating phases over 16×16 regions so isolated noise
    and ordinary edges do not dominate the check. Compare with the source;
    an existing periodic texture is not itself a restoration failure.
    """
    height, width = (output.shape[-2] // 16 * 16, output.shape[-1] // 16 * 16)
    if not height or not width:
        return output.new_zeros(())

    def phases(value):
        value = value[..., :height, :width]
        a, b = value[..., ::2, ::2], value[..., ::2, 1::2]
        c, d = value[..., 1::2, ::2], value[..., 1::2, 1::2]
        return torch.stack(
            [
                F.avg_pool2d(band * 0.25, 8, 8).abs()
                for band in (a - b + c - d, a + b - c - d, a - b - c + d)
            ]
        )

    return (phases(output) - phases(source)).amax()


def _resolve_torch_device(device_str: str) -> torch.device:
    if device_str.startswith("directml:"):
        try:
            import torch_directml
        except ImportError as error:
            raise RuntimeError(
                "DirectML was selected, but this engine pack does not include torch-directml."
            ) from error
        except (OSError, RuntimeError) as error:
            raise RuntimeError(
                "The DirectML runtime could not load. Check the installed DirectML engine "
                "and graphics driver, or select CPU processing."
            ) from error
        _, _, raw_index = device_str.partition(":")
        try:
            index = int(raw_index)
        except ValueError as error:
            raise RuntimeError(f"Invalid DirectML device identifier: {device_str}") from error
        if index < 0:
            raise RuntimeError(f"Invalid DirectML device identifier: {device_str}")
        if not bool(getattr(torch_directml, "is_available", lambda: True)()):
            raise RuntimeError("DirectML was selected, but no compatible adapter is available.")
        count = int(torch_directml.device_count())
        if index >= count:
            raise RuntimeError(
                f"DirectML adapter {index} is no longer available. "
                "Refresh Hardware and select an available GPU or CPU."
            )
        return torch_directml.device(index)
    if device_str.startswith("qnn"):
        raise RuntimeError("QNN acceleration is not available in the PyTorch/Spandrel engine yet.")
    return torch.device(device_str)


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
        # Multi-model cache: path -> (model, device, precision).
        # The active model is the one used by process_frame / process_image.
        # Switching between cached models is instant (no torch reload).
        self._loaded_models: dict[str, tuple[object, torch.device, torch.dtype]] = {}
        self._loaded_backend_ids: dict[str, str] = {}
        self._active_path: str | None = None
        # Legacy single-model fields kept for backward compat with
        # process_image and any external callers.
        self._loaded_model = None
        self._loaded_model_path: str | None = None
        self._loaded_device: torch.device | None = None
        self._loaded_precision: torch.dtype | None = None

    @property
    def active_model(self):
        """The currently active model, or None if nothing is loaded."""
        if self._active_path is None:
            return None
        entry = self._loaded_models.get(self._active_path)
        return entry[0] if entry is not None else None

    def load_model(
        self, model_path: str, device_str: str, precision_str: str, model_info
    ) -> tuple[torch.device, torch.dtype]:
        """Load a model and cache it. Switching to an already-cached model
        is instant — no torch reload, no VRAM spike.

        Video pipelines call this once per model before the frame loop
        and then switch between them by calling load_model with the
        desired path. Image pipelines still use process_image(), which
        calls this internally.
        """
        onnx_device_index = None
        if device_str.startswith("directml:"):
            from .onnx_runtime import directml_available

            if directml_available():
                try:
                    onnx_device_index = int(device_str.partition(":")[2])
                except ValueError as error:
                    raise RuntimeError(
                        f"Invalid DirectML device identifier: {device_str}"
                    ) from error
                if onnx_device_index < 0:
                    raise RuntimeError(f"Invalid DirectML device identifier: {device_str}")
        # ONNX owns the GPU tensors. PyTorch stages tiles and validates returned
        # pixels on CPU; the selected execution backend is tracked separately.
        device = (
            torch.device("cpu")
            if onnx_device_index is not None
            else _resolve_torch_device(device_str)
        )
        precision = (
            torch.float16
            if precision_str == "fp16" and model_info.half_supported and onnx_device_index is None
            else torch.float32
        )

        # Already cached? Just switch active.
        if model_path in self._loaded_models:
            cached_model, cached_device, cached_precision = self._loaded_models[model_path]
            if (
                cached_device == device
                and cached_precision == precision
                and self._loaded_backend_ids.get(model_path) == device_str
            ):
                self._active_path = model_path
                self._sync_legacy_fields()
                return device, precision
            # Precision/device changed — drop the stale cache entry and reload.
            del self._loaded_models[model_path]
            gc.collect()

        # Load the new model. The GPU memory limit is applied once per
        # device; subsequent loads on the same device keep the existing cap.
        if not any(d == device for _, d, _ in self._loaded_models.values()):
            _apply_gpu_memory_limit(device, getattr(model_info, "safe_memory", True))

        if onnx_device_index is None:
            model, _ = self.model_adapter.load(model_path, device, precision)
        else:
            model, _ = self.model_adapter.load(
                model_path, device, precision, onnx_device_index=onnx_device_index
            )
        self._loaded_models[model_path] = (model, device, precision)
        self._loaded_backend_ids[model_path] = device_str
        self._active_path = model_path
        self._sync_legacy_fields()
        return device, precision

    @property
    def active_backend_id(self):
        return self._loaded_backend_ids.get(self._active_path)

    def _sync_legacy_fields(self) -> None:
        """Keep _loaded_model / _loaded_model_path etc. in sync with the
        multi-model cache so process_image and external code that reads
        those fields continues to work.
        """
        if self._active_path is not None:
            entry = self._loaded_models.get(self._active_path)
            if entry is not None:
                self._loaded_model = entry[0]
                self._loaded_model_path = self._active_path
                self._loaded_device = entry[1]
                self._loaded_precision = entry[2]
        else:
            self._loaded_model = None
            self._loaded_model_path = None
            self._loaded_device = None
            self._loaded_precision = None

    def release_model(self, path: str | None = None) -> None:
        """Release one cached model, or all of them when path is None."""
        if path is not None:
            entry = self._loaded_models.pop(path, None)
            self._loaded_backend_ids.pop(path, None)
            # entry is a (model, device, precision) tuple. The model
            # reference is dropped by letting it go out of scope; we
            # don't need to del entry[0] (tuples are immutable).
            del entry
            if self._active_path == path:
                self._active_path = next(iter(self._loaded_models), None)
            self._sync_legacy_fields()
            gc.collect()
        else:
            for _, (model, _, _) in self._loaded_models.items():
                del model
            self._loaded_models.clear()
            self._loaded_backend_ids.clear()
            self._active_path = None
            self._sync_legacy_fields()
            gc.collect()

    def release_all(self) -> None:
        """Release every cached model. Alias for release_model(path=None)."""
        self.release_model(path=None)

    def switch_model(self, path: str) -> None:
        """Switch the active model to an already-cached one. No reload.

        This is the fast path for per-tile model selection in the
        face-aware pipeline: call load_model once per model at start,
        then switch_model per tile.
        """
        if path not in self._loaded_models:
            raise RuntimeError(f"Model {path} is not cached. Call load_model first.")
        self._active_path = path
        self._sync_legacy_fields()

    def run_tile(
        self,
        tile,
        img_tensor: torch.Tensor,
        model_info,
        device: torch.device,
        precision: torch.dtype,
        halo: int,
        model,
        *,
        float_output: bool = False,
        cancel_event: threading.Event | None = None,
    ) -> np.ndarray:
        """Render a tile, recovering unstable regions with more real context.

        NAFNet and SPAN can produce extreme finite values on some small crops. The
        descriptor clips those into colourful blocks. Retry only that region
        on a larger aligned crop, with the same weights, precision and device.
        Never publish the damaged tile or substitute an unprocessed source.
        """

        architecture = getattr(model_info, "architecture", "Model")

        def run(region):
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("Cancelled")
            set_cancel = getattr(model, "set_cancel_event", None)
            if callable(set_cancel):
                set_cancel(cancel_event)
            return self._run_tile_once(
                region,
                img_tensor,
                model_info,
                device,
                precision,
                halo,
                model,
                float_output=float_output,
            )

        try:
            return run(tile)
        except UnstableRestorationError:
            pass
        _, height, width = img_tensor.shape
        attempted = {(tile.core_x, tile.core_y, tile.core_w, tile.core_h)}
        for size, fill_edges in ((512, False), (512, True), (1024, False), (1024, True)):
            if size <= max(tile.core_w, tile.core_h):
                continue
            # Preserve alignment, including a partial region at the image's
            # right/bottom edges. Non-power-of-two user tile sizes also fit.
            x = max(tile.core_x // size * size, tile.core_x + tile.core_w - size)
            y = max(tile.core_y // size * size, tile.core_y + tile.core_h - size)
            if fill_edges:
                # A partial border crop may still be almost entirely flat
                # paper/black. Fill the context window inward with real pixels.
                x, y = min(x, max(0, width - size)), min(y, max(0, height - size))
            region = make_tile(
                width,
                height,
                x,
                y,
                min(size, width - x),
                min(size, height - y),
                halo,
                model_info.scale,
            )
            key = (region.core_x, region.core_y, region.core_w, region.core_h)
            if key in attempted:
                continue
            attempted.add(key)
            try:
                output = run(region)
            except UnstableRestorationError:
                continue
            except RuntimeError as error:
                if any(
                    word in str(error).lower()
                    for word in ("out of memory", "not enough memory", "allocate")
                ):
                    # The outer OOM loop must not shrink this region back to
                    # the known-bad crop and keep retrying indefinitely.
                    raise UnstableRestorationError(
                        f"{architecture} needs more image context for this region, but the device has "
                        "insufficient memory. No damaged output was saved. Try CPU or another model."
                    ) from error
                raise
            scale = model_info.scale
            ox, oy = (tile.core_x - x) * scale, (tile.core_y - y) * scale
            logging.getLogger(__name__).warning(
                "Recovered %s region (%s, %s) using %s-pixel context on %s.",
                architecture,
                tile.core_x,
                tile.core_y,
                size,
                device,
            )
            return output[:, oy : oy + tile.out_h, ox : ox + tile.out_w].copy()
        raise UnstableRestorationError(
            f"{architecture} produced unstable values in this image region even with more context. "
            "No damaged output was saved. Try another model."
        )

    def _run_tile_once(
        self,
        tile,
        img_tensor: torch.Tensor,
        model_info,
        device: torch.device,
        precision: torch.dtype,
        halo: int,
        model,
        *,
        float_output: bool = False,
    ) -> np.ndarray:
        """Run inference on a single tile and return the core output as uint8 (C, H, W).

        This is the shared per-tile logic used by process_frame, process_image,
        and the face-aware pipeline. It handles halo extraction, padding,
        divisibility, inference, and halo/padding stripping.
        """
        tile_input = img_tensor[
            :, tile.halo_y : tile.halo_y + tile.halo_h, tile.halo_x : tile.halo_x + tile.halo_w
        ]

        if tile.pad_left > 0 or tile.pad_right > 0 or tile.pad_top > 0 or tile.pad_bottom > 0:
            if (
                tile.pad_left >= tile_input.shape[2]
                or tile.pad_right >= tile_input.shape[2]
                or tile.pad_top >= tile_input.shape[1]
                or tile.pad_bottom >= tile_input.shape[1]
            ):
                tile_input = F.pad(
                    tile_input,
                    (tile.pad_left, tile.pad_right, tile.pad_top, tile.pad_bottom),
                    mode="replicate",
                )
            else:
                tile_input = F.pad(
                    tile_input,
                    (tile.pad_left, tile.pad_right, tile.pad_top, tile.pad_bottom),
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

        hook = None
        architecture = getattr(model_info, "architecture", "").lower()
        if architecture in {"nafnet", "span"}:
            # Check the network output before Spandrel clamps it. NAFNet and
            # SPAN use separate limits so normal sharpening overshoot is allowed.
            raw_model = getattr(model, "model", model)

            def validate_raw(_module, _args, output):
                scale = model_info.scale
                core = output[
                    ...,
                    halo * scale : (halo + tile.core_h) * scale,
                    halo * scale : (halo + tile.core_w) * scale,
                ]
                if architecture == "span":
                    if _span_output_is_unstable(core):
                        raise UnstableRestorationError("SPAN produced extreme pixel values.")
                    return
                source_core = _args[0][
                    ...,
                    halo : halo + tile.core_h,
                    halo : halo + tile.core_w,
                ]
                # Some unstable crops remain inside the nominal range but
                # introduce large coloured checker patterns on neutral scans.
                # Range checks alone miss them. A denoiser should not invent
                # this much colour or replace half the SDR signal in a pixel.
                added_chroma = (core.amax(dim=1) - core.amin(dim=1)) - (
                    source_core.amax(dim=1) - source_core.amin(dim=1)
                )
                if (
                    not torch.isfinite(core).all()
                    or core.amin() < -0.25
                    or core.amax() > 1.25
                    or (core - source_core).abs().amax() > 0.5
                    or added_chroma.amax() > 0.35
                    or _introduced_periodic_signal(core, source_core) > 4 / 255
                ):
                    raise UnstableRestorationError(
                        "NAFNet produced unstable SDR values or colour changes."
                    )

            hook = raw_model.register_forward_hook(validate_raw)
        try:
            with torch.no_grad():
                out_device = model(tile_input_device)
        finally:
            if hook is not None:
                hook.remove()

        if torch.isnan(out_device).any() or torch.isinf(out_device).any():
            raise ValueError("Model produced NaN or Infinity. Precision might be unsupported.")

        out_cpu = out_device.squeeze(0).to(torch.float32).cpu()

        scale = model_info.scale
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

        if float_output:
            return out_core.clamp(0, 1).numpy()
        out_core = torch.clamp(out_core, 0, 1) * 255.0
        return out_core.byte().numpy()

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
        float_output: bool = False,
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
        model = self.active_model
        if device is None or precision is None or model is None:
            raise RuntimeError("Model is not loaded. Call load_model() first.")

        _, h, w = img_tensor.shape
        scale = model_info.scale
        out_channels = model_info.out_channels
        out_shape = (out_channels, h * scale, w * scale)
        output_dtype = np.float32 if float_output else np.uint8
        out_array = np.zeros(out_shape, dtype=output_dtype)

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

                    out_core_np = self.run_tile(
                        t,
                        img_tensor,
                        model_info,
                        device,
                        precision,
                        halo,
                        model,
                        cancel_event=cancel_event,
                        **({"float_output": True} if float_output else {}),
                    )

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
                    out_array = np.zeros(out_shape, dtype=output_dtype)
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
        temporary_directory: str | None = None,
    ) -> OutputWriter:

        # load_model applies GPU memory limits and loads the model onto device.
        device, precision = self.load_model(model_path, device_str, precision_str, model_info)
        model = self.active_model

        img_tensor = img_data["tensor"]
        _, h, w = img_tensor.shape
        scale = model_info.scale

        writer = OutputWriter(
            (model_info.out_channels, h * scale, w * scale),
            dtype=np.uint8,
            temporary_directory=temporary_directory,
        )

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

                        out_core_np = self.run_tile(
                            t,
                            img_tensor,
                            model_info,
                            device,
                            precision,
                            halo,
                            model,
                            cancel_event=cancel_event,
                        )

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
                            (model_info.out_channels, h * scale, w * scale),
                            dtype=np.uint8,
                            temporary_directory=temporary_directory,
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
