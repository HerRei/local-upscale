import os

import numpy as np

from localsr.core.output_writer import OutputWriter


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
