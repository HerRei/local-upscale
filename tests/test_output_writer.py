import os
import time

import numpy as np
import pytest

from localsr.core.output_writer import OutputWriter, cleanup_stale_work_files


def test_output_writer_context_manager():
    path = ""
    with OutputWriter((3, 100, 100)) as writer:
        path = writer.get_path()
        assert os.path.exists(path)

        arr = np.zeros((3, 50, 50), dtype=np.uint8)
        writer.write_tile(arr, 0, 0)

    # After context manager, should be deleted
    assert not os.path.exists(path)


def test_output_writer_manual_cleanup():
    writer = OutputWriter((3, 10, 10))
    path = writer.get_path()
    assert os.path.exists(path)
    writer.cleanup()
    assert not os.path.exists(path)


def test_stale_cleanup_is_bounded_by_name_age_and_exact_directory(tmp_path):
    work = tmp_path / "LocalSR" / "work"
    work.mkdir(parents=True)
    expired = work / "localsr-output-expired.dat"
    fresh = work / "localsr-output-fresh.dat"
    unrelated = work / "keep-me.dat"
    for path in (expired, fresh, unrelated):
        path.write_bytes(b"fixture")
    old = time.time() - 72 * 3600
    os.utime(expired, (old, old))

    assert cleanup_stale_work_files(work, max_age_hours=48) == 1
    assert not expired.exists()
    assert fresh.exists()
    assert unrelated.exists()


def test_stale_cleanup_rejects_broad_root_target(tmp_path):
    with pytest.raises(ValueError, match="unsafe"):
        cleanup_stale_work_files(tmp_path.anchor)
