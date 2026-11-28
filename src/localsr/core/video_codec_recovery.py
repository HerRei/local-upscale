"""One Automatic encoder retry, replaying rendered frames from private scratch."""

from __future__ import annotations

import errno
import tempfile
from contextlib import suppress
from fractions import Fraction

import numpy as np

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
    from .video_io import TimedVideoFrame

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
                    pixels = frame.rgb if isinstance(frame, TimedVideoFrame) else frame
                    frame_size = pixels.nbytes
                    if size + frame_size > REPLAY_LIMIT_BYTES:
                        with suppress(OSError):
                            cache.close()
                        cache = None
                    else:
                        try:
                            # Plain arrays only, read back without pickle: the pixels, then
                            # the frame's index and times as exact text.
                            timing = (
                                [
                                    str(frame.index),
                                    *(
                                        str(Fraction(value))
                                        for value in (
                                            frame.timestamp,
                                            frame.duration,
                                            frame.time_base,
                                        )
                                    ),
                                ]
                                if isinstance(frame, TimedVideoFrame)
                                else []
                            )
                            np.save(cache, pixels, allow_pickle=False)
                            np.save(cache, np.array(timing, dtype=str), allow_pickle=False)
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
                pixels = np.load(cache, allow_pickle=False)
                if not (pixels.flags.owndata and pixels.flags.writeable):
                    pixels = pixels.copy()
                timing = np.load(cache, allow_pickle=False)
                yield (
                    TimedVideoFrame(
                        int(timing[0]), pixels, *(Fraction(str(value)) for value in timing[1:])
                    )
                    if timing.size
                    else pixels
                )
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
