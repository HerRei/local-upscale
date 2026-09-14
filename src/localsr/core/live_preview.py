"""Bounded, non-blocking enhanced-frame preview encoding.

Inference submits at most one pending NumPy frame.  A daemon encoder owns JPEG
compression and transport emission, so a slow webview can never build an
unbounded frame queue or make the inference loop wait for preview encoding.
"""

from __future__ import annotations

import base64
import io
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from PIL import Image


@dataclass(frozen=True)
class PreviewPacket:
    job_id: str
    sequence: int
    preview_kind: str
    jpeg_base64: str
    output_x: int = 0
    output_y: int = 0
    output_width: int = 0
    output_height: int = 0
    image_width: int = 0
    image_height: int = 0
    frame_index: int = -1
    active_tile_size: int = 0
    stage_index: int = 0


@dataclass
class _PendingPreview:
    job_id: str
    sequence: int
    preview_kind: str
    pixels: np.ndarray
    metadata: dict[str, int]


def encode_preview_jpeg(pixels: np.ndarray, max_dimension: int, quality: int) -> str:
    """Encode CHW or HWC uint8 enhanced pixels as a bounded Base64 JPEG."""
    array = np.asarray(pixels)
    if array.ndim != 3:
        raise ValueError("preview pixels must be a three-dimensional array")
    if array.shape[0] in (1, 3, 4):
        if array.shape[0] == 1:
            array = np.repeat(array, 3, axis=0)
        array = array[:3].transpose(1, 2, 0)
    elif array.shape[2] in (1, 3, 4):
        if array.shape[2] == 1:
            array = np.repeat(array, 3, axis=2)
        array = array[:, :, :3]
    else:
        raise ValueError("preview pixels must contain one, three, or four channels")
    if array.dtype != np.uint8:
        array = np.clip(array, 0, 255).astype(np.uint8)
    image = Image.fromarray(np.ascontiguousarray(array), mode="RGB")
    maximum = max(64, min(640, int(max_dimension)))
    image.thumbnail((maximum, maximum), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=max(35, min(85, int(quality))), optimize=False)
    return base64.b64encode(output.getvalue()).decode("ascii")


class LatestPreviewEncoder:
    """Sample frames into a capacity-one queue and encode them off-thread."""

    def __init__(
        self,
        emit: Callable[[PreviewPacket], None],
        warn: Callable[[str], None] | None = None,
        *,
        enabled: bool = True,
        max_fps: float = 2.0,
        max_dimension: int = 320,
        quality: int = 68,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.enabled = enabled
        self._emit = emit
        self._warn = warn
        self._interval = 1.0 / max(0.1, min(5.0, max_fps))
        self._max_dimension = max_dimension
        self._quality = quality
        self._clock = clock
        self._last_submitted = float("-inf")
        self._sequence = 0
        self._queue: queue.Queue[_PendingPreview] = queue.Queue(maxsize=1)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def submit(
        self,
        *,
        job_id: str,
        preview_kind: str,
        pixels: np.ndarray,
        force: bool = False,
        **metadata: int,
    ) -> bool:
        if not self.enabled or self._stop.is_set():
            return False
        now = self._clock()
        if not force and now - self._last_submitted < self._interval:
            return False
        self._last_submitted = now
        self._sequence += 1
        pending = _PendingPreview(
            job_id=job_id,
            sequence=self._sequence,
            preview_kind=preview_kind,
            pixels=np.ascontiguousarray(pixels).copy(),
            metadata={key: int(value) for key, value in metadata.items()},
        )
        try:
            self._queue.put_nowait(pending)
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:  # pragma: no cover - another consumer won the race
                pass
            try:
                self._queue.put_nowait(pending)
            except queue.Full:  # pragma: no cover - bounded defensive race
                return False
        self._ensure_thread()
        return True

    def clear(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return

    def close(self) -> None:
        """Discard pending work immediately; never wait on the inference thread."""
        self._stop.set()
        self.clear()

    def _ensure_thread(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                pending = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                encoded = encode_preview_jpeg(
                    pending.pixels,
                    max_dimension=self._max_dimension,
                    quality=self._quality,
                )
                if self._stop.is_set():
                    continue
                self._emit(
                    PreviewPacket(
                        job_id=pending.job_id,
                        sequence=pending.sequence,
                        preview_kind=pending.preview_kind,
                        jpeg_base64=encoded,
                        **pending.metadata,
                    )
                )
            except (OSError, TypeError, ValueError) as error:
                if self._warn is not None and not self._stop.is_set():
                    self._warn(f"Live preview skipped a frame: {error}")
