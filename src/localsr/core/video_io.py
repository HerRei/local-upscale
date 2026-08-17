"""PyAV-based video decode and encode for the worker.

All ffmpeg work happens here so the worker never shells out to a binary.
The encoder uses an open temp file and an atomic rename at the end so a
crashed or cancelled job never leaves a half-written output at the final
path.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from fractions import Fraction

import av
import numpy as np
from PIL import Image


@dataclass(frozen=True)
class VideoProbe:
    width: int
    height: int
    fps: float
    frame_count: int
    codec: str
    duration_seconds: float


def probe_video(path: str) -> VideoProbe:
    """Probe a video container without decoding frames."""
    with av.open(path) as container:
        stream = next((s for s in container.streams if s.type == "video"), None)
        if stream is None:
            raise ValueError(f"No video stream found in {path}")
        avg_fps = stream.average_rate
        fps = float(avg_fps) if avg_fps else 0.0
        frame_count = int(stream.frames or 0)
        if frame_count == 0 and container.duration:
            duration_sec = float(container.duration) / 1_000_000.0
            frame_count = int(round(duration_sec * fps)) if fps else 0
        duration = float(container.duration) / 1_000_000.0 if container.duration else 0.0
        return VideoProbe(
            width=int(stream.width or 0),
            height=int(stream.height or 0),
            fps=fps,
            frame_count=frame_count,
            codec=str(stream.codec_context.name or "unknown"),
            duration_seconds=duration,
        )


def decode_frames(
    path: str,
    start_frame: int | None = None,
    end_frame: int | None = None,
):
    """Yield (frame_index, rgb_uint8_HxWx3) for every decoded frame.

    Frames are converted to RGB on decode. start_frame/end_frame are
    inclusive 0-indexed bounds; None means unbounded on that side.
    """
    start = max(0, int(start_frame)) if start_frame is not None else 0
    with av.open(path) as container:
        stream = next((s for s in container.streams if s.type == "video"), None)
        if stream is None:
            raise ValueError(f"No video stream in {path}")
        try:
            stream.thread_type = "FRAME"
        except (KeyError, ValueError):
            pass
        index = 0
        for frame in container.decode(stream):
            if index < start:
                index += 1
                continue
            if end_frame is not None and index > end_frame:
                break
            rgb = frame.to_ndarray(format="rgb24")
            yield index, rgb
            index += 1


def encode_video(
    frames,
    destination_path: str,
    *,
    fps: float,
    container_format: str = "mp4",
    codec: str = "libx264",
    crf: int = 18,
    width: int,
    height: int,
    pixel_format: str = "yuv420p",
) -> str:
    """Encode an iterable of (rgb_uint8_HxWx3) frames into a video file.

    Writes to destination_path + ".tmp" and atomically renames on success.
    On any exception the temp file is removed and the destination is never
    created.
    """
    tmp_path = destination_path + ".tmp"
    output_container = av.open(tmp_path, mode="w", format=container_format)
    fps_fraction = Fraction(int(fps * 1000), 1000) if fps else Fraction(25, 1)
    stream = output_container.add_stream(codec, rate=fps_fraction)
    stream.width = int(width)
    stream.height = int(height)
    stream.pix_fmt = pixel_format
    stream.options = {"crf": str(max(0, min(51, int(crf)))), "preset": "medium"}

    try:
        for rgb in frames:
            if rgb.dtype != np.uint8:
                rgb = np.clip(rgb, 0, 255).astype(np.uint8)
            frame = av.VideoFrame.from_ndarray(rgb, format="rgb24")
            for packet in stream.encode(frame):
                output_container.mux(packet)
        for packet in stream.encode():
            output_container.mux(packet)
        output_container.close()
        os.replace(tmp_path, destination_path)
        return destination_path
    except BaseException:
        try:
            output_container.close()
        except Exception:
            pass
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise


def uint8_chw_to_rgb_hwc(array_chw: np.ndarray) -> np.ndarray:
    """Convert a (C, H, W) uint8 array to an HxWx3 uint8 RGB array.

    Single-channel inputs are repeated to 3 channels so the encoder always
    gets RGB.
    """
    if array_chw.ndim != 3:
        raise ValueError("Expected a (C, H, W) array.")
    if array_chw.shape[0] == 1:
        array_chw = np.repeat(array_chw, 3, axis=0)
    if array_chw.shape[0] < 3:
        raise ValueError("Expected at least one or three channels.")
    return np.ascontiguousarray(array_chw[:3].transpose(1, 2, 0))


def thumbnail_jpeg(rgb: np.ndarray, max_dimension: int = 192, quality: int = 72) -> bytes:
    """Produce a small JPEG bytes blob for a frame thumbnail."""
    image = Image.fromarray(rgb)
    image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
    from io import BytesIO

    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=quality, optimize=True)
    return buffer.getvalue()
