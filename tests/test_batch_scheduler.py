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

    lanes = [Broken(), good]
    outcomes, deferred = run(
        range(4), Photo, lanes, lane_failed=lambda lane, error: errors.append((lane, error))
    )
    assert deferred == []
    assert sorted(outcomes) == [(key, "done", key) for key in range(4)]
    assert calls["broken"] == 1 and calls["good"] == 20
    assert [(lane, str(error)) for lane, error in errors] == [(lanes[0], "Core ML GPU lane failed")]


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
    release, saturated, encoders_full = threading.Event(), threading.Event(), threading.Event()
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
                if counts["active"] == 4:
                    encoders_full.set()
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
        assert encoders_full.wait(10)
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

    outcomes = []

    def finished(key, result):
        outcomes.append((key, "done", result))
        if key == 0:
            cancel.set()

    run_photo_batch(
        range(20),
        open_photo=open_photo,
        lanes=[echo] * 2,
        cancel=cancel,
        finished=finished,
        failed=lambda key, error: outcomes.append((key, "failed", str(error))),
        cancelled=lambda key: outcomes.append((key, "cancelled", None)),
    )
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


class Idle:
    """A GPU lane on battery: alive, but it takes no tile."""

    available = False

    def __call__(self, *_args):
        raise AssertionError("an unavailable lane was used")


def broken(photo, tile):
    raise RuntimeError("Error computing NN outputs")


def test_when_only_idle_lanes_remain_the_photos_go_to_the_normal_path():
    # Both Neural Engine lanes fail; the GPU lanes are alive but on battery.
    started = time.monotonic()
    outcomes, deferred = run(range(5), Photo, [broken, broken, Idle(), Idle()])
    assert time.monotonic() - started < 5
    assert outcomes == [] and deferred == list(range(5))


def test_one_poison_tile_with_idle_lanes_left_cannot_stall_the_batch():
    def poison(photo, tile):
        if photo.key == 1 and tile == 2:
            raise RuntimeError("poison tile")
        return photo.key, tile

    outcomes, deferred = run(range(4), Photo, [poison, poison, Idle()])
    assert sorted(outcomes) == [(0, "done", 0)]
    assert deferred == [1, 2, 3]


def test_a_lane_interrupted_without_a_cancellation_retires_and_its_tile_runs_elsewhere():
    calls = Counter()

    def interrupted(photo, tile):
        calls["interrupted"] += 1
        raise InterruptedError("Cancelled")

    def good(photo, tile):
        time.sleep(0.001)
        return photo.key, tile

    outcomes, deferred = run(range(3), Photo, [interrupted, good])
    assert sorted(outcomes) == [(key, "done", key) for key in range(3)]
    assert deferred == [] and calls["interrupted"] == 1
    # With no other lane, the photos go back instead of waiting forever.
    outcomes, deferred = run(range(2), Photo, [interrupted])
    assert outcomes == [] and deferred == [0, 1]


def test_the_queue_holds_one_tile_per_lane():
    lock = threading.Lock()
    counts = Counter()

    class Counted(Photo):
        def dispatch(self, tile):
            super().dispatch(tile)
            with lock:
                counts["queued"] += 1
                counts["peak_queued"] = max(counts["peak_queued"], counts["queued"])
                counts["out"] += 1
                counts["peak_out"] = max(counts["peak_out"], counts["out"])

        def stitch(self, tile, value):
            super().stitch(tile, value)
            with lock:
                counts["out"] -= 1

    def slow(photo, tile):
        with lock:
            counts["queued"] -= 1
        time.sleep(0.01)
        return photo.key, tile

    outcomes, _ = run(range(4), lambda key: Counted(key, tiles=12), [slow] * 3)
    assert len(outcomes) == 4
    # Never more than one waiting tile per lane; with the one each lane renders
    # and results not yet stitched, a few tiles per lane at most exist at once.
    assert counts["peak_queued"] == 3
    assert counts["peak_out"] <= 4 * 3


def sized_photos(nbytes, counts, lock, release=None, hold=0.0, opened=None, saving=None):
    def open_photo(key):
        photo = Photo(key, tiles=1)
        photo.nbytes = nbytes

        def encode():
            with lock:
                counts["saving"] += nbytes
                counts["peak_saving"] = max(counts["peak_saving"], counts["saving"])
                counts["savers"] += 1
                counts["peak_savers"] = max(counts["peak_savers"], counts["savers"])
                if saving is not None:
                    saving.set()
            if release is not None:
                assert release.wait(10)
            time.sleep(hold)
            with lock:
                counts["saving"] -= nbytes
                counts["savers"] -= 1
            return key

        def close():
            Photo.close(photo)
            with lock:
                counts["open"] -= 1

        with lock:
            counts["open"] += 1
            counts["peak_open"] = max(counts["peak_open"], counts["open"])
            if opened is not None and counts["open"] == 2:
                opened.set()
        photo.encode, photo.close = encode, close
        return photo

    return open_photo


def test_saves_run_together_only_while_their_bytes_fit_the_budget():
    lock, counts = threading.Lock(), Counter()
    open_photo = sized_photos(400, counts, lock, hold=0.03)
    outcomes, _ = run(range(10), open_photo, [echo] * 2, encoders=6, memory_budget=1000)
    assert sorted(outcomes) == [(key, "done", key) for key in range(10)]
    assert counts["peak_saving"] <= 1000
    assert counts["peak_savers"] == 2


def test_a_photo_larger_than_the_budget_still_saves_alone():
    lock, counts = threading.Lock(), Counter()
    open_photo = sized_photos(5000, counts, lock, hold=0.01)
    outcomes, _ = run(range(4), open_photo, [echo], encoders=6, memory_budget=1000)
    assert len(outcomes) == 4 and counts["peak_savers"] == 1


def test_large_photos_stop_opening_at_the_budget():
    release, opened, saving = threading.Event(), threading.Event(), threading.Event()
    lock, counts = threading.Lock(), Counter()
    open_photo = sized_photos(600, counts, lock, release=release, opened=opened, saving=saving)
    outcomes = []
    worker = threading.Thread(
        target=lambda: outcomes.extend(
            run(range(8), open_photo, [echo] * 2, encoders=6, memory_budget=1000)[0]
        )
    )
    worker.start()
    try:
        assert opened.wait(10) and saving.wait(10)
        # One saving and one waiting for room; no third photo is decoded.
        assert counts["peak_open"] == 2 and counts["peak_savers"] == 1
    finally:
        release.set()
        worker.join(10)
    assert len(outcomes) == 8


def test_the_budget_holds_with_real_large_outputs():
    """Peak traced memory with 48 MB save buffers stays within the budget."""
    import tracemalloc

    import numpy as np

    size = 48 * 2**20

    class Large(Photo):
        nbytes = size

        def encode(self):
            buffer = np.ones(size, dtype=np.uint8)  # what Pillow holds during a save
            time.sleep(0.02)
            del buffer
            return super().encode()

    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        before = tracemalloc.get_traced_memory()[0]
        outcomes, _ = run(
            range(8), lambda key: Large(key, 2), [echo] * 2, encoders=6, memory_budget=2 * size
        )
        peak = tracemalloc.get_traced_memory()[1] - before
    finally:
        tracemalloc.stop()
    assert len(outcomes) == 8
    assert 1.5 * size < peak <= 2 * size + 8 * 2**20


def test_on_cancel_photos_outside_an_encoder_are_reported_while_a_save_still_runs():
    cancel, release = threading.Event(), threading.Event()
    outcomes = []
    cancelled_all = threading.Event()

    def cancelled(key):
        outcomes.append((key, "cancelled"))
        if len(outcomes) == 5:
            cancelled_all.set()

    def open_photo(key):
        photo = Photo(key, tiles=3)
        if key == 0:

            def encode():
                cancel.set()
                assert release.wait(10)
                return key  # Committed after the others were cancelled.

            photo.encode = encode
        return photo

    def slow(photo, tile):
        time.sleep(0.01)
        return photo.key, tile

    worker = threading.Thread(
        target=lambda: run_photo_batch(
            range(6),
            open_photo=open_photo,
            lanes=[slow] * 2,
            cancel=cancel,
            finished=lambda key, result: outcomes.append((key, "done")),
            failed=lambda key, error: outcomes.append((key, "failed")),
            cancelled=cancelled,
        )
    )
    worker.start()
    try:
        assert cancelled_all.wait(5)
        # Everything but the running save is reported before that save ends.
        assert sorted(outcomes) == [(key, "cancelled") for key in range(1, 6)]
    finally:
        release.set()
        worker.join(10)
    assert (0, "done") in outcomes and len(outcomes) == 6
