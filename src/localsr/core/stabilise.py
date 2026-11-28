"""Video stabilisation: measured camera motion, a smoothed path, one crop-to-fit warp a frame.

Movement between neighbouring frames is measured as a shift and a turn from tracked points
on a grey copy no longer than 640 px. Per shot, the movements add up to the camera's path;
each frame's point on that path is replaced by a Gaussian-weighted straight-line fit of the
path over the level's window, and the frame is warped onto it. The same warp enlarges the
picture by the level's crop, so the band the correction moves in stays out of view and the
frame keeps its size, timing and sample type.

The model is a global shutter: a skew that changes within a frame (rolling-shutter wobble)
is not measured and stays in the picture, as does blur the shake left in single frames.
"""

from __future__ import annotations

import itertools
import math
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeVar

import cv2
import numpy as np

from .video_colour import _cut_luma

if TYPE_CHECKING:
    from .video_chain import StreamFrame, VideoStepContext, VideoStreamStage
    from .video_pipeline import VideoFixStage


@dataclass(frozen=True)
class SmoothingLevel:
    # The path is evened out over this many seconds before and after each frame.
    seconds: float
    # Fraction of the width and of the height cut off each edge.
    crop: float


LEVELS: dict[str, SmoothingLevel] = {
    "light": SmoothingLevel(seconds=0.25, crop=0.03),
    "medium": SmoothingLevel(seconds=0.5, crop=0.05),
    "strong": SmoothingLevel(seconds=1.0, crop=0.08),
}

# The source frames held for the look-ahead never take more than this.
LOOKAHEAD_BUDGET_BYTES = 400_000_000
TRACKING_EDGE = 640
# Fewer tracked points than this between two frames count as no movement.
MIN_TRACKS = 12
# Source pixels kept clear at each edge: the cubic kernel reads two pixels beyond the
# sample point, and decoded frames often carry a dark or ringing outermost line.
EDGE_GUARD = 3.0
# A measured movement beyond these is a tracking failure, not a camera move a crop of at
# most 8 % per edge could take back; it counts as no movement.
MAX_TURN = 0.2
MAX_SCALE_CHANGE = 0.2
MAX_SHIFT = 0.25

_LK = {
    "winSize": (21, 21),
    "maxLevel": 3,
    "criteria": (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01),
}

K = TypeVar("K")


def _rotation(angle: float) -> np.ndarray:
    cos, sin = math.cos(angle), math.sin(angle)
    return np.array([[cos, -sin], [sin, cos]])


def tracking_grey(rgb: np.ndarray) -> np.ndarray:
    """An 8-bit grey copy whose long edge is at most ``TRACKING_EDGE`` pixels."""
    height, width = rgb.shape[:2]
    scale = min(1.0, TRACKING_EDGE / max(height, width))
    small = np.ascontiguousarray(rgb)
    if scale < 1.0:
        size = (max(16, round(width * scale)), max(16, round(height * scale)))
        small = cv2.resize(small, size, interpolation=cv2.INTER_AREA)
    if small.dtype == np.uint16:
        small = (small >> 8).astype(np.uint8)
    elif small.dtype != np.uint8:
        small = np.clip(small * 255.0 + 0.5, 0, 255).astype(np.uint8)
    if small.ndim == 2:
        return small
    if small.shape[2] == 1:
        return np.ascontiguousarray(small[..., 0])
    return cv2.cvtColor(np.ascontiguousarray(small[..., :3]), cv2.COLOR_RGB2GRAY)


def measure_motion(
    previous: np.ndarray, current: np.ndarray, full_size: tuple[int, int]
) -> np.ndarray | None:
    """The rigid move from ``previous`` to ``current`` as ``(turn, x, y)`` of the frame's
    centre in full-size pixels, or ``None`` when too few points can be followed."""
    points = cv2.goodFeaturesToTrack(
        previous, maxCorners=400, qualityLevel=0.01, minDistance=8, blockSize=7
    )
    if points is None or len(points) < MIN_TRACKS:
        return None
    moved, found, _ = cv2.calcOpticalFlowPyrLK(previous, current, points, None, **_LK)
    back, found_back, _ = cv2.calcOpticalFlowPyrLK(current, previous, moved, None, **_LK)
    # A point that does not track back to where it started sat on an occlusion, a
    # repeating pattern or a moving object's edge.
    round_trip = np.linalg.norm((back - points).reshape(-1, 2), axis=1)
    good = (found.ravel() == 1) & (found_back.ravel() == 1) & (round_trip < 1.0)
    if int(good.sum()) < MIN_TRACKS:
        return None
    matrix, inliers = cv2.estimateAffinePartial2D(
        points[good],
        moved[good],
        method=cv2.RANSAC,
        ransacReprojThreshold=1.5,
        maxIters=2000,
        confidence=0.995,
    )
    if matrix is None or inliers is None or int(inliers.sum()) < MIN_TRACKS:
        return None
    small_height, small_width = previous.shape
    full_width, full_height = full_size
    linear, offset = matrix[:, :2], matrix[:, 2]
    turn = math.atan2(linear[1, 0], linear[0, 0])
    scale = math.hypot(linear[0, 0], linear[1, 0])
    centre = np.array([(small_width - 1) / 2, (small_height - 1) / 2])
    # The move of the centre point; the scale change itself is not corrected.
    shift = (linear @ centre + offset - centre) * (
        full_width / small_width,
        full_height / small_height,
    )
    if (
        abs(turn) > MAX_TURN
        or abs(scale - 1.0) > MAX_SCALE_CHANGE
        or abs(shift[0]) > MAX_SHIFT * full_width
        or abs(shift[1]) > MAX_SHIFT * full_height
    ):
        return None
    return np.array([turn, shift[0], shift[1]])


def _then(motion: np.ndarray, path: np.ndarray) -> np.ndarray:
    """The path point reached by ``motion`` after ``path``; both turn about the centre."""
    shift = _rotation(motion[0]) @ path[1:] + motion[1:]
    return np.array([path[0] + motion[0], shift[0], shift[1]])


def smoothed_point(path: np.ndarray, index: int, radius: int) -> np.ndarray:
    """A Gaussian-weighted straight-line fit of ``path`` around ``index``, read at ``index``.

    Inside a shot the window is symmetric and this is the weighted mean; at a shot's ends
    the line keeps a steady pan steady instead of pulling its first and last frames back.
    """
    first = max(0, index - radius)
    last = min(len(path) - 1, index + radius)
    offsets = np.arange(first - index, last - index + 1, dtype=np.float64)
    sigma = max(radius / 2.0, 0.5)
    weights = np.exp(-0.5 * (offsets / sigma) ** 2)
    values = path[first : last + 1]
    s0, s1, s2 = weights.sum(), weights @ offsets, weights @ offsets**2
    t0, t1 = weights @ values, (weights * offsets) @ values
    determinant = s0 * s2 - s1 * s1
    if determinant <= 1e-9 * s0 * s0:
        return t0 / s0
    return (s2 * t0 - s1 * t1) / determinant


def limit_correction(
    turn: float, shift: np.ndarray, size: tuple[int, int], crop: float
) -> tuple[float, np.ndarray]:
    """The largest part of a correction that keeps every output pixel inside the source,
    ``EDGE_GUARD`` pixels from its edge, once the frame is enlarged for ``crop``."""
    width, height = size
    half_w, half_h = (width - 1) / 2, (height - 1) / 2
    inner_w, inner_h = half_w * (1 - 2 * crop), half_h * (1 - 2 * crop)
    room_w, room_h = half_w - EDGE_GUARD, half_h - EDGE_GUARD

    def reach(angle: float) -> tuple[float, float]:
        cos, sin = abs(math.cos(angle)), abs(math.sin(angle))
        return inner_w * cos + inner_h * sin, inner_w * sin + inner_h * cos

    reach_w, reach_h = reach(turn)
    if reach_w > room_w or reach_h > room_h:
        fits, too_far = 0.0, abs(turn)
        for _ in range(32):
            middle = (fits + too_far) / 2
            middle_w, middle_h = reach(middle)
            if middle_w <= room_w and middle_h <= room_h:
                fits = middle
            else:
                too_far = middle
        turn = math.copysign(fits, turn)
        reach_w, reach_h = reach(turn)
    # The shift is bounded in the turned frame, where the window is an upright rectangle.
    local = _rotation(-turn) @ shift
    bound_w, bound_h = max(0.0, room_w - reach_w), max(0.0, room_h - reach_h)
    local = np.clip(local, (-bound_w, -bound_h), (bound_w, bound_h))
    return turn, _rotation(turn) @ local


def warp_frame(rgb: np.ndarray, turn: float, shift: np.ndarray, crop: float) -> np.ndarray:
    """Turn and shift ``rgb`` about its centre and enlarge it for ``crop``, in one resample."""
    height, width = rgb.shape[:2]
    zoom = 1.0 / (1.0 - 2.0 * crop)
    centre = np.array([(width - 1) / 2, (height - 1) / 2])
    linear = zoom * _rotation(turn)
    offset = centre + zoom * shift - linear @ centre
    matrix = np.hstack([linear, offset[:, None]])
    out = cv2.warpAffine(
        np.ascontiguousarray(rgb),
        matrix,
        (width, height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )
    if out.ndim == 2 and rgb.ndim == 3:
        out = out[..., None]
    if np.issubdtype(out.dtype, np.floating):
        # The cubic kernel overshoots at hard edges; float frames are coded 0..1.
        np.clip(out, 0.0, 1.0, out=out)
    return out


def window_radius(level: SmoothingLevel, fps: float, frame_bytes: int) -> int:
    """Frames on each side of the window, shortened when the look-ahead would not fit."""
    wanted = round(level.seconds * (fps if fps > 0 else 30.0))
    held = LOOKAHEAD_BUDGET_BYTES // max(1, frame_bytes)
    return max(0, min(wanted, held - 1))


def stabilise_frames(
    frames: Iterable[tuple[K, np.ndarray]],
    *,
    smoothing: str,
    fps: float,
    check_cancelled: Callable[[], None] | None = None,
) -> Iterator[tuple[K, np.ndarray]]:
    """Stabilise ``(key, pixels)`` pairs in order, yielding each key with its new pixels.

    A frame leaves once the path is known ``radius`` frames past it, or when its shot ends,
    so the stream lags by that many frames and holds at most ``radius + 1`` of them.
    """
    level = LEVELS[smoothing]
    stream = iter(frames)
    first = next(stream, None)
    if first is None:
        return
    radius = window_radius(level, fps, first[1].nbytes)
    pending: deque[tuple[K, np.ndarray, int]] = deque()
    path: deque[np.ndarray] = deque()
    # Shot-relative index of path[0]; older points are dropped once no window needs them.
    path_start = 0
    shot_length = 0
    previous: np.ndarray | None = None

    def emit() -> tuple[K, np.ndarray]:
        nonlocal path_start
        key, rgb, index = pending.popleft()
        points = np.asarray(path)
        here = index - path_start
        target = smoothed_point(points, here, radius)
        actual = points[here]
        turn = float(target[0] - actual[0])
        shift = target[1:] - _rotation(turn) @ actual[1:]
        height, width = rgb.shape[:2]
        turn, shift = limit_correction(turn, shift, (width, height), level.crop)
        # The newest point stays: the next frame's movement is added to it.
        while len(path) > 1 and path_start < index + 1 - radius:
            path.popleft()
            path_start += 1
        return key, warp_frame(rgb, turn, shift, level.crop)

    for key, rgb in itertools.chain((first,), stream):
        if check_cancelled is not None:
            check_cancelled()
        grey = tracking_grey(rgb)
        same_shot = (
            previous is not None and grey.shape == previous.shape and not _cut_luma(previous, grey)
        )
        if same_shot:
            motion = measure_motion(previous, grey, (rgb.shape[1], rgb.shape[0]))
            point = _then(np.zeros(3) if motion is None else motion, path[-1])
        else:
            # A cut, the first frame or a new frame size starts a new path; the frames of
            # the shot before it are finished on their own path.
            while pending:
                if check_cancelled is not None:
                    check_cancelled()
                yield emit()
            path.clear()
            path_start = 0
            shot_length = 0
            point = np.zeros(3)
        previous = grey
        path.append(point)
        pending.append((key, rgb, shot_length))
        shot_length += 1
        while pending and pending[0][2] + radius <= shot_length - 1:
            yield emit()
    while pending:
        if check_cancelled is not None:
            check_cancelled()
        yield emit()


def build_stabilise_stage(stage: VideoFixStage, step_ctx: VideoStepContext) -> VideoStreamStage:
    """The ``stabilise`` video step: runs on decoded frames, before the fixes and upscale."""
    ctx = step_ctx.shared
    smoothing = stage.params.get("smoothing", "medium")
    if smoothing not in LEVELS:
        raise ValueError(f"Unknown stabilise smoothing {smoothing!r}.")
    fps = float(getattr(ctx, "fps", 0.0) or 0.0)

    def _stage(frames: Iterator[StreamFrame]) -> Iterator[StreamFrame]:
        pairs = ((item, item.frame.rgb) for item in frames)
        for item, rgb in stabilise_frames(
            pairs, smoothing=smoothing, fps=fps, check_cancelled=ctx.check_cancelled
        ):
            yield item.with_pixels(rgb)

    return _stage
