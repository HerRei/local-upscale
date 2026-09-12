"""LocalSR adapter around the vendored SeedVR2 one-step diffusion upscaler.

Drives the upstream 4-phase pipeline (encode → upscale → decode →
postprocess) exactly the way its reference CLI does, with three LocalSR
specifics: cancellation is injected through the pipeline's interrupt hook,
frames arrive and leave as uint8 numpy arrays, and the model context is
built once and reused across streamed chunks of one job.
"""

from __future__ import annotations

import gc
import threading
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import torch
from PIL import Image

from localsr.core.model_catalog import VIDEO_CATALOG_BY_ID
from localsr.core.video_memory import seed_memory_plan

from .vendor.core.generation_phases import (
    decode_all_batches,
    encode_all_batches,
    postprocess_all_batches,
    upscale_all_batches,
)
from .vendor.core.generation_utils import (
    compute_generation_info,
    load_text_embeddings,
    prepare_runner,
    setup_generation_context,
)
from .vendor.utils.debug import Debug

# The vendored tree computes its "repo root" three directory levels above
# vendor/utils/constants.py, which is this package directory: configs_3b/,
# configs_7b/, and the text embeddings live here, beside vendor/.
PACKAGE_DIR = str(Path(__file__).parent)
VAE_FILENAME = "ema_vae_fp16.safetensors"
# Preference order among DiT weights that may be present in a bundle.
DIT_PREFERENCE = (
    "seedvr2_ema_3b_fp16.safetensors",
    "seedvr2_ema_3b_fp8_e4m3fn.safetensors",
)


class SeedVR2CancelledError(InterruptedError):
    pass


def _select_dit(bundle_dir: Path) -> str:
    for filename in DIT_PREFERENCE:
        if (bundle_dir / filename).is_file():
            return filename
    for candidate in sorted(bundle_dir.glob("seedvr2_ema_*.safetensors")):
        return candidate.name
    raise FileNotFoundError(
        f"No SeedVR2 DiT checkpoint found in {bundle_dir}. Download the model bundle first."
    )


class SeedVR2Engine:
    """One job's worth of SeedVR2 state: context, runner, text embeddings."""

    def __init__(
        self,
        bundle_dir: str,
        device: str,
        precision: str,
        *,
        debug: bool = False,
        low_memory: bool = True,
        model_id: str = "",
    ) -> None:
        del precision  # The checkpoint's own dtype governs; MPS converts in-place.
        self.bundle_dir = Path(bundle_dir)
        self.device = "mps" if device.startswith("mps") else device
        plan = seed_memory_plan(self.device, low_memory)
        self.max_temporal_window = plan.clip_frames
        self.debug = Debug(enabled=debug)
        if model_id:
            model = VIDEO_CATALOG_BY_ID[model_id]
            self.dit_model = next(file.filename for file in model.files if file.role == "dit")
            if not (self.bundle_dir / self.dit_model).is_file():
                raise FileNotFoundError(
                    f"The selected SeedVR2 checkpoint is missing: {self.dit_model}"
                )
        else:
            self.dit_model = _select_dit(self.bundle_dir)
        vae_tile = plan.vae_tile_size

        self.ctx = setup_generation_context(
            dit_device=self.device,
            vae_device=self.device,
            # Loading via CPU also avoids registering the entire SafeTensors
            # file as one large MPS allocation before the model can run.
            dit_offload_device="cpu" if self.device != "cpu" else None,
            # Do not keep the VAE resident alongside the 3B diffusion model.
            vae_offload_device="cpu" if self.device != "cpu" else None,
            tensor_offload_device="cpu" if plan.offload_tensors else None,
            debug=self.debug,
        )
        runner, cache_context = prepare_runner(
            dit_model=self.dit_model,
            vae_model=VAE_FILENAME,
            model_dir=str(self.bundle_dir),
            debug=self.debug,
            ctx=self.ctx,
            dit_cache=False,
            vae_cache=False,
            dit_id=None,
            vae_id=None,
            block_swap_config={"blocks_to_swap": plan.blocks_to_swap, "swap_io_components": True}
            if plan.blocks_to_swap
            else None,
            encode_tiled=True,
            encode_tile_size=(vae_tile, vae_tile),
            encode_tile_overlap=(vae_tile // 8, vae_tile // 8),
            decode_tiled=True,
            decode_tile_size=(vae_tile, vae_tile),
            decode_tile_overlap=(vae_tile // 8, vae_tile // 8),
            tile_debug="false",
            attention_mode="sdpa",
            torch_compile_args_dit=None,
            torch_compile_args_vae=None,
        )
        self.runner = runner
        self.ctx["cache_context"] = cache_context
        self.ctx["text_embeds"] = load_text_embeddings(
            PACKAGE_DIR, self.ctx["dit_device"], self.ctx["compute_dtype"], self.debug
        )

    @staticmethod
    def prepare_frame(frame: np.ndarray, resolution: int) -> np.ndarray:
        """Honor a smaller requested output before buffering or uploading frames.

        Upstream resizes after uploading the clip to the GPU. A CPU downsample
        first avoids retaining and uploading 4K inputs when choosing 512p.
        """
        height, width = frame.shape[:2]
        if resolution <= 0 or min(height, width) <= resolution:
            return frame
        ratio = resolution / min(height, width)
        size = (max(2, int(width * ratio) // 2 * 2), max(2, int(height * ratio) // 2 * 2))
        return np.array(Image.fromarray(frame).resize(size, Image.Resampling.LANCZOS))

    def _release_phase_cache(self) -> None:
        if self.device == "mps":
            # Offloaded weights and the preceding phase's kernels must finish
            # before the next VAE/diffusion allocation uses the same budget.
            torch.mps.synchronize()
            gc.collect()
            torch.mps.empty_cache()

    @contextmanager
    def _observe_tiles(self, callback, report):
        """Report actual VAE work, clipped to the unpadded exported frame."""
        previous = getattr(self.debug, "tile_callback", None)

        def observe(stage, phase, done, total, x, y, width, height, image_w, image_h):
            if phase == "completed" and self.device == "mps":
                torch.mps.synchronize()
            elif phase == "completed" and self.device.startswith("cuda"):
                torch.cuda.synchronize(self.device)
            report(stage, done, total)
            true_h, true_w = self.ctx.get("true_target_dims", (image_h, image_w))
            width, height = min(width, true_w - x), min(height, true_h - y)
            if callback is not None and width > 0 and height > 0:
                callback(stage, phase, done, total, x, y, width, height, true_w, true_h)

        self.debug.tile_callback = observe
        try:
            yield
        finally:
            self.debug.tile_callback = previous

    @torch.inference_mode()
    def process_frames(
        self,
        frames: list[np.ndarray],
        **kwargs,
    ) -> list[np.ndarray]:
        # Upstream's clip-level interrupt checks can be minutes apart with
        # large VAE tiles. Check before each torch module, including DiT blocks
        # and VAE layers, even when visual previews are disabled. Global hooks
        # also cover modules loaded lazily by the upstream phase runner.
        cancel_event = kwargs.get("cancel_event")
        owner_thread = threading.get_ident()

        def interrupt(_module=None, _inputs=None):
            if (
                threading.get_ident() == owner_thread
                and cancel_event is not None
                and cancel_event.is_set()
            ):
                raise SeedVR2CancelledError("Video cancelled")

        interrupt()
        handle = torch.nn.modules.module.register_module_forward_pre_hook(interrupt)
        try:
            return self._process_frames(frames, **kwargs)
        finally:
            handle.remove()

    def _process_frames(
        self,
        frames: list[np.ndarray],
        *,
        resolution: int,
        batch_size: int = 5,
        temporal_overlap: int = 0,
        seed: int = 42,
        cancel_event: threading.Event | None = None,
        progress_callback: Callable[[str, int, int], None] | None = None,
        tile_callback: Callable[..., None] | None = None,
    ) -> list[np.ndarray]:
        """Upscale uint8 HxWx3 frames; returns uint8 frames at the target size.

        `resolution` is the target shortest-edge in pixels (SeedVR2 has no
        fixed scale factor). `batch_size` should satisfy 4n+1; other values
        work but waste compute on mirror padding. Cancellation raises
        SeedVR2CancelledError between pipeline batches.
        """
        if cancel_event is not None:

            def interrupt() -> None:
                if cancel_event.is_set():
                    raise SeedVR2CancelledError()

            self.ctx["interrupt_fn"] = interrupt
        else:
            self.ctx["interrupt_fn"] = None

        def report(stage: str, completed: int = 0, total: int = 0) -> None:
            if cancel_event is not None and cancel_event.is_set():
                raise SeedVR2CancelledError()
            if progress_callback is not None:
                progress_callback(stage, completed, total)

        def phase_callback(stage: str):
            return lambda current, total, _frames, _name: report(stage, current, total)

        # Upstream releases these after diffusion; reload for subsequent clips.
        if not self.ctx.get("text_embeds"):
            self.ctx["text_embeds"] = load_text_embeddings(
                PACKAGE_DIR, self.ctx["dit_device"], self.ctx["compute_dtype"], self.debug
            )
        self.ctx["final_video"] = None
        report("preparing_clip")
        stacked = np.stack([self.prepare_frame(frame, resolution) for frame in frames]).astype(
            np.float32
        )
        stacked /= 255.0
        tensor = torch.from_numpy(stacked)

        tensor, _info = compute_generation_info(
            ctx=self.ctx,
            images=tensor,
            resolution=resolution,
            max_resolution=0,
            batch_size=batch_size,
            uniform_batch_size=False,
            seed=seed,
            prepend_frames=0,
            temporal_overlap=temporal_overlap,
            debug=self.debug,
        )
        report("encoding")
        with self._observe_tiles(tile_callback, report):
            ctx = encode_all_batches(
                self.runner,
                ctx=self.ctx,
                images=tensor,
                debug=self.debug,
                batch_size=batch_size,
                uniform_batch_size=False,
                seed=seed,
                progress_callback=phase_callback("encoding"),
                temporal_overlap=temporal_overlap,
                resolution=resolution,
                max_resolution=0,
                input_noise_scale=0.0,
                color_correction="lab",
            )
        self._release_phase_cache()
        report("enhancing")
        if tile_callback is not None:
            height, width = ctx["true_target_dims"]
            # DiT processes the whole clip, rather than independent image tiles.
            tile_callback("enhancing", "started", 0, 1, 0, 0, width, height, width, height)
        ctx = upscale_all_batches(
            self.runner,
            ctx=ctx,
            debug=self.debug,
            progress_callback=phase_callback("enhancing"),
            seed=seed,
            latent_noise_scale=0.0,
            # Retain this job's structure on CPU between phases/clips. With
            # False upstream destroys runner.dit/vae after the first clip.
            cache_model=True,
        )
        self._release_phase_cache()
        if tile_callback is not None:
            tile_callback("enhancing", "completed", 1, 1, 0, 0, width, height, width, height)
        report("decoding")
        with self._observe_tiles(tile_callback, report):
            ctx = decode_all_batches(
                self.runner,
                ctx=ctx,
                debug=self.debug,
                progress_callback=phase_callback("decoding"),
                cache_model=True,
            )
        self._release_phase_cache()
        report("finishing")
        ctx = postprocess_all_batches(
            ctx=ctx,
            debug=self.debug,
            progress_callback=phase_callback("finishing"),
            color_correction="lab",
            prepend_frames=0,
            temporal_overlap=temporal_overlap,
            batch_size=batch_size,
        )
        result = ctx["final_video"]
        if result.device.type != "cpu":
            result = result.cpu()
        result = result.to(torch.float32).clamp_(0.0, 1.0)
        output = (result * 255.0).round().to(torch.uint8).numpy()
        ctx["final_video"] = None
        return [np.ascontiguousarray(frame) for frame in output]
