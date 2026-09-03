"""Frame-by-frame video super-resolution pipeline.

This is the simple, flicker-prone pipeline: each frame is restored
independently using the existing image InferenceEngine. A future
VideoSRAdapter (RealBasicVSR/RVRT) will plug into the same loop and
replace the per-frame call with a multi-frame temporal call without
changing the decode/encode/cancel/progress plumbing here.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import numpy as np
import torch
from PIL import Image

from .face_compositing import blend_tile_outputs, classify_tile
from .face_detection import FaceMask, detect_faces, face_mask_for_tile_core
from .inference import InferenceEngine
from .tiling import generate_tiles
from .video_io import (
    decode_frames,
    encode_video,
    probe_video,
    uint8_chw_to_rgb_hwc,
)


def rgb_to_tensor(rgb: np.ndarray) -> torch.Tensor:
    """Convert an HxWx3 uint8 RGB array to a (3, H, W) float32 tensor in [0, 1]."""
    arr = rgb.astype(np.float32) / 255.0
    return torch.from_numpy(arr).permute(2, 0, 1)


def _deflicker_frames(
    restored: Iterator[np.ndarray],
    window: int,
    cancel_event: threading.Event,
) -> Iterator[np.ndarray]:
    """Temporal median de-flicker over a sliding window of restored frames.

    For each output frame, take the per-pixel median of the current frame
    and its (window - 1) neighbors. This removes per-frame flicker caused
    by independent restoration while preserving genuine motion, because
    a median ignores outliers but tracks the majority.

    The buffer holds at most `window` frames. Frames are emitted with a
    one-frame delay so the window is centered on the current frame. The
    first and last frames use a smaller window since they have fewer
    neighbors.
    """
    if window < 2:
        yield from restored
        return

    left = (window - 1) // 2
    right = window - 1 - left
    buffer: deque[tuple[int, np.ndarray]] = deque()
    next_emit = 0
    last_index = -1

    def median_for(index: int) -> np.ndarray:
        start = max(0, index - left)
        end = index + right
        selected = [frame for frame_index, frame in buffer if start <= frame_index <= end]
        return np.median(np.stack(selected, axis=0), axis=0).astype(np.uint8)

    for stream_index, frame in enumerate(restored):
        if cancel_event.is_set():
            raise InterruptedError("video job cancelled")
        buffer.append((stream_index, frame))
        last_index = stream_index
        while next_emit + right <= stream_index:
            yield median_for(next_emit)
            next_emit += 1
            minimum_needed = max(0, next_emit - left)
            while buffer and buffer[0][0] < minimum_needed:
                buffer.popleft()

    # Flush the final frames with a shrinking look-ahead window. The deque
    # remains bounded by `window`, even for multi-hour clips.
    while next_emit <= last_index:
        if cancel_event.is_set():
            raise InterruptedError("video job cancelled")
        yield median_for(next_emit)
        next_emit += 1
        minimum_needed = max(0, next_emit - left)
        while buffer and buffer[0][0] < minimum_needed:
            buffer.popleft()


@dataclass
class VideoJobConfig:
    video_path: str
    model_path: str
    output_video_path: str
    model_info: object
    device_str: str
    precision_str: str
    tile_size: int
    halo: int
    safe_memory: bool
    container: str = "mp4"
    crf: int = 18
    start_frame: int | None = None
    end_frame: int | None = None
    fps_override: float | None = None
    face_model_path: str | None = None
    face_model_info: object | None = None
    face_detection_interval: int = 5
    deflicker: bool = False
    deflicker_window: int = 3
    output_scale: int | None = None
    face_fidelity: float = 0.7


@dataclass
class VideoJobResult:
    output_path: str
    frames_processed: int
    elapsed_seconds: float
    inference_seconds: float


def run_video_job(
    config: VideoJobConfig,
    engine: InferenceEngine,
    cancel_event: threading.Event,
    frame_started_cb: Callable[[int, int], None] | None = None,
    frame_completed_cb: Callable[[int, int, str], None] | None = None,
    enhanced_frame_cb: Callable[[int, int, np.ndarray], None] | None = None,
    progress_cb: Callable[[int, int, float], None] | None = None,
    tile_callback: Callable[..., None] | None = None,
) -> VideoJobResult:
    """Run a video upscale job end-to-end.

    frame_started_cb(frame_index, total_frames) and
    frame_completed_cb(frame_index, total_frames, jpeg_b64) are called
    per frame. progress_cb(frames_done, total_frames, elapsed_seconds)
    is called after each frame. All callbacks are optional.
    """
    job_started_at = time.monotonic()
    inference_started_at: float | None = None

    probe = probe_video(config.video_path)
    fps = config.fps_override if config.fps_override else probe.fps
    if fps <= 0:
        fps = 25.0

    total_frames = probe.frame_count
    if total_frames <= 0 and config.end_frame is not None:
        total_frames = int(config.end_frame) + 1
    if config.start_frame is not None:
        total_frames = max(0, total_frames - int(config.start_frame))
    if config.end_frame is not None and config.start_frame is not None:
        total_frames = int(config.end_frame) - int(config.start_frame) + 1
    if total_frames <= 0:
        # Without a reliable count we still proceed; the UI shows "?" until
        # the first frame lands.
        total_frames = 0

    model_info = config.model_info
    native_scale = int(getattr(model_info, "scale", 1))
    output_scale = int(config.output_scale or native_scale)
    if output_scale < 1 or output_scale > native_scale:
        raise ValueError(
            f"Video output scale must be between 1 and the model's native {native_scale}× scale."
        )
    output_width = probe.width * output_scale
    output_height = probe.height * output_scale

    # Load the model once and reuse it for every frame.
    engine.load_model(
        config.model_path,
        config.device_str,
        config.precision_str,
        # safe_memory is enforced at the engine level via model_info.
        _SafeModelInfo(model_info, config.safe_memory),
    )
    # If face-aware mode is enabled, also pre-load the face model.
    use_face_aware = config.face_model_path is not None and config.face_model_info is not None
    if use_face_aware:
        engine.load_model(
            config.face_model_path,
            config.device_str,
            config.precision_str,
            _SafeModelInfo(config.face_model_info, config.safe_memory),
        )

    frames_processed = 0
    cached_face_mask: FaceMask | None = None
    frames_since_detection = 0

    def frame_generator() -> Iterator[np.ndarray]:
        nonlocal inference_started_at
        nonlocal frames_processed
        nonlocal cached_face_mask
        nonlocal frames_since_detection

        decoded = decode_frames(
            config.video_path,
            start_frame=config.start_frame,
            end_frame=config.end_frame,
        )
        for output_frame_index, (_source_frame_index, rgb) in enumerate(decoded):
            if cancel_event.is_set():
                raise InterruptedError("video job cancelled")
            if inference_started_at is None:
                inference_started_at = time.monotonic()
            if frame_started_cb is not None:
                frame_started_cb(output_frame_index, total_frames)

            tensor = rgb_to_tensor(rgb)

            def tile_progress(completed: int, total: int, active_tile: int) -> None:
                pass

            try:
                if use_face_aware:
                    # Run face detection every N frames and cache the mask.
                    if (
                        cached_face_mask is None
                        or frames_since_detection >= config.face_detection_interval
                    ):
                        cached_face_mask = detect_faces(rgb, cancel_event=cancel_event)
                        frames_since_detection = 0
                    else:
                        frames_since_detection += 1

                    if cached_face_mask.has_faces:
                        out_chw = process_frame_face_aware(
                            engine=engine,
                            img_tensor=tensor,
                            face_mask=cached_face_mask,
                            general_model_path=config.model_path,
                            face_model_path=config.face_model_path,
                            general_model_info=model_info,
                            face_model_info=config.face_model_info,
                            device_str=config.device_str,
                            precision_str=config.precision_str,
                            tile_size=config.tile_size,
                            halo=config.halo,
                            cancel_event=cancel_event,
                            safe_memory=config.safe_memory,
                            face_fidelity=config.face_fidelity,
                        )
                    else:
                        # No faces detected — use the general model.
                        out_chw = engine.process_frame(
                            img_tensor=tensor,
                            model_info=model_info,
                            tile_size=config.tile_size,
                            halo=config.halo,
                            cancel_event=cancel_event,
                            progress_callback=tile_progress,
                            safe_memory=config.safe_memory,
                            tile_callback=tile_callback,
                        )
                else:
                    out_chw = engine.process_frame(
                        img_tensor=tensor,
                        model_info=model_info,
                        tile_size=config.tile_size,
                        halo=config.halo,
                        cancel_event=cancel_event,
                        progress_callback=tile_progress,
                        safe_memory=config.safe_memory,
                        tile_callback=tile_callback,
                    )
            except InterruptedError:
                raise

            out_rgb = uint8_chw_to_rgb_hwc(out_chw)
            if output_scale != native_scale:
                out_rgb = np.array(
                    Image.fromarray(out_rgb).resize(
                        (output_width, output_height),
                        Image.Resampling.LANCZOS,
                    ),
                    dtype=np.uint8,
                )
            frames_processed += 1

            if enhanced_frame_cb is not None:
                enhanced_frame_cb(output_frame_index, total_frames, out_rgb)
            if frame_completed_cb is not None:
                frame_completed_cb(output_frame_index, total_frames, "")

            if progress_cb is not None:
                elapsed = time.monotonic() - job_started_at
                progress_cb(frames_processed, total_frames, elapsed)

            yield out_rgb

    # Apply temporal de-flicker if enabled. The median filter wraps the
    # restored-frame generator so the encoder receives smoothed frames.
    if config.deflicker:
        output_frames = _deflicker_frames(frame_generator(), config.deflicker_window, cancel_event)
    else:
        output_frames = frame_generator()

    # The encoder consumes the generator directly so frames never accumulate
    # in memory beyond what PyAV's internal buffers hold. Compatible audio and
    # subtitle packets are trimmed and shifted onto the enhanced timeline.
    # A deliberate frame-rate override changes video speed, so passthrough
    # streams are omitted rather than silently producing drift.
    source_fps = probe.fps or fps
    rate_unchanged = config.fps_override is None or abs(float(fps) - float(source_fps)) < 1e-6
    source_start_seconds = max(0, int(config.start_frame or 0)) / source_fps
    encode_video(
        output_frames,
        config.output_video_path,
        fps=fps,
        container_format=config.container,
        crf=config.crf,
        width=output_width,
        height=output_height,
        audio_source=config.video_path if rate_unchanged else None,
        source_start_seconds=source_start_seconds,
    )

    completed_at = time.monotonic()
    return VideoJobResult(
        output_path=config.output_video_path,
        frames_processed=frames_processed,
        elapsed_seconds=completed_at - job_started_at,
        inference_seconds=(completed_at - (inference_started_at or completed_at)),
    )


def process_frame_face_aware(
    engine: InferenceEngine,
    img_tensor: torch.Tensor,
    face_mask: FaceMask,
    general_model_path: str,
    face_model_path: str,
    general_model_info,
    face_model_info,
    device_str: str,
    precision_str: str,
    tile_size: int,
    halo: int,
    cancel_event: threading.Event,
    safe_memory: bool = True,
    face_fidelity: float = 0.7,
    face_threshold_high: float = 0.85,
    face_threshold_low: float = 0.15,
    progress_callback: Callable[[int, int, int], None] | None = None,
    tile_callback: Callable[..., None] | None = None,
) -> np.ndarray:
    """Process a single frame with per-tile model selection.

    Tiles whose core overlaps a face box >= face_threshold_high are run
    through the face model. Tiles with overlap <= face_threshold_low use
    the general model. Boundary tiles run both and are alpha-blended.

    Both models must share the same scale and output channels. Since the
    current face checkpoint has no native fidelity input, ``face_fidelity``
    linearly blends the restored result with a bicubic reconstruction of the
    original inside the smoothed face mask. Non-face pixels keep the general
    model result.

    progress_callback and tile_callback are optional — when provided,
    they receive the same per-tile notifications as the standard
    process_frame path so the GUI's progress bar and progressive
    preview work during face-aware restoration.
    """
    import gc

    if int(face_model_info.scale) != int(general_model_info.scale):
        raise ValueError("Face and primary checkpoints must have the same native scale.")
    if int(face_model_info.out_channels) != int(general_model_info.out_channels):
        raise ValueError("Face and primary checkpoints must have compatible output channels.")

    _, h, w = img_tensor.shape
    scale = general_model_info.scale
    out_channels = general_model_info.out_channels
    out_shape = (out_channels, h * scale, w * scale)
    out_array = np.zeros(out_shape, dtype=np.uint8)

    # Load both models once. Subsequent switching via switch_model is
    # instant — no torch reload, no VRAM spike.
    general_safe = _SafeModelInfo(general_model_info, safe_memory)
    face_safe = _SafeModelInfo(face_model_info, safe_memory)
    engine.load_model(general_model_path, device_str, precision_str, general_safe)
    engine.load_model(face_model_path, device_str, precision_str, face_safe)

    device = engine._loaded_device
    precision = engine._loaded_precision

    tiles = list(generate_tiles(w, h, tile_size, halo, scale))
    total_tiles = len(tiles)

    def identity_core_for(tile) -> np.ndarray:
        """Return the source core bicubic-resampled to the model output size."""
        import torch.nn.functional as functional

        source = img_tensor[
            :,
            tile.core_y : tile.core_y + tile.core_h,
            tile.core_x : tile.core_x + tile.core_w,
        ].unsqueeze(0)
        resized = functional.interpolate(
            source.float(),
            size=(tile.out_h, tile.out_w),
            mode="bicubic",
            align_corners=False,
            antialias=True,
        )
        return resized.squeeze(0).clamp(0, 1).mul(255).round().byte().cpu().numpy()

    for i, tile in enumerate(tiles):
        if cancel_event.is_set():
            raise InterruptedError("Cancelled")

        # Compute face overlap for this tile's core.
        alpha = face_mask_for_tile_core(
            face_mask.mask, tile.core_x, tile.core_y, tile.core_w, tile.core_h
        )
        tile_class = classify_tile(alpha, face_threshold_high, face_threshold_low)

        if tile_callback is not None:
            tile_callback("started", tile, None, i, total_tiles, w * scale, h * scale, tile_size)

        if tile_class == "face":
            engine.switch_model(general_model_path)
            general_core = engine.run_tile(
                tile,
                img_tensor,
                general_model_info,
                device,
                precision,
                halo,
                engine.active_model,
            )
            identity_core = identity_core_for(tile)
            if face_fidelity <= 0.0:
                core_output = blend_tile_outputs(
                    identity_core,
                    general_core,
                    alpha,
                    fidelity=0.0,
                    identity_output=identity_core,
                )
            else:
                engine.switch_model(face_model_path)
                face_core = engine.run_tile(
                    tile, img_tensor, face_model_info, device, precision, halo, engine.active_model
                )
                core_output = blend_tile_outputs(
                    face_core,
                    general_core,
                    alpha,
                    fidelity=face_fidelity,
                    identity_output=identity_core,
                )
        elif tile_class == "general":
            engine.switch_model(general_model_path)
            core_output = engine.run_tile(
                tile, img_tensor, general_model_info, device, precision, halo, engine.active_model
            )
        else:  # boundary
            engine.switch_model(general_model_path)
            general_core = engine.run_tile(
                tile, img_tensor, general_model_info, device, precision, halo, engine.active_model
            )
            engine.switch_model(face_model_path)
            face_core = engine.run_tile(
                tile, img_tensor, face_model_info, device, precision, halo, engine.active_model
            )
            core_output = blend_tile_outputs(
                face_core,
                general_core,
                alpha,
                fidelity=face_fidelity,
                identity_output=identity_core_for(tile),
            )

        out_array[:, tile.out_y : tile.out_y + tile.out_h, tile.out_x : tile.out_x + tile.out_w] = (
            core_output
        )

        if tile_callback is not None:
            tile_callback(
                "completed", tile, core_output, i + 1, total_tiles, w * scale, h * scale, tile_size
            )

        if progress_callback is not None:
            progress_callback(i + 1, total_tiles, tile_size)

        if safe_memory:
            import torch as _torch

            if device is not None:
                if device.type == "mps":
                    _torch.mps.empty_cache()
                elif device.type == "cuda":
                    _torch.cuda.empty_cache()
                elif device.type == "xpu":
                    _torch.xpu.empty_cache()
            gc.collect()

    return out_array


@dataclass
class _SafeModelInfo:
    """Wraps a NormalizedModelInfo to expose safe_memory to InferenceEngine.

    InferenceEngine.load_model reads model_info.safe_memory to decide the
    GPU memory ceiling. NormalizedModelInfo does not carry that field, so
    we wrap it transparently for the video path only.
    """

    inner: object
    safe_memory: bool

    def __getattr__(self, name):
        return getattr(self.inner, name)
