"""Frame-by-frame video super-resolution pipeline.

This is the simple, flicker-prone pipeline: each frame is restored
independently using the existing image InferenceEngine. A future
VideoSRAdapter (RealBasicVSR/RVRT) will plug into the same loop and
replace the per-frame call with a multi-frame temporal call without
changing the decode/encode/cancel/progress plumbing here.
"""

from __future__ import annotations

import base64
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import numpy as np
import torch

from .inference import InferenceEngine
from .video_io import (
    decode_frames,
    encode_video,
    probe_video,
    thumbnail_jpeg,
    uint8_chw_to_rgb_hwc,
)


def rgb_to_tensor(rgb: np.ndarray) -> torch.Tensor:
    """Convert an HxWx3 uint8 RGB array to a (3, H, W) float32 tensor in [0, 1]."""
    arr = rgb.astype(np.float32) / 255.0
    return torch.from_numpy(arr).permute(2, 0, 1)


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
    scale = int(getattr(model_info, "scale", 1))
    output_width = probe.width * scale
    output_height = probe.height * scale

    # Load the model once and reuse it for every frame.
    engine.load_model(
        config.model_path,
        config.device_str,
        config.precision_str,
        # safe_memory is enforced at the engine level via model_info.
        _SafeModelInfo(model_info, config.safe_memory),
    )

    frames_processed = 0

    def frame_generator() -> Iterator[np.ndarray]:
        nonlocal inference_started_at
        nonlocal frames_processed

        for frame_index, rgb in decode_frames(
            config.video_path,
            start_frame=config.start_frame,
            end_frame=config.end_frame,
        ):
            if cancel_event.is_set():
                return
            if inference_started_at is None:
                inference_started_at = time.monotonic()
            if frame_started_cb is not None:
                frame_started_cb(frame_index, total_frames)

            tensor = rgb_to_tensor(rgb)

            def tile_progress(completed: int, total: int, active_tile: int) -> None:
                # Per-tile progress is intentionally not forwarded to the GUI
                # for video jobs — frame-level progress is enough and avoids
                # flooding the IPC channel.
                pass

            try:
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
                return

            out_rgb = uint8_chw_to_rgb_hwc(out_chw)
            frames_processed += 1

            if frame_completed_cb is not None:
                thumb_b64 = ""
                try:
                    thumb_bytes = thumbnail_jpeg(out_rgb, max_dimension=192, quality=72)
                    thumb_b64 = base64.b64encode(thumb_bytes).decode("ascii")
                except (OSError, ValueError):
                    thumb_b64 = ""
                frame_completed_cb(frame_index, total_frames, thumb_b64)

            if progress_cb is not None:
                elapsed = time.monotonic() - job_started_at
                progress_cb(frames_processed, total_frames, elapsed)

            yield out_rgb

    # The encoder consumes the generator directly so frames never accumulate
    # in memory beyond what PyAV's internal buffers hold.
    encode_video(
        frame_generator(),
        config.output_video_path,
        fps=fps,
        container_format=config.container,
        crf=config.crf,
        width=output_width,
        height=output_height,
    )

    completed_at = time.monotonic()
    return VideoJobResult(
        output_path=config.output_video_path,
        frames_processed=frames_processed,
        elapsed_seconds=completed_at - job_started_at,
        inference_seconds=(completed_at - (inference_started_at or completed_at)),
    )


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
