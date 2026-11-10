"""Remaining-time estimate from cumulative work, for every job kind.

Work is any additive measure of the job (output pixels of finished tiles, frames). The first
unit of work includes one-off costs such as compiling a model, so it only starts the clock:
no speed is taken from it. The speed is the average over all work since then (work done
divided by time), which moves less and less as evidence piles up. A recent average pulls it
only when the speed lately has differed from it by a clear margin for a sustained stretch of
work: a lasting slowdown or speed-up is followed, a single slow tile or noise is not. Until
enough steady work has been seen there is no estimate, and the caller shows nothing.
"""

from __future__ import annotations

import math
from bisect import bisect_left
from collections import deque

# Steady intervals needed before a speed is trusted, and the seconds they must span.
MIN_INTERVALS = 2
MIN_SECONDS = 1.0
# One interval counts at most this many times slower than the intervals just before it, so a
# single stall (a cache flush, a sleeping computer, another app taking the GPU for a moment)
# cannot swing the estimate; a lasting change soon becomes the intervals just before. Until
# there are that many intervals nothing is known to be a stall.
MAX_STALL = 2.5
# The intervals that say what is typical just now (the slowest of them is left out).
TYPICAL_INTERVALS = 8
# The recent speed is taken over this share of the whole job's work, at least this many
# intervals.
RECENT_SHARE = 0.1
RECENT_INTERVALS = 4
# The recent speed pulls the estimate from this ratio to the average, fully from the other.
PULL_FROM = 1.15
PULL_FULL = 1.4
# It must have differed that way for this many times the recent stretch before it pulls at
# all, and for twice that before it pulls fully.
SUSTAIN = 1.0


class RemainingTime:
    def __init__(
        self,
        total: float,
        *,
        min_intervals: int = MIN_INTERVALS,
        min_seconds: float = MIN_SECONDS,
        warmup: int = 1,
    ) -> None:
        self.total = float(total)
        self.min_intervals = min_intervals
        self.min_seconds = min_seconds
        # How many reports only move the starting line (one per lane that has to warm up).
        self.warmup = max(1, warmup)
        self.done = 0.0
        self._at: float | None = None
        self._warmed = 0
        self._span = 0.0
        # Seconds the intervals count for and the work they hold, cumulative after each.
        self._seconds: list[float] = [0.0]
        self._work: list[float] = [0.0]
        self._typical: deque[tuple[float, float]] = deque(maxlen=TYPICAL_INTERVALS)
        # The work done since the recent speed first differed from the average the same way,
        # and the way (+1 faster, -1 slower).
        self._differing = 0.0
        self._direction = 0

    def update(self, now: float, done: float) -> None:
        """Record the cumulative work done at time ``now``."""
        if not (math.isfinite(now) and math.isfinite(done)) or done <= self.done:
            return
        if self._at is None or self._warmed < self.warmup:
            # The first unit of work: it carries the warm-up, so it only sets the baseline.
            self._at, self.done = now, done
            self._warmed += 1
            return
        seconds = now - self._at
        if seconds <= 0:
            # A clock that did not tick (or went back): the work counts with the next tick.
            return
        work = done - self.done
        counted = seconds
        if len(self._typical) == TYPICAL_INTERVALS:
            # What the intervals just before took per unit of work, without the slowest.
            rest = sorted(self._typical, key=lambda item: item[0] / item[1])[:-1]
            typical = sum(s for s, _w in rest) / sum(w for _s, w in rest)
            counted = min(seconds, typical * work * MAX_STALL)
        self._typical.append((seconds, work))
        self._seconds.append(self._seconds[-1] + counted)
        self._work.append(self._work[-1] + work)
        self._span += seconds
        self._at, self.done = now, done
        self._follow(work)

    def restart(self, now: float, total: float) -> None:
        """The work counter began again from zero (for example after a smaller tile size).

        The measured speed stays: it is in work per second, whatever the pieces are.
        """
        self.total = float(total)
        self.done = 0.0
        self._at = now if len(self._work) > 1 else None

    def _recent(self) -> float | None:
        """Seconds per unit of work over the most recent stretch, or None if it is short."""
        intervals = len(self._work) - 1
        stretch = max(self.total * RECENT_SHARE, 1e-9)
        # The most recent intervals holding at least that much work, and a few at least.
        start = bisect_left(self._work, self._work[-1] - stretch)
        start = min(start, intervals - RECENT_INTERVALS)
        if start < 0:
            return None
        work = self._work[-1] - self._work[start]
        return (self._seconds[-1] - self._seconds[start]) / work if work > 0 else None

    def _ratio(self) -> float | None:
        """Recent speed over average speed (above 1 is faster), or None while unknown."""
        recent = self._recent()
        if recent is None or recent <= 0 or self._seconds[-1] <= 0:
            return None
        return (self._seconds[-1] / self._work[-1]) / recent

    def _follow(self, work: float) -> None:
        ratio = self._ratio()
        direction = 0
        if ratio is not None and abs(math.log(ratio)) > math.log(PULL_FROM):
            direction = 1 if ratio > 1 else -1
        if direction and direction == self._direction:
            self._differing += work
        else:
            self._differing = work if direction else 0.0
        self._direction = direction

    def seconds_per_unit(self) -> float | None:
        """Seconds one unit of work takes now, or None while there is not enough evidence."""
        if (
            len(self._work) - 1 < self.min_intervals
            or self._span < self.min_seconds
            or self._work[-1] <= 0
            or self._seconds[-1] <= 0
        ):
            return None
        average = self._seconds[-1] / self._work[-1]
        ratio = self._ratio()
        if ratio is None or not self._direction:
            return average
        stretch = max(self.total * RECENT_SHARE, 1e-9)
        amount = (abs(math.log(ratio)) - math.log(PULL_FROM)) / (
            math.log(PULL_FULL) - math.log(PULL_FROM)
        )
        lasting = (self._differing / stretch - SUSTAIN) / SUSTAIN
        pull = min(max(amount, 0.0), 1.0) * min(max(lasting, 0.0), 1.0)
        # Weighted by the log, so a pull towards a slowdown and one towards a speed-up are
        # of the same size.
        return average / ratio**pull

    def remaining(self) -> float | None:
        """Seconds left, or None while there is not enough steady work to say."""
        speed = self.seconds_per_unit()
        if speed is None:
            return None
        return max(0.0, self.total - self.done) * speed
