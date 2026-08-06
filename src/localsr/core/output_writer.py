import os
import tempfile
import warnings

import numpy as np


class OutputWriter:
    def __init__(self, shape: tuple[int, int, int], dtype=np.uint8):
        """
        shape is (C, H, W).
        Creates a temporary memmap.
        """
        self.shape = shape
        self.dtype = dtype
        self.mmap = None

        fd, self.temp_path = tempfile.mkstemp(prefix="localsr_", suffix=".dat")
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
