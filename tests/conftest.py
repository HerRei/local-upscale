"""Suite-wide test isolation checks."""

import threading
import time

import pytest


@pytest.fixture(autouse=True)
def no_leaked_threads():
    before = set(threading.enumerate())
    yield
    deadline = time.monotonic() + 1
    while True:
        leaked = [
            thread
            for thread in threading.enumerate()
            if thread not in before
            and thread.is_alive()
            and (
                not thread.daemon
                or thread.name.startswith("localsr-")
                or thread.name in {"result-preview", "compare-regions"}
            )
        ]
        if not leaked or time.monotonic() >= deadline:
            break
        for thread in leaked:
            thread.join(timeout=max(0, deadline - time.monotonic()))
    assert not leaked, f"Test leaked threads: {leaked}"
