import os
import sys
import tempfile
import time
import warnings
from pathlib import Path

import numpy as np


def default_work_directory() -> Path:
    override = os.environ.get("LOCALSR_WORK_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "LocalSR" / "work"
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "LocalSR" / "work"
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "LocalSR" / "work"


def cleanup_stale_work_files(
    directory: str | os.PathLike | None = None, max_age_hours: int = 48
) -> int:
    """Remove only expired LocalSR-owned memmaps from the exact work directory."""
    root = Path(directory) if directory is not None else default_work_directory()
    try:
        root.mkdir(parents=True, exist_ok=True)
        resolved = root.resolve(strict=True)
    except OSError:
        return 0
    if resolved == Path(resolved.anchor) or len(resolved.parts) < 3:
        raise ValueError("refusing to clean an unsafe LocalSR work directory")
    cutoff = time.time() - max(1, max_age_hours) * 3600
    removed = 0
    for candidate in resolved.glob("localsr-output-*.dat"):
        try:
            if candidate.is_file() and candidate.stat().st_mtime < cutoff:
                candidate.unlink()
                removed += 1
        except OSError:
            continue
    return removed


class OutputWriter:
    def __init__(
        self,
        shape: tuple[int, int, int],
        dtype=np.uint8,
        temporary_directory: str | os.PathLike | None = None,
    ):
        """
        shape is (C, H, W).
        Creates a temporary memmap.
        """
        self.shape = shape
        self.dtype = dtype
        self.mmap = None

        work_directory = (
            Path(temporary_directory)
            if temporary_directory is not None
            else default_work_directory()
        )
        work_directory.mkdir(parents=True, exist_ok=True)
        fd, self.temp_path = tempfile.mkstemp(
            prefix="localsr-output-", suffix=".dat", dir=work_directory
        )
        os.close(fd)
        try:
            self.mmap = np.memmap(
                self.temp_path,
                dtype=self.dtype,
                mode="w+",
                shape=self.shape,
            )
        except BaseException:
            # mkstemp has already created the file. If memmap construction fails,
            # no OutputWriter instance is returned to the caller, so __exit__ or
            # cleanup cannot run later.
            try:
                os.unlink(self.temp_path)
            except FileNotFoundError:
                pass
            raise

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()

    def write_tile(self, data: np.ndarray, x: int, y: int):
        """
        data should be (C, H, W).
        """
        _, h, w = data.shape
        self.mmap[:, y : y + h, x : x + w] = data
        self.mmap.flush()

    def get_array(self) -> np.ndarray:
        return self.mmap

    def get_path(self) -> str:
        return self.temp_path

    def close(self):
        mmap_array = self.mmap
        self.mmap = None
        if mmap_array is not None:
            try:
                mmap_array.flush()
            except (OSError, ValueError):
                # A failed inference or externally closed mapping may no longer
                # be flushable. Closing the underlying mapping is still safe.
                pass

            underlying_mmap = getattr(mmap_array, "_mmap", None)
            if underlying_mmap is not None and not underlying_mmap.closed:
                underlying_mmap.close()

    def cleanup(self):
        self.close()
        if self.temp_path:
            try:
                os.unlink(self.temp_path)
            except FileNotFoundError:
                pass
            except OSError as error:
                warnings.warn(
                    f"Could not remove LocalSR temporary output {self.temp_path}: {error}",
                    RuntimeWarning,
                    stacklevel=2,
                )
