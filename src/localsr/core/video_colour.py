"""Bounded, shot-aware chroma propagation for colourising video."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator

import cv2
import numpy as np


def _small(frame: np.ndarray) -> np.ndarray:
    height, width = frame.shape[:2]
    scale = min(1.0, 512 / max(height, width))
    size = (max(8, round(width * scale)), max(8, round(height * scale)))
    return cv2.resize(frame, size, interpolation=cv2.INTER_AREA)


def _luma(frame: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(_small(frame), cv2.COLOR_RGB2LAB)[:, :, 0]


def _cut_luma(a: np.ndarray, b: np.ndarray) -> bool:
    difference = np.abs(a.astype(np.float32) - b.astype(np.float32)).mean()
    hist_a = cv2.calcHist([a], [0], None, [32], [0, 256])
    hist_b = cv2.calcHist([b], [0], None, [32], [0, 256])
    hist_a /= a.size
    hist_b /= b.size
    return bool(difference > 90 or (difference > 35 and np.abs(hist_a - hist_b).sum() > 0.6))


def _flow(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_FAST).calc(a, b, None)


def _warp(image: np.ndarray, backward: np.ndarray) -> np.ndarray:
    height, width = backward.shape[:2]
    x, y = np.meshgrid(np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32))
    return cv2.remap(
        image,
        x + backward[:, :, 0],
        y + backward[:, :, 1],
        cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    )


def _inconsistent(forward: np.ndarray, backward: np.ndarray) -> np.ndarray:
    error = np.linalg.norm(backward + _warp(forward, backward), axis=2)
    height, width = backward.shape[:2]
    x, y = np.meshgrid(np.arange(width), np.arange(height))
    target_x, target_y = x + backward[:, :, 0], y + backward[:, :, 1]
    outside = (target_x < 0) | (target_x > width - 1) | (target_y < 0) | (target_y > height - 1)
    return outside | (error > (1.5 + 0.05 * np.linalg.norm(backward, axis=2)))


def _blend_chroma(
    forward: np.ndarray, backward: np.ndarray, weight: float, invalid: np.ndarray
) -> np.ndarray:
    """Blend valid flow and feather the nearest propagated colour over occlusions."""
    blended = forward * (1 - weight) + backward * weight
    mask = cv2.GaussianBlur(invalid.astype(np.float32), (5, 5), 0)[:, :, None]
    nearest = forward if weight <= 0.5 else backward
    return blended * (1 - mask) + nearest * mask


def _render_mapped(
    frame: np.ndarray,
    first: np.ndarray,
    forward_map: np.ndarray,
    last: np.ndarray | None,
    backward_map: np.ndarray | None,
    weight: float,
    invalid: np.ndarray | None,
    smoothing: np.ndarray,
) -> np.ndarray:
    """Upsample flow and sample keyframe chroma in strips to bound working memory."""
    lab = cv2.cvtColor(frame, cv2.COLOR_RGB2LAB)
    height, width = frame.shape[:2]
    small_height, small_width = first.shape[:2]
    x = (np.arange(width, dtype=np.float32) + 0.5) * small_width / width - 0.5
    feather = None if invalid is None else cv2.GaussianBlur(invalid.astype(np.float32), (5, 5), 0)
    for top in range(0, height, 128):
        bottom = min(height, top + 128)
        y = (np.arange(top, bottom, dtype=np.float32) + 0.5) * small_height / height - 0.5
        grid_x, grid_y = np.meshgrid(x, y)

        def sample(
            image: np.ndarray,
            displacement: np.ndarray | None = None,
            grid_x: np.ndarray = grid_x,
            grid_y: np.ndarray = grid_y,
        ) -> np.ndarray:
            if displacement is not None:
                # Flow is interpolated at each full-resolution output pixel. Scaling
                # it to output pixels and then back to the keyframe grid cancels out.
                flow = cv2.remap(
                    displacement, grid_x, grid_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE
                )
                sample_x, sample_y = grid_x + flow[:, :, 0], grid_y + flow[:, :, 1]
            else:
                sample_x, sample_y = grid_x, grid_y
            return cv2.remap(
                image, sample_x, sample_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE
            )

        forward = sample(first, forward_map)
        ab = forward
        if last is not None and backward_map is not None and feather is not None:
            backward = sample(last, backward_map)
            blended = forward * (1 - weight) + backward * weight
            mask = sample(feather)[:, :, None]
            nearest = forward if weight <= 0.5 else backward
            ab = blended * (1 - mask) + nearest * mask
        ab += sample(smoothing)
        lab[top:bottom, :, 1:] = np.clip(ab, 0, 255).astype(np.uint8)
    return cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)


def colour_frames(
    frames: Iterable[np.ndarray],
    colourise: Callable[[np.ndarray], np.ndarray],
    interval: int = 12,
    inconsistent_threshold: float = 0.35,
) -> Iterator[np.ndarray]:
    """Colour frames using keyframes and bidirectional flow, buffering at most interval + 1."""
    if interval < 1:
        raise ValueError("Keyframe interval must be positive")
    buffered: list[np.ndarray] = []
    lumas: list[np.ndarray] = []
    forwards: list[np.ndarray] = []
    backwards: list[np.ndarray] = []
    first_ab: np.ndarray | None = None
    smoothed_ab: np.ndarray | None = None
    smoothed_luma: np.ndarray | None = None

    def key_ab(frame: np.ndarray) -> np.ndarray:
        return cv2.cvtColor(colourise(_small(frame)), cv2.COLOR_RGB2LAB)[:, :, 1:].astype(
            np.float32
        )

    def flush(last_ab: np.ndarray | None) -> Iterator[np.ndarray]:
        nonlocal smoothed_ab, smoothed_luma
        assert first_ab is not None
        forward_ab = [first_ab]
        forward_maps = [np.zeros((*first_ab.shape[:2], 2), dtype=np.float32)]
        forward_invalid = [np.zeros(first_ab.shape[:2], dtype=bool)]
        for forward, backward in zip(forwards, backwards, strict=True):
            forward_ab.append(_warp(forward_ab[-1], backward))
            forward_maps.append(backward + _warp(forward_maps[-1], backward))
            carried = _warp(forward_invalid[-1].astype(np.float32), backward) > 0.01
            forward_invalid.append(carried | _inconsistent(forward, backward))
        backward_ab = [first_ab] * len(buffered)
        backward_maps = [np.zeros((*first_ab.shape[:2], 2), dtype=np.float32) for _ in buffered]
        backward_invalid = [np.zeros(first_ab.shape[:2], dtype=bool) for _ in buffered]
        if last_ab is not None:
            backward_ab[-1] = last_ab
            for index in range(len(buffered) - 2, -1, -1):
                backward_ab[index] = _warp(backward_ab[index + 1], forwards[index])
                backward_maps[index] = forwards[index] + _warp(
                    backward_maps[index + 1], forwards[index]
                )
                carried = (
                    _warp(backward_invalid[index + 1].astype(np.float32), forwards[index]) > 0.01
                )
                backward_invalid[index] = carried | _inconsistent(backwards[index], forwards[index])
        end = len(buffered) - 1 if last_ab is not None else len(buffered)
        for index in range(end):
            ab = forward_ab[index]
            invalid = None
            weight = 0.0
            if last_ab is not None and index:
                weight = index / (len(buffered) - 1)
                invalid = forward_invalid[index] | backward_invalid[index]
                ab = _blend_chroma(ab, backward_ab[index], weight, invalid)
            unsmoothed = ab
            if smoothed_ab is not None and smoothed_luma is not None:
                backward_flow = (
                    backwards[index - 1] if index else _flow(lumas[index], smoothed_luma)
                )
                forward_flow = forwards[index - 1] if index else _flow(smoothed_luma, lumas[index])
                valid = ~_inconsistent(forward_flow, backward_flow)
                previous = _warp(smoothed_ab, backward_flow)
                ab = np.where(valid[:, :, None], 0.85 * ab + 0.15 * previous, ab)
            smoothed_ab = ab
            smoothed_luma = lumas[index]
            yield _render_mapped(
                buffered[index],
                first_ab,
                forward_maps[index],
                last_ab if index else None,
                backward_maps[index] if index else None,
                weight,
                invalid,
                ab - unsmoothed,
            )

    for frame in frames:
        lum = _luma(frame)
        if not buffered:
            buffered.append(frame)
            lumas.append(lum)
            first_ab = key_ab(frame)
            continue
        if _cut_luma(lumas[-1], lum):
            yield from flush(None)
            smoothed_ab = None
            smoothed_luma = None
            buffered = [frame]
            lumas = [lum]
            forwards, backwards = [], []
            first_ab = key_ab(frame)
            continue
        forward = _flow(lumas[-1], lum)
        backward = _flow(lum, lumas[-1])
        forwards.append(forward)
        backwards.append(backward)
        buffered.append(frame)
        lumas.append(lum)
        if (
            len(buffered) - 1 >= interval
            or _inconsistent(forward, backward).mean() > inconsistent_threshold
        ):
            next_ab = key_ab(frame)
            yield from flush(next_ab)
            buffered = [frame]
            lumas = [lum]
            forwards, backwards = [], []
            first_ab = next_ab
    if buffered:
        yield from flush(None)
