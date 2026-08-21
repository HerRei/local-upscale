"""Clip windowing and overlap stitching for temporal video engines.

Pure array/index arithmetic, deliberately model-agnostic: the engine sees
clips of `window` frames whose neighbors share `overlap` frames, and the
stitcher crossfades those shared frames so clip boundaries never pop. All
functions are exercised by unit tests with fake models.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

import numpy as np


def clip_windows(total_frames: int, window: int, overlap: int) -> list[tuple[int, int]]:
    """Inclusive (start, end) frame ranges covering [0, total_frames).

    Consecutive windows share exactly `overlap` frames. The last window is
    anchored to the end of the sequence, so it may overlap its predecessor
    by more than `overlap` but never leaves a gap and never exceeds the
    sequence bounds.
    """
    if total_frames <= 0:
        return []
    if window <= 0:
        raise ValueError("window must be positive")
    if overlap < 0 or overlap >= window:
        raise ValueError("overlap must satisfy 0 <= overlap < window")
    if total_frames <= window:
        return [(0, total_frames - 1)]

    stride = window - overlap
    windows: list[tuple[int, int]] = []
    start = 0
    while True:
        end = start + window - 1
        if end >= total_frames - 1:
            windows.append((total_frames - window, total_frames - 1))
            return windows
        windows.append((start, end))
        start += stride


def crossfade_weights(overlap: int) -> np.ndarray:
    """Per-frame blend weights for the incoming clip across the overlap.

    Weight w[i] applies to the incoming clip's i-th overlap frame; the
    outgoing clip's matching frame gets 1 - w[i]. Weights rise linearly
    and are strictly inside (0, 1) so both clips always contribute.
    """
    if overlap <= 0:
        return np.zeros(0, dtype=np.float32)
    return (np.arange(1, overlap + 1, dtype=np.float32)) / (overlap + 1)


def stitch_clips(
    clips: Iterator[Sequence[np.ndarray]] | Iterator[np.ndarray],
    windows: Sequence[tuple[int, int]],
) -> Iterator[np.ndarray]:
    """Merge overlapping processed clips back into one frame stream.

    `clips` yields, per window, an array-like of frames matching that
    window's length. Shared frames are crossfaded with crossfade_weights;
    every source frame index is emitted exactly once, in order.
    """
    previous_frames: list[np.ndarray] | None = None
    previous_window: tuple[int, int] | None = None

    for window, clip in zip(windows, clips, strict=True):
        frames = [np.asarray(frame) for frame in clip]
        expected = window[1] - window[0] + 1
        if len(frames) != expected:
            raise ValueError(
                f"Clip for window {window} has {len(frames)} frames, expected {expected}."
            )
        if previous_frames is None:
            previous_frames = frames
            previous_window = window
            continue

        assert previous_window is not None
        shared = previous_window[1] - window[0] + 1
        if shared < 0:
            raise ValueError(
                f"Windows {previous_window} and {window} leave a gap; "
                "clip windows must overlap or touch."
            )

        # Emit the previous clip's frames that the new clip does not cover.
        keep = len(previous_frames) - shared
        yield from previous_frames[:keep]

        if shared > 0:
            weights = crossfade_weights(shared)
            blended = []
            for index in range(shared):
                outgoing = previous_frames[keep + index].astype(np.float32)
                incoming = frames[index].astype(np.float32)
                weight = float(weights[index])
                mixed = outgoing * (1.0 - weight) + incoming * weight
                blended.append(np.clip(mixed, 0, 255).astype(previous_frames[0].dtype))
            frames[:shared] = blended

        previous_frames = frames
        previous_window = window

    if previous_frames is not None:
        yield from previous_frames
