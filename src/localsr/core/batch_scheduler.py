"""One tile queue across several photos for several inference lanes, with bounded memory.

This module knows nothing about Core ML: a lane is any callable ``lane(photo, tile)``
that returns the tile's pixels, so tests drive it with fake lanes. The calling
thread coordinates. It decodes the next photo ahead on one helper thread, queues
tile coordinates in photo order (a photo's tiles enter the queue only after the
previous photo's), stitches each lane result into its own photo, and hands a
finished photo to a pool of encoder threads.

Bounds: the queue holds one tile per lane. At most ``encoders + 2`` photos are
open at once, counting the one being decoded, the one being tiled and every
photo that waits for or occupies an encoder. With a ``memory_budget`` (bytes),
saves run together only while the ``nbytes`` of their photos fit in it, and no
further photo is decoded while the open photos already reach it; a photo larger
than the budget still runs, alone. A slow disk therefore stalls the lanes
instead of filling memory.

A photo provides ``tiles`` and optionally ``nbytes`` (its peak memory while it
saves); ``start()`` before its first tile is queued; ``dispatch(tile)`` as each
tile is queued; ``stitch(tile, pixels)`` for each result; ``encode()`` once, on
an encoder thread, after its last stitch; and ``close()`` exactly once, last, on
the calling thread.
"""

from __future__ import annotations

import queue
import threading
from collections import deque
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import FIRST_COMPLETED, CancelledError, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Any


def max_open_photos(encoders: int) -> int:
    """The photos open at once: one decoding, one being tiled, and each encoder's."""
    return encoders + 2


class PhotoFallback(Exception):
    """A tile these lanes cannot render; the photo goes back to the normal runner."""


_DONE, _PHOTO, _LANE, _SKIPPED = range(4)


@dataclass(eq=False)
class _Entry:
    index: int
    photo: Any
    tiles: deque = field(default_factory=deque)
    total: int = 0
    nbytes: int = 0
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
    except InterruptedError as error:
        # Without a cancellation, an interrupted lane cannot be trusted with tiles.
        return entry, tile, (_SKIPPED if cancel.is_set() else _LANE), error
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
    lane_failed: Callable[[Any, BaseException], None] | None = None,
    encoders: int = 4,
    memory_budget: int | None = None,
    poll: float = 0.02,
) -> list[Any]:
    """Process ``items`` and report each exactly once, or return it.

    ``open_photo(item)`` decodes an item into a photo. A failure to open, start,
    stitch or encode a photo reports only that item as failed. A lane raising
    :class:`PhotoFallback` returns its photo, unfinished, to the caller. Any other
    lane error retires that lane (``lane_failed(lane, error)``), and its tile runs
    on another; once no lane that may take tiles is left (all retired, or only
    idle ones such as a GPU lane on battery), every unfinished photo is returned.
    On cancellation, running native calls finish before their photos are
    released, every photo outside an encoder is reported cancelled at once, and
    each running save is reported as it ends: finished if it committed.

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
    max_open = max_open_photos(encoders)

    pending = deque(range(len(items)))
    opening: list[tuple[int, Future]] = []
    ready: deque[_Entry] = deque()
    tiling: deque[_Entry] = deque()
    retry: deque[tuple[_Entry, Any]] = deque()
    waiting: deque[_Entry] = deque()  # tiled, waiting for an encoder or for memory
    encoding: dict[_Entry, Future] = {}
    resolved: set[int] = set()
    returned: list[int] = []
    alive = set(range(len(lanes)))
    state = {"open": 0, "open_bytes": 0, "usable": True}

    def lane_loop(number: int, lane) -> None:
        while not stop.is_set():
            if not _available(lane):
                stop.wait(poll)
                continue
            try:
                entry, tile = work.get(timeout=poll)
            except queue.Empty:
                continue
            result = (number, *_render(lane, entry, tile, cancel))
            results.put(result)
            retired = result[3] == _LANE
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
        state["open"] -= 1
        state["open_bytes"] -= entry.nbytes
        entry.photo.close()

    def take_opened() -> None:
        if not opening or not opening[0][1].done():
            return
        index, future = opening.pop()
        try:
            photo = future.result()
        except (InterruptedError, CancelledError):
            state["open"] -= 1
            return
        except Exception as error:  # noqa: BLE001 - this photo only
            state["open"] -= 1
            resolve(index, "failed", error)
            return
        entry = _Entry(index, photo, deque(photo.tiles), nbytes=int(getattr(photo, "nbytes", 0)))
        entry.total = len(entry.tiles)
        entry.deferred = not state["usable"]
        state["open_bytes"] += entry.nbytes
        ready.append(entry)

    def start_opening() -> None:
        room = memory_budget is None or state["open_bytes"] < memory_budget
        if not opening and pending and not ready and state["open"] < max_open and room:
            index = pending.popleft()
            state["open"] += 1
            opening.append((index, decoder.submit(open_photo, items[index])))

    def feed() -> None:
        while state["usable"] and not work.full():
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
            number, entry, tile, kind, value = result
            entry.in_flight -= 1
            if kind == _LANE:
                alive.discard(number)
                if lane_failed is not None:
                    lane_failed(lanes[number], value)
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

    def check_lanes() -> None:
        """No lane may take tiles: return each unfinished photo to the normal runner."""
        state["usable"] = any(_available(lanes[number]) for number in alive)
        if state["usable"]:
            return
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
                waiting.append(entry)
        while ready and ready[0].deferred:
            entry = ready.popleft()
            close(entry)
            resolve(entry.index, "returned")

    def start_saves() -> None:
        while waiting and len(encoding) < encoders:
            busy = sum(entry.nbytes for entry in encoding)
            if encoding and memory_budget is not None and busy + waiting[0].nbytes > memory_budget:
                return
            entry = waiting.popleft()
            encoding[entry] = encoder.submit(entry.photo.encode)

    def finish_encoded(report: bool = True) -> None:
        for entry, future in [(e, f) for e, f in encoding.items() if f.done()]:
            del encoding[entry]
            close(entry)
            if not report:
                continue
            try:
                result = future.result()
            except (InterruptedError, CancelledError) as error:
                if index_open(entry.index):
                    if cancel.is_set():
                        resolve_cancelled(entry.index)
                    else:
                        resolve(entry.index, "failed", InterruptedError(str(error)))
                continue
            except Exception as error:  # noqa: BLE001 - this photo only
                resolve(entry.index, "failed", error)
            else:
                resolve(entry.index, "finished", result)

    def index_open(index: int) -> bool:
        return index not in resolved

    def resolve_cancelled(index: int) -> None:
        resolved.add(index)
        cancelled(items[index])

    threads = [
        threading.Thread(target=lane_loop, args=(i, lane), name=f"localsr-batch-lane-{i}")
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
            check_lanes()
            feed()
            start_opening()
            if not (tiling or waiting or encoding or opening or ready or pending):
                break
            collect(poll)
            finish_tiled()
            finish_encoded()
            start_saves()
        aborted = False
    finally:
        # Native calls cannot be interrupted: let them return before any photo
        # they read is released.
        stop.set()
        for thread in threads:
            thread.join()
        decoder.shutdown(wait=True, cancel_futures=True)
        if aborted:
            encoder.shutdown(wait=True, cancel_futures=True)
            for _, future in opening:
                if not future.cancelled() and future.exception() is None:
                    future.result().close()
            for entry in (*tiling, *ready, *waiting):
                close(entry)
            finish_encoded(report=False)
        else:
            take_opened()
            for entry in (*tiling, *ready, *waiting):
                close(entry)
            tiling.clear()
            ready.clear()
            waiting.clear()
            in_encoder = {entry.index for entry in encoding}
            for index in range(len(items)):
                if index_open(index) and index not in in_encoder:
                    if cancel.is_set():
                        resolve_cancelled(index)
                    else:
                        resolve(index, "failed", InterruptedError("The photo was interrupted."))
            # A running save commits or raises InterruptedError; one that has not
            # started is dropped. Report each as it ends.
            for future in encoding.values():
                future.cancel()
            while encoding:
                wait(list(encoding.values()), return_when=FIRST_COMPLETED)
                finish_encoded()
            encoder.shutdown(wait=True)
    return [items[index] for index in sorted(returned)]
