"""Publish a finished output without ever replacing a file that is already there."""

from __future__ import annotations

import os
import shutil
import threading
from pathlib import Path


class OutputExistsError(FileExistsError):
    """A file appeared at the output's name while the job ran; it was left untouched."""

    def __init__(self, destination: str | os.PathLike):
        self.destination = Path(destination)
        super().__init__(
            f'A file named "{self.destination.name}" already exists in the output folder. '
            "Nothing was replaced, and this result was not saved."
        )


def publish_output(
    temporary: str | os.PathLike,
    destination: str | os.PathLike,
    cancel_event: threading.Event | None = None,
) -> None:
    """Give a finished temporary file its final name, failing if that name is taken.

    A hard link is atomic and refuses an existing name on APFS, NTFS and ext4;
    the temporary is removed afterwards. Where the file system has no hard
    links (exFAT/FAT sticks, some network shares), the file is copied into an
    exclusively created destination instead. That never replaces a file either,
    but it is not atomic: another program can see the name while it is still
    being filled, and a crash mid-copy leaves a partial file. A copy that fails
    is removed again.

    Raises ``OutputExistsError`` when the name is taken. The temporary stays in
    place for the caller to remove on any failure.
    """
    temporary, destination = Path(temporary), Path(destination)
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("Cancelled before the output was saved.")
    try:
        os.link(temporary, destination)
    except FileExistsError:
        raise OutputExistsError(destination) from None
    except OSError:
        _copy_exclusively(temporary, destination)
    # The callers remove the temporary again if this fails.
    try:
        temporary.unlink()
    except OSError:
        pass


def _copy_exclusively(temporary: Path, destination: Path) -> None:
    try:
        target = open(destination, "xb")
    except FileExistsError:
        raise OutputExistsError(destination) from None
    try:
        with target, open(temporary, "rb") as source:
            shutil.copyfileobj(source, target, 1024 * 1024)
            target.flush()
            os.fsync(target.fileno())
    except BaseException:
        # Only this call created the file, so it may remove it.
        try:
            destination.unlink()
        except OSError:
            pass
        raise
