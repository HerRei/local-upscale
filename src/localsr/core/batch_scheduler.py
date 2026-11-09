"""One tile queue across several photos for several inference lanes, with bounded memory.

This module knows nothing about Core ML: a lane is any callable ``lane(photo, tile)``
that returns the tile's pixels, so tests drive it with fake lanes. The calling
thread coordinates. It decodes the next photo ahead on one helper thread, queues
tile coordinates in photo order (a photo's tiles enter the queue only after the
previous photo's), stitches each lane result into its own photo, and hands a
finished photo to a pool of encoder threads.

Bounds: the queue holds one tile per lane, so at most two tiles per lane exist
at a time; at most ``encoders + 2`` photos are open at once, counting the one
being decoded, the one being tiled and every photo that waits for or occupies
an encoder. A slow disk therefore stalls the lanes instead of filling memory.

A photo provides ``tiles``; ``start()`` before its first tile is queued;
``dispatch(tile)`` as each tile is queued; ``stitch(tile, pixels)`` for each
result; ``encode()`` once, on an encoder thread, after its last stitch; and
``close()`` exactly once, last, on the calling thread.
"""

from __future__ import annotations

import queue
import threading
from collections import deque
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any


class PhotoFallback(Exception):
    """A tile these lanes cannot render; the photo goes back to the normal runner."""


_DONE, _PHOTO, _LANE, _SKIPPED = range(4)


@dataclass(eq=False)
class _Entry:
    index: int
    photo: Any
    tiles: deque = field(default_factory=deque)
    total: int = 0
    stitched: int = 0
    in_flight: int = 0
    error: Exception | None = None
    deferred: bool = False

    @property
    def broken(self) -> bool:
        return self.error is not None or self.deferred

    @property
    def tiled(self) -> bool:
        return self.in_flight == 0 and (self.broken or self.stitched == self.total)


def _available(lane) -> bool:
    try:
        return bool(getattr(lane, "available", True))
    except Exception:  # noqa: BLE001 - an unreadable power state keeps the lane idle
        return False


def _render(lane, entry: _Entry, tile, cancel: threading.Event) -> tuple:
    if cancel.is_set() or entry.broken:
        return entry, tile, _SKIPPED, None
    try:
        return entry, tile, _DONE, lane(entry.photo, tile)
    except PhotoFallback as error:
        return entry, tile, _PHOTO, error
    except InterruptedError:
        return entry, tile, _SKIPPED, None
    except BaseException as error:  # noqa: BLE001 - every taken tile must be reported
        return entry, tile, _LANE, error


def run_photo_batch(
    items: Iterable[Any],
    *,
    open_photo: Callable[[Any], Any],
    lanes: Sequence[Callable[[Any, Any], Any]],
    cancel: threading.Event,
    finished: Callable[[Any, Any], None],
    failed: Callable[[Any, Exception], None],
    cancelled: Callable[[Any], None],
    lane_failed: Callable[[BaseException], None] | None = None,
    encoders: int = 4,
    poll: float = 0.02,
) -> list[Any]:
    """Process ``items`` and report each exactly once, or return it.

    ``open_photo(item)`` decodes an item into a photo. A failure to open, start,
    stitch or encode a photo reports only that item as failed. A lane raising
    :class:`PhotoFallback` returns its photo, unfinished, to the caller. Any other
    lane error retires that lane, and its tile runs on another; once no lane is
    left, every unfinished photo is returned. On cancellation, running native
    calls and saves finish before their photos are released, a save that already
    committed still counts as finished, and every other item is cancelled.

    Returns the items for the normal per-photo runner, in their original order.
    """
    if not lanes:
        raise ValueError("A photo batch needs at least one lane.")
    if encoders < 1:
        raise ValueError("A photo batch needs at least one encoder.")
    items = list(items)
    work: queue.Queue = queue.Queue(maxsize=len(lanes))
    # Bounded by construction: every result is a queued tile that a lane took.
    results: queue.SimpleQueue = queue.SimpleQueue()
    stop = threading.Event()
    max_open = encoders + 2

    pending = deque(range(len(items)))
    opening: list[tuple[int, Future]] = []
    ready: deque[_Entry] = deque()
    tiling: deque[_Entry] = deque()
    retry: deque[tuple[_Entry, Any]] = deque()
    encoding: dict[_Entry, Future] = {}
    resolved: set[int] = set()
    returned: list[int] = []
    counts = {"open": 0, "lanes": len(lanes)}

    def lane_loop(lane) -> None:
        while not stop.is_set():
            if not _available(lane):
                stop.wait(poll)
                continue
            try:
                entry, tile = work.get(timeout=poll)
            except queue.Empty:
                continue
            result = _render(lane, entry, tile, cancel)
            results.put(result)
            retired = result[2] == _LANE
            # An idle lane must not keep the last tile's input or output alive.
            del entry, tile, result
            if retired:
                return

    def resolve(index: int, outcome: str, value: Any = None) -> None:
        resolved.add(index)
        if outcome == "finished":
            finished(items[index], value)
        elif outcome == "failed":
            failed(items[index], value)
        else:
            returned.append(index)

    def close(entry: _Entry) -> None:
        counts["open"] -= 1
        entry.photo.close()

    def take_opened() -> None:
        if not opening or not opening[0][1].done():
            return
        index, future = opening.pop()
        try:
            photo = future.result()
        except (InterruptedError, CancelledError):
            counts["open"] -= 1
            return
        except Exception as error:  # noqa: BLE001 - this photo only
            counts["open"] -= 1
            resolve(index, "failed", error)
            return
        entry = _Entry(index, photo, deque(photo.tiles))
        entry.total = len(entry.tiles)
        entry.deferred = counts["lanes"] == 0
        ready.append(entry)

    def start_opening() -> None:
        if not opening and pending and not ready and counts["open"] < max_open:
            index = pending.popleft()
            counts["open"] += 1
            opening.append((index, decoder.submit(open_photo, items[index])))

    def feed() -> None:
        while counts["lanes"] and not work.full():
            if retry:
                entry, tile = retry.popleft()
                if entry.broken:
                    continue
            else:
                entry = next((e for e in tiling if e.tiles and not e.broken), None)
                if entry is None:
                    if not ready:
                        return
                    entry = ready.popleft()
                    tiling.append(entry)
                    try:
                        entry.photo.start()
                    except Exception as error:  # noqa: BLE001 - this photo only
                        entry.error = error
                    continue
                tile = entry.tiles.popleft()
            try:
                entry.photo.dispatch(tile)
            except Exception as error:  # noqa: BLE001 - this photo only
                entry.error = error
                continue
            entry.in_flight += 1
            work.put_nowait((entry, tile))

    def collect(timeout: float) -> None:
        try:
            result = results.get(timeout=timeout)
        except queue.Empty:
            return
        while True:
            entry, tile, kind, value = result
            entry.in_flight -= 1
            if kind == _LANE:
                counts["lanes"] -= 1
                if lane_failed is not None:
                    lane_failed(value)
                retry.append((entry, tile))
            elif kind == _PHOTO:
                entry.deferred = True
            elif kind == _DONE and not entry.broken and not cancel.is_set():
                try:
                    entry.photo.stitch(tile, value)
                    entry.stitched += 1
                except Exception as error:  # noqa: BLE001 - this photo only
                    entry.error = error
            del result, entry, tile, value
            try:
                result = results.get_nowait()
            except queue.Empty:
                return

    def without_lanes() -> None:
        """Every lane retired: return each unfinished photo to the normal runner."""
        while True:
            try:
                entry, _tile = work.get_nowait()
            except queue.Empty:
                break
            entry.in_flight -= 1
        retry.clear()
        for entry in (*tiling, *ready):
            if entry.error is None and entry.stitched < entry.total:
                entry.deferred = True
        while pending:
            resolve(pending.popleft(), "returned")

    def finish_tiled() -> None:
        for entry in [entry for entry in tiling if entry.tiled]:
            tiling.remove(entry)
            if entry.error is not None:
                close(entry)
                resolve(entry.index, "failed", entry.error)
            elif entry.deferred:
                close(entry)
                resolve(entry.index, "returned")
            else:
                encoding[entry] = encoder.submit(entry.photo.encode)
        while ready and ready[0].deferred:
            entry = ready.popleft()
            close(entry)
            resolve(entry.index, "returned")

    def finish_encoded(report: bool = True) -> None:
        for entry, future in [(e, f) for e, f in encoding.items() if f.done()]:
            del encoding[entry]
            close(entry)
            if not report:
                continue
            try:
                result = future.result()
            except (InterruptedError, CancelledError):
                continue  # Cancelled before or during the save; reported below.
            except Exception as error:  # noqa: BLE001 - this photo only
                resolve(entry.index, "failed", error)
            else:
                resolve(entry.index, "finished", result)

    threads = [
        threading.Thread(target=lane_loop, args=(lane,), name=f"localsr-batch-lane-{i}")
        for i, lane in enumerate(lanes)
    ]
    decoder = ThreadPoolExecutor(max_workers=1, thread_name_prefix="localsr-batch-decode")
    encoder = ThreadPoolExecutor(max_workers=encoders, thread_name_prefix="localsr-batch-encode")
    for thread in threads:
        thread.start()
    aborted = True
    try:
        while not cancel.is_set():
            take_opened()
            if counts["lanes"] == 0:
                without_lanes()
            feed()
            start_opening()
            if not (tiling or encoding or opening or ready or pending):
                break
            collect(poll)
            finish_tiled()
            finish_encoded()
        aborted = False
    finally:
        # Native calls cannot be interrupted: let them return before any photo
        # they read is released. A running save commits or raises InterruptedError;
        # one that has not started is dropped.
        stop.set()
        for thread in threads:
            thread.join()
        decoder.shutdown(wait=True, cancel_futures=True)
        encoder.shutdown(wait=True, cancel_futures=True)
        if aborted:
            for _, future in opening:
                if not future.cancelled() and future.exception() is None:
                    future.result().close()
        else:
            take_opened()
        for entry in (*tiling, *ready):
            close(entry)
        tiling.clear()
        ready.clear()
        finish_encoded(report=not aborted)
    for index, item in enumerate(items):
        if index in resolved:
            continue
        if cancel.is_set():
            cancelled(item)
        else:
            failed(item, InterruptedError("The photo was interrupted without a cancellation."))
    return [items[index] for index in sorted(returned)]
