"""One Automatic encoder retry, replaying rendered frames from private scratch."""

from __future__ import annotations

import errno
import pickle
import tempfile
from contextlib import suppress

from .disk_space import DiskFullError
from .system_codecs import SystemCodecsError

REPLAY_LIMIT_BYTES = 256 * 1024 * 1024


def system_export_failure(error: BaseException) -> bool:
    """Only an encoder failure qualifies, never inference or publication errors."""
    current = error
    found = False
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (InterruptedError, DiskFullError)):
            return False
        if isinstance(current, OSError) and current.errno in (
            errno.ENOSPC,
            getattr(errno, "EDQUOT", errno.ENOSPC),
        ):
            return False
        detail = str(current).lower()
        if any(
            text in detail
            for text in (
                "disk is full",
                "disk full",
                "volume full",
                "volume is full",
                "no space left",
                "not enough space",
                "-11807",
                "nscocoaerrordomain (640)",
                "nsposixerrordomain (28)",
                "nsosstatuserrordomain (-34)",
            )
        ):
            return False
        found |= isinstance(current, SystemCodecsError)
        current = current.__cause__ or current.__context__
    return found


def automatic_export(
    frames,
    destination,
    *,
    export,
    cancel_event,
    temporary_directory,
    note_callback,
    warning_callback,
):
    """Try HEVC, retaining only a bounded, complete prefix for AV1 recovery."""
    iterator = iter(frames)
    count = 0
    size = 0
    cache = None
    try:
        try:
            cache = tempfile.TemporaryFile(dir=temporary_directory, buffering=0)
        except OSError:
            pass

        def record():
            nonlocal cache, count, size
            for frame in iterator:
                if cache is not None:
                    frame_size = frame.rgb.nbytes
                    if size + frame_size > REPLAY_LIMIT_BYTES:
                        with suppress(OSError):
                            cache.close()
                        cache = None
                    else:
                        try:
                            pickle.dump(frame, cache, protocol=pickle.HIGHEST_PROTOCOL)
                            count += 1
                            size += frame_size
                        except OSError:
                            with suppress(OSError):
                                cache.close()
                            cache = None
                yield frame

        try:
            return export(record(), "hevc")
        except Exception as error:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("video job cancelled") from error
            if not system_export_failure(error):
                raise
            if cache is None:
                raise SystemCodecsError(
                    f"{error}. Choose Automatic or AV1 under Advanced > Video format. "
                    "The video encoder stopped part-way; run the job again with "
                    "AV1 under Advanced > Video format."
                ) from error
            if warning_callback:
                warning_callback(f"HEVC export failed on this Mac; retrying with AV1. {error}")
        cache.seek(0)

        def replay():
            for _ in range(count):
                if cancel_event is not None and cancel_event.is_set():
                    raise InterruptedError("video job cancelled")
                yield pickle.load(cache)
            yield from iterator

        result = export(replay(), "av1")
        if note_callback:
            note_callback("HEVC export failed on this Mac; the video was written as AV1 instead.")
        return result
    finally:
        if cache is not None:
            with suppress(OSError):
                cache.close()
        close = getattr(iterator, "close", None)
        if close:
            close()
