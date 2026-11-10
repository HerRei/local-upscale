"""A hard bound for work the engine does before a job's first tile or frame.

Converting and compiling a model, opening a session on a driver and loading a
checkpoint can run far longer than expected and cannot be interrupted once
started. Every such phase goes through ``bounded_preparation``: the work runs on
a helper thread, the caller waits in short slices (so a cancellation is seen
within a fraction of a second), repeats the status message every few seconds, and
gives up at the limit. The caller then continues on a route that needs no such
preparation, or fails with a clear sentence.

A thread cannot be stopped, so one that outlives its limit keeps running. At
most one such thread exists per slot; while it runs, the slot refuses new work
at once instead of piling a second compile on top of the first.
"""

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# The window never sits on one unchanged sentence for longer than this.
STATUS_INTERVAL_SECONDS = 5.0
# A cancellation is noticed within this long.
WAIT_SLICE_SECONDS = 0.25


class PreparationTimeout(TimeoutError):
    """The work did not finish within its limit, or its slot is still busy."""

    def __init__(self, name: str, limit: float, *, busy: bool = False):
        self.name, self.limit, self.busy = name, limit, busy
        super().__init__(
            f"{name} is still running from an earlier job"
            if busy
            else f"{name} did not finish within {limit:.0f} s"
        )


class _Box:
    def __init__(self) -> None:
        self.done = threading.Event()
        self.outcome: tuple[str, Any] | None = None
        self.abandoned = False


_lock = threading.Lock()
_abandoned: dict[str, threading.Thread] = {}


def busy_slot(slot: str) -> bool:
    """Whether a thread left behind by an earlier timeout or cancellation still runs."""
    with _lock:
        thread = _abandoned.get(slot)
        if thread is not None and not thread.is_alive():
            del _abandoned[slot]
            return False
        return thread is not None


def bounded_preparation(
    name: str,
    limit: float,
    work: Callable[[], Any],
    *,
    fallback: str,
    slot: str,
    cancel_event: threading.Event | None = None,
    status: Callable[[str], None] | None = None,
    deadline: float | None = None,
    since: float | None = None,
    on_late: Callable[[str, Any], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> Any:
    """Run ``work`` and wait at most ``limit`` seconds (or until ``deadline``, a
    ``clock`` time shared by several phases of one job); ``since`` is when that
    shared preparation began, for the elapsed time in the status message.

    Returns what ``work`` returns and re-raises what it raises. Raises
    ``InterruptedError`` when ``cancel_event`` is set, and ``PreparationTimeout``
    at the limit or when the slot's earlier thread still runs. ``name`` says what
    is being prepared and ``fallback`` what happens at the limit, as a clause
    ("starts on the standard path", "the job stops"); both appear in the status message, which is sent at the start
    and then every ``STATUS_INTERVAL_SECONDS``. ``on_late(kind, value)`` runs on
    the helper thread if the work finishes after the caller gave up; ``kind`` is
    "ok" or "error".
    """
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("Cancelled")
    if busy_slot(slot):
        raise PreparationTimeout(name, limit, busy=True)
    started = clock()
    end = started + limit if deadline is None else min(started + limit, deadline)
    shown_from = started if since is None else since
    box = _Box()

    def run() -> None:
        try:
            outcome: tuple[str, Any] = ("ok", work())
        except BaseException as error:  # noqa: BLE001 - handed to the caller
            outcome = ("error", error)
        with _lock:
            box.outcome = outcome
            box.done.set()
            late = box.abandoned
        if late:
            # The slot stays busy while the hook runs: it may load the result again.
            try:
                if on_late is not None:
                    on_late(*outcome)
            except Exception:  # noqa: BLE001
                logger.debug("Late preparation hook failed", exc_info=True)
            finally:
                with _lock:
                    if _abandoned.get(slot) is thread:
                        del _abandoned[slot]

    thread = threading.Thread(target=run, name=f"localsr-prepare-{slot}", daemon=True)
    thread.start()
    next_status = started
    while True:
        now = clock()
        if status is not None and now >= next_status:
            next_status = now + STATUS_INTERVAL_SECONDS
            left = max(0, round(end - now))
            try:
                status(
                    f"{name}. {max(0, round(now - shown_from))} s so far; "
                    f"{fallback} in {left} s if it is not ready."
                )
            except Exception:  # noqa: BLE001
                logger.debug("Preparation status callback failed", exc_info=True)
        if box.done.wait(min(WAIT_SLICE_SECONDS, max(0.0, end - now))):
            break
        interrupted = cancel_event is not None and cancel_event.is_set()
        if interrupted or clock() >= end:
            with _lock:
                if not box.done.is_set():
                    box.abandoned = True
                    _abandoned[slot] = thread
                    if interrupted:
                        raise InterruptedError("Cancelled")
                    raise PreparationTimeout(name, limit)
            break
    kind, value = box.outcome  # type: ignore[misc]
    if kind == "error":
        raise value
    return value
