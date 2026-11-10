"""How much disk a job needs on its output's volume, and how much must stay free.

A job writes an output memmap and an encoded temporary file beside the result; the
final name is a hard link to (or a rename of) that temporary, so it is not counted
twice. A job whose files do not fit into the free space minus a reserve must not
start, and a write that still hits a full disk reads as a sentence, not an errno.

The desktop host mirrors ``image_need`` and ``reserve_for`` in
``desktop/src-tauri/src/disk_room.rs`` to refuse early; both are checked against
``tests/fixtures/disk_estimate_cases.json``. This module decides for the engine.
"""

from __future__ import annotations

import errno
import os
import shutil
import sys
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from localsr.core.batch_scheduler import max_open_photos

# The system volume keeps the larger of this and 5 % of its size free; others keep less. The
# floor is what swap can grow by while the engine works: a 12 MP photo at 4x peaks near
# 5.8 GB of memory, the desktop app about 1 GB more, and macOS pages memory out to this volume.
SYSTEM_RESERVE_FLOOR = 8_000_000_000
OTHER_RESERVE = 1_000_000_000
# Headers, an ICC profile and EXIF around the pixels.
_SLACK = 1 << 20
# The output memmap holds uint8 samples.
_MEMMAP_CHANNELS = 3

disk_usage = shutil.disk_usage


@dataclass(frozen=True)
class Volume:
    device: int
    free: int
    total: int
    system: bool


def _existing(folder: str | os.PathLike) -> Path:
    """The nearest folder that exists: a work or output folder may not be made yet."""
    path = Path(folder)
    while not path.exists() and path != path.parent:
        path = path.parent
    return path


def _system_devices() -> set[int]:
    roots = [os.path.abspath(os.sep)]
    if sys.platform == "win32":
        roots.append(os.environ.get("SystemDrive", "C:") + "\\")
    try:
        roots.append(str(Path.home()))
    except RuntimeError:  # no home folder is known
        pass
    devices = set()
    for root in roots:
        try:
            devices.add(os.stat(root).st_dev)
        except OSError:
            continue
    return devices


def volume_of(folder: str | os.PathLike) -> Volume:
    path = _existing(folder)
    usage = disk_usage(path)
    device = os.stat(path).st_dev
    return Volume(device, int(usage.free), int(usage.total), device in _system_devices())


def reserve_for(volume: Volume) -> int:
    """What must stay free: the system volume keeps max(8 GB, 5 %), any other 1 GB.

    ``LOCALSR_DISK_RESERVE_BYTES`` replaces the rule; the test suite sets it to 0, so that
    its small jobs do not depend on the free space of the machine that runs them.
    """
    override = os.environ.get("LOCALSR_DISK_RESERVE_BYTES", "")
    if override.isdecimal():
        return int(override)
    if not volume.system:
        return OTHER_RESERVE
    return max(SYSTEM_RESERVE_FLOOR, volume.total // 20)


def format_size(nbytes: int) -> str:
    """Decimal units, as the app's other size messages."""
    if nbytes >= 1_000_000_000:
        return f"{nbytes / 1e9:.1f} GB"
    if nbytes >= 1_000_000:
        return f"{nbytes / 1e6:.0f} MB"
    return f"{max(nbytes, 0) / 1e3:.0f} KB"


def _shown(folder: str | os.PathLike) -> Path:
    """The output folder, not the hidden per-job folder the host makes inside it."""
    path = Path(folder)
    return path.parent if path.name.startswith(".localsr-job-") else path


class DiskFullError(Exception):
    """The disk cannot take a job's files; the message is the sentence the user reads."""


def _refusal(folder: str | os.PathLike, need: int, volume: Volume) -> DiskFullError:
    return DiskFullError(
        f'Not enough free disk space in "{_shown(folder)}": about {format_size(need)} is '
        f"needed, but only {format_size(volume.free)} is free, and "
        f"{format_size(reserve_for(volume))} must stay free so the computer keeps working."
    )


def require_room(
    claims: Iterable[tuple[str | os.PathLike, int]],
    *,
    lookup: Callable[[str | os.PathLike], Volume] | None = None,
) -> None:
    """Refuse when the bytes wanted in each folder do not fit its volume minus the reserve.

    Folders on one volume add up; the claims of different volumes are separate.
    """
    wanted: dict[int, list[Any]] = {}
    for folder, nbytes in claims:
        volume = (lookup or volume_of)(folder)
        entry = wanted.setdefault(volume.device, [volume, 0, folder])
        entry[1] += nbytes
    for volume, need, folder in wanted.values():
        if volume.free - reserve_for(volume) < need:
            raise _refusal(folder, need, volume)


def encoded_bound(fmt: str, width: int, height: int, alpha: bool = False) -> int:
    """At most this many bytes for a ``width`` x ``height`` picture in ``fmt``.

    PNG and TIFF (deflate) never exceed the raw samples by more than their framing;
    the lossy formats are bounds for noisy pictures at the highest quality offered,
    not predictions.
    """
    pixels = width * height
    name = fmt.lower()
    if name in ("jpg", "jpeg"):
        return 2 * pixels + _SLACK
    if name == "heic":
        return pixels + _SLACK
    if name == "webp":
        return (3 if alpha else 2) * pixels + _SLACK
    raw = (4 if alpha else 3) * pixels
    return raw + raw // 256 + height + _SLACK


@dataclass(frozen=True)
class ImageNeed:
    scratch: int  # the output memmap, at the model's native scale
    encoded: int  # the encoded temporary; the final name links to it

    @property
    def total(self) -> int:
        return self.scratch + self.encoded

    def in_flight(self, jobs: int) -> int:
        return jobs * self.total


def image_need(
    width: int,
    height: int,
    scale: int,
    output_scale: int | None,
    fmt: str,
    alpha: bool = False,
) -> ImageNeed:
    """Peak bytes one photo of ``width`` x ``height`` needs: its memmap and its encoded file."""
    final = int(output_scale or scale)
    return ImageNeed(
        scratch=_MEMMAP_CHANNELS * width * scale * height * scale,
        encoded=encoded_bound(fmt, round(width * final), round(height * final), alpha),
    )


def job_folders(data: Mapping[str, Any]) -> tuple[Path, Path]:
    """Where an image job's memmap and its encoded temporary go."""
    from localsr.core.output_writer import default_work_directory

    scratch = Path(data["scratch_directory"]) if data.get("scratch_directory") else None
    temporary = data.get("output_temporary_directory") or Path(data["output_path"]).parent
    return scratch or default_work_directory(), Path(temporary)


def image_claims(data: Mapping[str, Any], need: ImageNeed, encoders: int = 0) -> list:
    """The folders a photo, or a batch with ``encoders`` encoder threads, writes into."""
    memmap_folder, temporary_folder = job_folders(data)
    if not encoders:
        return [(memmap_folder, need.scratch), (temporary_folder, need.encoded)]
    return [
        (memmap_folder, max_open_photos(encoders) * need.scratch),
        (temporary_folder, encoders * need.encoded),
    ]


def require_image_room(
    data: Mapping[str, Any], width: int, height: int, scale: int, alpha: bool = False
) -> None:
    need = image_need(
        width, height, scale, data.get("output_scale"), str(data["output_format"]), alpha
    )
    require_room(image_claims(data, need))


# Video temporaries: the encoded video and the muxed file exist together.
_VIDEO_BYTES_PER_PIXEL_FRAME = 20


def video_bytes(width: int, height: int, frames: int, master: bool = False) -> int:
    """At most this much for an output video: two encoded files, and a lossless master
    (about 0.9 byte per pixel and frame) when a program outside LocalSR encodes the result."""
    encoded = width * height * max(frames, 1) // _VIDEO_BYTES_PER_PIXEL_FRAME + _SLACK
    lossless = int(width * height * max(frames, 1) * 0.9) if master else 0
    return 2 * encoded + lossless


def require_video_room(
    data: Mapping[str, Any], width: int, height: int, frames: int, master: bool = False
) -> None:
    folder = data.get("temporary_directory") or Path(data["output_video_path"]).parent
    require_room([(folder, video_bytes(width, height, frames, master))])


def edit_bytes(max_dimension: int) -> int:
    """The edit's reference and result pictures, both PNG at most ``max_dimension`` square."""
    return 2 * encoded_bound("png", max_dimension, max_dimension)


_FULL = {errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC)}


def disk_full_from(error: BaseException, folder: str | os.PathLike | None) -> DiskFullError | None:
    """The sentence for an error that is, or was caused by, a full disk; None for any other."""
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        if isinstance(current, DiskFullError):
            return current
        if isinstance(current, OSError) and current.errno in _FULL:
            where = folder or (Path(current.filename).parent if current.filename else None)
            if where is None:
                return DiskFullError("No space was left on the disk to finish writing.")
            where = _shown(where)
            try:
                free = f" ({format_size(volume_of(where).free)} free)"
            except OSError:
                free = ""
            return DiskFullError(f'No space was left on the disk to write in "{where}"{free}.')
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return None


def failure_fields(error: BaseException, output_path: str | os.PathLike | None) -> dict[str, Any]:
    """The ``job_failed`` fields for ``error``: the full-disk sentence and flag, or its text."""
    full = disk_full_from(error, Path(output_path).parent if output_path else None)
    if full is None:
        return {"error_message": str(error)}
    return {"error_message": str(full), "disk_full": True}
