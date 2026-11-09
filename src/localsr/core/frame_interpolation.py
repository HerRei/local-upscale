"""Optional frame interpolation for video jobs.

The model only synthesizes the pixels between two frames. Everything else is
here and does not depend on the model: which output times exist, which source
frames bound each one, where a scene cut stops blending, how repeated frames
(animation on twos, pulldown duplicates) are bridged, and which time base
holds every output timestamp exactly, so the encoder, the audio and variable
frame rates keep their timing.

Two kinds of target exist. A multiplier (×2, ×4) divides every source
interval, so variable frame rate stays variable and each source frame keeps
its own timestamp. A frame rate (60, 120 fps) resamples onto a constant grid
that starts at the first source frame. Neither changes the start or the end
of the video, so source audio is copied unchanged.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Protocol

import numpy as np
import torch
import torch.nn.functional as F

from .video_io import TimedVideoFrame

ORDERS = ("after_upscale", "before_upscale")
_TARGETS: dict[str, tuple[int, Fraction | None]] = {
    "x2": (2, None),
    "x4": (4, None),
    "fps60": (0, Fraction(60)),
    "fps120": (0, Fraction(120)),
}

# Practical-RIFE's inference_video.py holds the previous frame instead of
# interpolating when a 32×32 SSIM falls below 0.2; LocalSR uses the same test.
SCENE_CUT_SSIM = 0.2
# A frame repeats the previous picture when no luma sample of its small
# thumbnail differs by more than this many 8-bit levels.
DUPLICATE_LEVELS = 2.5
# The longest run of repeats bridged as one held drawing (animation on threes).
# A longer run is still content, so only its last interval is interpolated.
MAX_DUPLICATE_RUN = 2
_FINE_EDGE = 160
_COARSE = 32


class FrameSynthesizer(Protocol):
    def synthesize(
        self,
        first: np.ndarray,
        second: np.ndarray,
        timesteps: Sequence[float],
        cancel_event: threading.Event | None = None,
    ) -> list[np.ndarray]:
        """Return one HxWx3 uint8 frame per timestep in (0, 1), in order."""
        ...


@dataclass(frozen=True)
class InterpolationTarget:
    name: str
    multiplier: int = 0
    frame_rate: Fraction | None = None

    @classmethod
    def parse(cls, name: str | None) -> InterpolationTarget | None:
        """``off`` (or nothing) disables interpolation; unknown names are refused."""
        if name in (None, "", "off"):
            return None
        try:
            multiplier, rate = _TARGETS[str(name)]
        except KeyError:
            raise ValueError(
                f"Unknown frame interpolation target {name!r}; use x2, x4, fps60 or fps120."
            ) from None
        return cls(str(name), multiplier, rate)

    def output_fps(self, source_fps: float) -> float:
        """The nominal output rate, for the encoder's header."""
        if self.frame_rate is not None:
            return float(self.frame_rate)
        return source_fps * self.multiplier

    def applies_to(self, source_fps: float) -> bool:
        """A frame-rate target only raises the rate; it never drops source frames."""
        return self.frame_rate is None or source_fps < float(self.frame_rate) * 0.99

    def output_time_base(self, source: Fraction) -> Fraction:
        """A time base in which the source times and every output time are integers."""
        if self.frame_rate is None:
            return Fraction(1, source.denominator * self.multiplier)
        return Fraction(1, math.lcm(source.denominator, self.frame_rate.numerator))

    def expected_frames(self, frame_count: int, source_fps: float) -> int:
        """How many frames a source of ``frame_count`` frames becomes (for progress)."""
        if frame_count <= 0:
            return 0
        if self.frame_rate is None:
            return (frame_count - 1) * self.multiplier + 1
        if source_fps <= 0:
            return frame_count
        return max(frame_count, math.ceil(frame_count * float(self.frame_rate) / source_fps - 1e-9))


@dataclass(frozen=True)
class FrameSignature:
    """Small luma thumbnails: ``fine`` for repeats, ``coarse`` (32×32) for cuts."""

    fine: np.ndarray
    coarse: np.ndarray


def frame_signature(rgb: np.ndarray) -> FrameSignature:
    height, width = rgb.shape[:2]
    # Very large frames are thinned before averaging; 4K stays above 600 px.
    stride = max(1, max(height, width) // 640)
    sample = np.asarray(rgb[::stride, ::stride, :3], dtype=np.float32)
    luma = torch.from_numpy(sample @ np.array([0.299, 0.587, 0.114], dtype=np.float32))
    scale = min(1.0, _FINE_EDGE / max(luma.shape))
    fine_size = (max(1, round(luma.shape[0] * scale)), max(1, round(luma.shape[1] * scale)))
    planes = luma[None, None]
    fine = F.adaptive_avg_pool2d(planes, fine_size)[0, 0].numpy()
    coarse = F.adaptive_avg_pool2d(planes, (_COARSE, _COARSE))[0, 0].numpy()
    return FrameSignature(fine, coarse)


def is_duplicate(previous: FrameSignature, current: FrameSignature) -> bool:
    if previous.fine.shape != current.fine.shape:
        return False
    return float(np.max(np.abs(previous.fine - current.fine))) <= DUPLICATE_LEVELS


def is_scene_cut(previous: FrameSignature, current: FrameSignature) -> bool:
    return _ssim(previous.coarse, current.coarse) < SCENE_CUT_SSIM


def _ssim(first: np.ndarray, second: np.ndarray) -> float:
    """Mean SSIM with an 11-tap Gaussian (sigma 1.5) over the valid region, 8-bit range."""
    taps = np.exp(-((np.arange(11) - 5) ** 2) / (2 * 1.5**2))
    taps /= taps.sum()

    def blur(values: np.ndarray) -> np.ndarray:
        rows = np.apply_along_axis(np.convolve, 0, values, taps, mode="valid")
        return np.apply_along_axis(np.convolve, 1, rows, taps, mode="valid")

    a, b = first.astype(np.float64), second.astype(np.float64)
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    mean_a, mean_b = blur(a), blur(b)
    var_a = blur(a * a) - mean_a**2
    var_b = blur(b * b) - mean_b**2
    covariance = blur(a * b) - mean_a * mean_b
    score = ((2 * mean_a * mean_b + c1) * (2 * covariance + c2)) / (
        (mean_a**2 + mean_b**2 + c1) * (var_a + var_b + c2)
    )
    return float(score.mean())


def _check(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("video job cancelled")


class _Schedule:
    """Output times and exact durations for one stream."""

    def __init__(self, target: InterpolationTarget, first: TimedVideoFrame):
        self.target = target
        self.origin = first.timestamp
        self.time_base = target.output_time_base(first.time_base)
        self.index = 0

    def times(self, start: Fraction, end: Fraction, final: bool) -> list[Fraction]:
        """Output times in [start, end); a multiplier shows the final frame once."""
        rate = self.target.frame_rate
        if rate is None:
            if final:
                return [start]
            count = self.target.multiplier
            return [start + (end - start) * step / count for step in range(count)]
        first = math.ceil((start - self.origin) * rate)
        last = math.ceil((end - self.origin) * rate)
        return [self.origin + Fraction(step) / rate for step in range(first, last)]

    def emit(self, times, pixels, end: Fraction, final: bool) -> Iterator[TimedVideoFrame]:
        rate = self.target.frame_rate
        for position, (stamp, rgb) in enumerate(zip(times, pixels, strict=True)):
            if position + 1 < len(times):
                following = times[position + 1]
            elif rate is not None and not final:
                following = stamp + 1 / rate
            else:
                following = end
            yield TimedVideoFrame(self.index, rgb, stamp, following - stamp, self.time_base)
            self.index += 1

    def hold(self, frame: TimedVideoFrame, until: Fraction | None) -> Iterator[TimedVideoFrame]:
        """Show ``frame`` unchanged until ``until`` (or to its own end, at the last frame)."""
        final = until is None
        end = frame.timestamp + frame.duration if until is None else until
        times = self.times(frame.timestamp, end, final)
        yield from self.emit(times, [frame.rgb] * len(times), end, final)

    def bridge(
        self,
        run: list[TimedVideoFrame],
        following: TimedVideoFrame,
        synthesizer: FrameSynthesizer | None,
        cancel_event: threading.Event | None,
    ) -> Iterator[TimedVideoFrame]:
        """Fill [run[0], following) from its two distinct ends.

        ``run`` is a frame and its repeats. Its repeats are replaced by in-between
        frames, so motion stays even. Without a synthesizer (a scene cut) every
        output time shows the source frame on screen at that time.
        """
        anchor = run[0]
        span = following.timestamp - anchor.timestamp
        slots: list[tuple[Fraction, np.ndarray | None]] = []
        for position, frame in enumerate(run):
            end = run[position + 1].timestamp if position + 1 < len(run) else following.timestamp
            for stamp in self.times(frame.timestamp, end, final=False):
                if synthesizer is None or (position == 0 and stamp == anchor.timestamp):
                    slots.append((stamp, frame.rgb))
                else:
                    slots.append((stamp, None))
        steps = [float((stamp - anchor.timestamp) / span) for stamp, rgb in slots if rgb is None]
        made: Iterator[np.ndarray] = iter(())
        if steps:
            assert synthesizer is not None
            _check(cancel_event)
            frames = synthesizer.synthesize(anchor.rgb, following.rgb, steps, cancel_event)
            if len(frames) != len(steps):
                raise ValueError(
                    f"Frame interpolation returned {len(frames)} frames for {len(steps)} times."
                )
            made = iter(frames)
        pixels = [rgb if rgb is not None else next(made) for _stamp, rgb in slots]
        yield from self.emit([stamp for stamp, _rgb in slots], pixels, following.timestamp, False)


def interpolate_frames(
    frames: Iterable[TimedVideoFrame],
    target: InterpolationTarget,
    synthesizer: FrameSynthesizer,
    cancel_event: threading.Event | None,
    *,
    max_duplicate_run: int = MAX_DUPLICATE_RUN,
) -> Iterator[TimedVideoFrame]:
    """Stream ``frames`` at the target rate, reading at most a few frames ahead.

    Memory stays bounded by ``max_duplicate_run`` + 2 frames. Every source frame
    keeps its pixels at its own timestamp unless it repeats the previous picture.
    """
    iterator = iter(frames)
    first = next(iterator, None)
    if first is None:
        return
    schedule = _Schedule(target, first)
    run = [first]
    anchor = frame_signature(first.rgb)
    holding = False
    for frame in iterator:
        _check(cancel_event)
        signature = frame_signature(frame.rgb)
        if is_duplicate(anchor, signature):
            run.append(frame)
            if holding or len(run) - 1 > max_duplicate_run:
                # Still content: show it as it is and keep only the newest copy.
                while len(run) > 1:
                    held = run.pop(0)
                    yield from schedule.hold(held, run[0].timestamp)
                holding = True
            continue
        bridge = None if is_scene_cut(anchor, signature) else synthesizer
        yield from schedule.bridge(run, frame, bridge, cancel_event)
        run, anchor, holding = [frame], signature, False
    while run:
        held = run.pop(0)
        yield from schedule.hold(held, run[0].timestamp if run else None)


__all__ = [
    "MAX_DUPLICATE_RUN",
    "ORDERS",
    "FrameSignature",
    "FrameSynthesizer",
    "InterpolationTarget",
    "frame_signature",
    "interpolate_frames",
    "is_duplicate",
    "is_scene_cut",
]
