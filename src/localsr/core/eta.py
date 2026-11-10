"""Remaining-time estimate from cumulative work, for every job kind.

Work is any additive measure of the job (output pixels of finished tiles, frames). The first
unit of work includes one-off costs such as compiling a model, so it only starts the clock:
no speed is taken from it. The speed after it is an exponentially decaying average that
forgets by work done, not by tiles counted, so one slow tile does not swing it. Until enough
steady work has been seen there is no estimate, and the caller shows nothing.
"""

from __future__ import annotations

import math

# Steady intervals needed before a speed is trusted, and the seconds they must span.
MIN_INTERVALS = 2
MIN_SECONDS = 1.0
# One interval counts at most this many times faster or slower than the average so far, so a
# single stall (a cache flush, another app taking the GPU for a moment) cannot swing the
# estimate; a lasting change still wins within a few intervals.
MAX_SWING = 4.0


class RemainingTime:
    def __init__(
        self,
        total: float,
        *,
        half_life: float | None = None,
        min_intervals: int = MIN_INTERVALS,
        min_seconds: float = MIN_SECONDS,
    ) -> None:
        self.total = float(total)
        # By default the average looks back over about a quarter of the job.
        self.half_life = half_life if half_life and half_life > 0 else max(self.total / 4, 1.0)
        self.min_intervals = min_intervals
        self.min_seconds = min_seconds
        self.done = 0.0
        self._at: float | None = None
        self._seconds = 0.0
        self._work = 0.0
        self._intervals = 0
        self._span = 0.0

    def update(self, now: float, done: float) -> None:
        """Record the cumulative work done at time ``now``."""
        if not (math.isfinite(now) and math.isfinite(done)) or done <= self.done:
            return
        if self._at is None:
            # The first unit of work: it carries the warm-up, so it only sets the baseline.
            self._at, self.done = now, done
            return
        seconds = now - self._at
        if seconds <= 0:
            # A clock that did not tick (or went back): the work counts with the next tick.
            return
        work = done - self.done
        counted = seconds
        if self._intervals >= self.min_intervals:
            typical = self._seconds / self._work * work
            counted = min(max(seconds, typical / MAX_SWING), typical * MAX_SWING)
        decay = 0.5 ** (work / self.half_life)
        self._seconds = self._seconds * decay + counted
        self._work = self._work * decay + work
        self._intervals += 1
        self._span += seconds
        self._at, self.done = now, done

    def restart(self, now: float, total: float) -> None:
        """The work counter began again from zero (for example after a smaller tile size).

        The measured speed stays: it is in work per second, whatever the pieces are.
        """
        self.total = float(total)
        self.half_life = max(self.total / 4, 1.0)
        self.done = 0.0
        self._at = now if self._intervals else None

    def remaining(self) -> float | None:
        """Seconds left, or None while there is not enough steady work to say."""
        if (
            self._intervals < self.min_intervals
            or self._span < self.min_seconds
            or self._work <= 0
            or self._seconds <= 0
        ):
            return None
        return max(0.0, self.total - self.done) * self._seconds / self._work
