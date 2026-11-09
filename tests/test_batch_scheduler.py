"""The shared tile queue, driven by fake lanes: no Apple runtime or GPU is needed."""

import random
import threading
import time
from collections import Counter

import pytest

from localsr.core.batch_scheduler import PhotoFallback, run_photo_batch

pytestmark = pytest.mark.timeout(30)


class Photo:
    """Records what the scheduler does with it; encodes to its key."""

    def __init__(self, key, tiles=5, log=None):
        self.key = key
        self.tiles = list(range(tiles))
        self.stitched = {}
        self.dispatched = []
        self.started = self.closed = False
        self.log = log if log is not None else []

    def start(self):
        assert not self.started and not self.closed
        self.started = True
        self.log.append(("start", self.key))

    def dispatch(self, tile):
        assert self.started and not self.closed
        self.dispatched.append(tile)
        self.log.append(("dispatch", self.key, tile))

    def stitch(self, tile, value):
        assert not self.closed and tile not in self.stitched
        self.stitched[tile] = value

    def encode(self):
        assert self.stitched == {tile: (self.key, tile) for tile in self.tiles}
        return self.key

    def close(self):
        assert not self.closed
        self.closed = True


def echo(photo, tile):
    return photo.key, tile


def run(items, open_photo, lanes, cancel=None, **kwargs):
    outcomes = []
    deferred = run_photo_batch(
        items,
        open_photo=open_photo,
        lanes=lanes,
        cancel=cancel or threading.Event(),
        finished=lambda key, result: outcomes.append((key, "done", result)),
        failed=lambda key, error: outcomes.append((key, "failed", str(error))),
        cancelled=lambda key: outcomes.append((key, "cancelled", None)),
        **kwargs,
    )
    return outcomes, deferred


def test_one_queue_feeds_every_lane_and_each_photo_is_stitched_from_its_own_tiles():
    photos, calls = {}, []
    lock = threading.Lock()
    jitter = random.Random(4)

    def open_photo(key):
        photos[key] = Photo(key, tiles=7)
        return photos[key]

    def lane(photo, tile):
        with lock:
            delay = jitter.random() * 0.004
        time.sleep(delay)
        with lock:
            calls.append((photo.key, tile, threading.current_thread().name))
        return photo.key, tile

    outcomes, deferred = run(range(9), open_photo, [lane] * 4)
    assert deferred == []
    assert sorted(outcomes) == [(key, "done", key) for key in range(9)]
    assert Counter((key, tile) for key, tile, _ in calls) == Counter(
        (key, tile) for key in range(9) for tile in range(7)
    )
    assert len({name for _, _, name in calls}) == 4
    assert all(photo.closed for photo in photos.values())


def test_photos_start_and_enter_the_queue_in_order():
    log = []
    outcomes, _ = run(range(6), lambda key: Photo(key, 4, log), [echo] * 4)
    assert len(outcomes) == 6
    dispatches = [entry[1] for entry in log if entry[0] == "dispatch"]
    assert dispatches == sorted(dispatches), "a photo's tiles queue only after the previous one's"
    starts = [entry[1] for entry in log if entry[0] == "start"]
    assert starts == list(range(6))
    # A photo starts only once the photo before it has queued every tile.
    for key in range(1, 6):
        start = log.index(("start", key))
        assert log.index(("dispatch", key - 1, 3)) < start


def test_the_next_photo_is_decoded_while_the_current_one_is_inferred():
    decoded_second = threading.Event()

    def open_photo(key):
        if key == 1:
            decoded_second.set()
        return Photo(key, tiles=3)

    def lane(photo, tile):
        if photo.key == 0 and tile == 2:
            # Deadlocks (and times out) if decoding waited for inference.
            assert decoded_second.wait(5)
        return photo.key, tile

    outcomes, _ = run(range(3), open_photo, [lane, lane])
    assert sorted(outcomes) == [(key, "done", key) for key in range(3)]


@pytest.mark.parametrize("phase", ["open", "start", "dispatch", "stitch", "encode"])
def test_a_failing_photo_does_not_fail_the_others(phase):
    photos = []

    def open_photo(key):
        if key == 1 and phase == "open":
            raise ValueError("bad decode")
        photo = Photo(key)
        photos.append(photo)
        if key == 1 and phase != "open":
            original = getattr(photo, phase)

            def fail(*args):
                original(*args)
                raise ValueError(f"bad {phase}")

            setattr(photo, phase, fail)
        return photo

    outcomes, deferred = run(range(5), open_photo, [echo] * 3)
    assert deferred == []
    assert sorted((key, status) for key, status, _ in outcomes) == [
        (0, "done"),
        (1, "failed"),
        (2, "done"),
        (3, "done"),
        (4, "done"),
    ]
    assert dict((key, text) for key, _, text in outcomes)[1].startswith("bad")
    assert all(photo.closed for photo in photos)


def test_a_tile_the_lanes_cannot_render_sends_only_its_photo_back_to_the_normal_path():
    photos, calls = [], []

    def open_photo(key):
        photos.append(Photo(key, tiles=6))
        return photos[-1]

    def lane(photo, tile):
        calls.append((photo.key, tile))
        if photo.key == 2 and tile == 1:
            raise PhotoFallback("SPAN produced extreme pixel values.")
        return photo.key, tile

    outcomes, deferred = run(range(5), open_photo, [lane] * 2)
    assert deferred == [2]
    assert sorted(outcomes) == [(key, "done", key) for key in (0, 1, 3, 4)]
    # The other photos' tiles were all rendered; the fallback photo stopped early.
    assert sum(1 for key, _ in calls if key == 2) < 6
    assert all(photo.closed for photo in photos)


def test_a_failing_lane_retires_and_its_tile_runs_on_another_lane():
    calls = Counter()
    errors = []

    class Broken:
        def __call__(self, photo, tile):
            calls["broken"] += 1
            raise RuntimeError("Core ML GPU lane failed")

    def good(photo, tile):
        calls["good"] += 1
        time.sleep(0.001)
        return photo.key, tile

    outcomes, deferred = run(range(4), Photo, [Broken(), good], lane_failed=errors.append)
    assert deferred == []
    assert sorted(outcomes) == [(key, "done", key) for key in range(4)]
    assert calls["broken"] == 1 and calls["good"] == 20
    assert [str(error) for error in errors] == ["Core ML GPU lane failed"]


def test_when_every_lane_fails_the_unfinished_photos_go_to_the_normal_path_in_order():
    photos = []

    def open_photo(key):
        photos.append(Photo(key, tiles=2))
        return photos[-1]

    def lane(photo, tile):
        if photo.key >= 2:
            raise RuntimeError("lane lost")
        return photo.key, tile

    outcomes, deferred = run(range(6), open_photo, [lane, lane])
    assert sorted(outcomes) == [(0, "done", 0), (1, "done", 1)]
    assert deferred == [2, 3, 4, 5]
    assert all(photo.closed for photo in photos)


def test_slow_encoders_bound_the_photos_in_memory_and_the_tiles_in_flight():
    release, saturated = threading.Event(), threading.Event()
    lock = threading.Lock()
    counts = Counter()

    def open_photo(key):
        photo = Photo(key, tiles=3)
        with lock:
            counts["live"] += 1
            counts["peak"] = max(counts["peak"], counts["live"])
            if counts["live"] == 6:
                saturated.set()

        def encode():
            with lock:
                counts["active"] += 1
                counts["peak_active"] = max(counts["peak_active"], counts["active"])
            assert release.wait(10)
            with lock:
                counts["active"] -= 1
            return key

        def close():
            with lock:
                counts["live"] -= 1

        photo.encode, photo.close = encode, close
        return photo

    def lane(photo, tile):
        with lock:
            counts["tiles"] += 1
            counts["peak_tiles"] = max(counts["peak_tiles"], counts["tiles"])
        time.sleep(0.001)
        with lock:
            counts["tiles"] -= 1
        return photo.key, tile

    outcomes = []
    worker = threading.Thread(
        target=lambda: outcomes.extend(run(range(30), open_photo, [lane] * 4, encoders=4)[0])
    )
    worker.start()
    try:
        assert saturated.wait(10)
        time.sleep(0.2)
        # Four encoding, one waiting for an encoder, one being inferred or decoded.
        assert counts["live"] == 6
        assert counts["peak_active"] == 4
    finally:
        release.set()
        worker.join(10)
    assert not worker.is_alive()
    assert sorted(outcomes) == [(key, "done", key) for key in range(30)]
    assert counts["live"] == 0 and counts["peak"] == 6
    assert counts["peak_tiles"] <= 4


def test_cancel_lets_native_calls_finish_before_cleanup_and_cancels_the_rest():
    cancel = threading.Event()
    photos, calls = [], []
    lock = threading.Lock()

    def open_photo(key):
        photos.append(Photo(key, tiles=20))
        return photos[-1]

    def lane(photo, tile):
        with lock:
            calls.append((photo.key, tile))
        cancel.set()
        time.sleep(0.05)
        assert not photo.closed, "a photo was released while a native call used it"
        return photo.key, tile

    started = time.monotonic()
    outcomes, deferred = run(range(50), open_photo, [lane] * 4, cancel=cancel)
    assert time.monotonic() - started < 2
    assert deferred == []
    assert sorted(outcomes) == [(key, "cancelled", None) for key in range(50)]
    assert len(calls) <= 4 and len(photos) <= 2
    assert all(photo.closed for photo in photos)


def test_cancel_while_encoders_are_full_does_not_deadlock_and_keeps_committed_saves():
    cancel = threading.Event()
    photos = []

    def open_photo(key):
        photo = Photo(key, tiles=1)
        photos.append(photo)

        def encode():
            if key == 0:
                return key  # Committed before the user cancelled.
            assert cancel.wait(5)
            raise InterruptedError("Cancelled during image export.")

        photo.encode = encode
        return photo

    timer = threading.Timer(0.2, cancel.set)
    timer.start()
    try:
        outcomes, _ = run(range(20), open_photo, [echo] * 2, cancel=cancel)
    finally:
        timer.cancel()
    statuses = dict((key, status) for key, status, _ in outcomes)
    assert statuses[0] == "done"
    assert len(outcomes) == 20
    assert all(status == "cancelled" for key, status in statuses.items() if key)
    assert all(photo.closed for photo in photos)


def test_a_cancelled_photo_is_never_reported_twice():
    cancel = threading.Event()

    def lane(photo, tile):
        if photo.key == 1:
            cancel.set()
        return photo.key, tile

    outcomes, _ = run(range(4), lambda key: Photo(key, 2), [lane], cancel=cancel)
    keys = [key for key, _, _ in outcomes]
    assert sorted(keys) == [0, 1, 2, 3]


def test_an_unavailable_lane_takes_no_tiles():
    class OnBattery:
        available = False

        def __call__(self, *_args):
            raise AssertionError("GPU lane used on battery")

    outcomes, _ = run(range(3), Photo, [echo, OnBattery()])
    assert sorted(outcomes) == [(key, "done", key) for key in range(3)]


def test_a_lane_that_becomes_available_joins_the_queue():
    class Gpu:
        def __init__(self):
            self.available = False
            self.calls = 0

        def __call__(self, photo, tile):
            self.calls += 1
            return photo.key, tile

    gpu = Gpu()

    def ane(photo, tile):
        gpu.available = True
        time.sleep(0.01)
        return photo.key, tile

    outcomes, _ = run(range(4), lambda key: Photo(key, 10), [ane, gpu])
    assert len(outcomes) == 4
    assert gpu.calls > 0


def test_an_error_outside_any_photo_stops_the_lanes_and_releases_every_photo():
    photos = []

    def open_photo(key):
        photos.append(Photo(key, tiles=3))
        return photos[-1]

    def finished(key, _result):
        raise OSError("the host connection closed")

    with pytest.raises(OSError, match="connection closed"):
        run_photo_batch(
            range(4),
            open_photo=open_photo,
            lanes=[echo, echo],
            cancel=threading.Event(),
            finished=finished,
            failed=lambda *_: None,
            cancelled=lambda *_: None,
        )
    assert photos and all(photo.closed for photo in photos)
    assert not any(t.name.startswith("localsr-batch") for t in threading.enumerate())


def test_lanes_and_encoders_are_required():
    with pytest.raises(ValueError):
        run([], Photo, [])
    with pytest.raises(ValueError):
        run([], Photo, [echo], encoders=0)
