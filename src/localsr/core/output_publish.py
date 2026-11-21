"""Publish a finished output without ever replacing a file that is already there."""

from __future__ import annotations

import ctypes
import errno
import os
import sys
import tempfile
import threading
from pathlib import Path, PureWindowsPath


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

    The temporary lies in the destination's own folder, so no step copies bytes.
    Each step refuses an existing name and leaves that file untouched:

    1. A hard link (APFS, NTFS, ext4) is atomic; the temporary is removed afterwards.
    2. Where the file system has no hard links (exFAT/FAT sticks, some network
       shares), an atomic rename that refuses to replace: ``os.rename`` on
       Windows, ``renameat2`` on Linux, ``renamex_np`` on macOS. The final name
       is either absent or complete.
    3. Where that is not available either, the name is claimed with an empty
       file and the temporary is renamed over it. The final name never holds
       partial content, but if the process dies between the two operations it
       holds an empty file while the complete result stays in the temporary.

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
        if not _rename_without_replacing(temporary, destination):
            _claim_then_replace(temporary, destination)
        return
    # The callers remove the temporary again if this fails.
    try:
        temporary.unlink()
    except OSError:
        pass


# "Not supported here" answers of the no-replace rename calls; anything else is a real error.
_UNSUPPORTED = {
    code
    for code in (
        getattr(errno, name, None) for name in ("ENOSYS", "EINVAL", "ENOTSUP", "EOPNOTSUPP")
    )
    if code is not None
}


def _rename_without_replacing(temporary: Path, destination: Path) -> bool:
    """Step 2. False when this system or file system cannot do it."""
    try:
        return _native_rename_noreplace(temporary, destination)
    except FileExistsError:
        raise OutputExistsError(destination) from None
    except OSError as error:
        if error.errno in _UNSUPPORTED:
            return False
        raise


def _native_rename_noreplace(temporary: Path, destination: Path) -> bool:
    """Rename without replacement; return False when the native call is unavailable."""
    if sys.platform == "win32":
        os.rename(temporary, destination)  # Windows refuses an existing destination.
        return True
    if sys.platform != "darwin" and not sys.platform.startswith("linux"):
        return False
    try:
        library = ctypes.CDLL(None, use_errno=True)
        old, new = os.fsencode(temporary), os.fsencode(destination)
        if sys.platform == "darwin":
            call, arguments = library.renamex_np, (old, new, 0x4)  # RENAME_EXCL
        elif sys.platform.startswith("linux"):
            # AT_FDCWD is -100; RENAME_NOREPLACE is 1.
            call, arguments = library.renameat2, (-100, old, -100, new, 1)
        else:
            return False
    except (AttributeError, OSError):
        return False
    ctypes.set_errno(0)
    if call(*arguments) == 0:
        return True
    code = ctypes.get_errno()
    if code == errno.EEXIST:
        raise FileExistsError(code, os.strerror(code))
    raise OSError(code, os.strerror(code))


def _claim_then_replace(temporary: Path, destination: Path) -> None:
    """Step 3: an empty file this call created holds the name until the result replaces it."""
    try:
        claim = open(destination, "xb")
    except FileExistsError:
        raise OutputExistsError(destination) from None
    with claim:
        made = os.fstat(claim.fileno())
    try:
        os.replace(temporary, destination)
    except BaseException:
        # Remove the placeholder only while it is still the empty file created above.
        try:
            now = os.stat(destination)
            if (now.st_ino, now.st_dev) == (made.st_ino, made.st_dev) and now.st_size == 0:
                destination.unlink()
        except OSError:
            pass
        raise


def owned_temporary(directory: str | os.PathLike, phase: str) -> Path:
    """Create an empty, hidden temporary file that LocalSR owns, next to the output.

    The name is short and never contains the destination's name, so an output name the
    file system accepts is not made unusable by the temporary beside it. The same
    directory keeps the final publication on one volume.
    """
    descriptor, name = tempfile.mkstemp(prefix=f".localsr-{phase}-", suffix=".tmp", dir=directory)
    os.close(descriptor)
    return Path(name)


# What most file systems allow for one name when the folder does not say otherwise.
DEFAULT_NAME_LIMIT = 255


def output_name_length(name: str) -> int:
    """The length the file system counts: UTF-16 units on APFS and NTFS, bytes on Linux."""
    if sys.platform in ("darwin", "win32"):
        return len(name.encode("utf-16-le")) // 2
    return len(os.fsencode(name))


def output_name_limit(directory: str | os.PathLike) -> int:
    """The longest file name ``directory`` can hold; the usual 255 where it cannot be asked."""
    try:
        return int(os.pathconf(directory, "PC_NAME_MAX"))
    except (AttributeError, ValueError, OSError):
        return DEFAULT_NAME_LIMIT


# Windows counts the whole path, with its terminating NUL, against this unless long paths work.
WINDOWS_PATH_LIMIT = 260
WINDOWS_LONG_PATH_LIMIT = 32767
# The longest hidden temporary name ``owned_temporary`` makes: ".localsr-" + phase + "-" + 8 + ".tmp".
LONGEST_TEMPORARY_NAME = 40


def windows_path_limit() -> int:
    """The path length (with NUL) Windows accepts for this process.

    The larger limit needs the registry switch and a long-path-aware process; if either
    cannot be established, the classic 260 applies.
    """
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem"
        ) as key:
            enabled = winreg.QueryValueEx(key, "LongPathsEnabled")[0] == 1
        if enabled and ctypes.windll.ntdll.RtlAreLongPathsEnabled():
            return WINDOWS_LONG_PATH_LIMIT
    except (ImportError, OSError, AttributeError):
        pass
    return WINDOWS_PATH_LIMIT


def require_output_path_fits(path: str, platform: str, limit: int) -> None:
    """Refuse a destination, or the temporary beside it, whose full path Windows cannot open.

    ``limit`` counts UTF-16 units including the terminating NUL. Other platforms are
    limited by the name only, which ``require_output_name`` checks.
    """
    if platform != "win32" or path.startswith("\\\\?\\"):
        return
    folder = str(PureWindowsPath(path).parent).rstrip("\\")
    longest = max(
        len(path.encode("utf-16-le")) // 2,
        len(folder.encode("utf-16-le")) // 2 + 1 + LONGEST_TEMPORARY_NAME,
    )
    if longest + 1 > limit:
        shown = path if len(path) <= 60 else path[:28] + "..." + path[-29:]
        raise ValueError(
            f'The output path "{shown}" is too long for Windows, which allows '
            f"{limit - 1} characters for a full path (and the temporary file beside the "
            "result needs room too). Choose a shorter folder or file name."
        )


def require_output_name(path: str | os.PathLike) -> None:
    """Refuse an output name the file system cannot hold, before any work is done."""
    name = Path(path).name
    length, limit = output_name_length(name), output_name_limit(Path(path).parent)
    if length > limit:
        shown = name if len(name) <= 40 else name[:37] + "..."
        raise ValueError(
            f'The output file name "{shown}" is {length} characters long, but this folder '
            f"allows at most {limit}. Shorten the name and try again."
        )
    if sys.platform == "win32":
        require_output_path_fits(os.path.abspath(path), sys.platform, windows_path_limit())
