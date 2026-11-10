import ast
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from localsr.core import preparation
from localsr.core.preparation import PreparationTimeout, bounded_preparation, busy_slot


@pytest.fixture(autouse=True)
def fast_waits(monkeypatch):
    monkeypatch.setattr(preparation, "WAIT_SLICE_SECONDS", 0.02)
    monkeypatch.setattr(preparation, "STATUS_INTERVAL_SECONDS", 0.1)
    preparation._abandoned.clear()
    yield
    preparation._abandoned.clear()


def call(work, limit=1.0, slot="test", **options):
    return bounded_preparation(
        "Preparing X", limit, work, fallback="going on", slot=slot, **options
    )


def test_returns_the_result_and_reraises_the_errors_of_the_work():
    assert call(lambda: 7) == 7
    with pytest.raises(ValueError, match="bad"):
        call(lambda: (_ for _ in ()).throw(ValueError("bad")))


def test_gives_up_at_the_limit_and_leaves_one_thread_behind():
    release = threading.Event()
    started = time.monotonic()
    with pytest.raises(PreparationTimeout):
        call(release.wait, limit=0.2)
    assert time.monotonic() - started < 1.0
    assert busy_slot("test")
    # A second compile is not started on top of the first.
    began = time.monotonic()
    with pytest.raises(PreparationTimeout) as refused:
        call(lambda: 1)
    assert refused.value.busy and time.monotonic() - began < 0.1
    release.set()
    deadline = time.monotonic() + 2
    while busy_slot("test") and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not busy_slot("test")
    assert call(lambda: 2) == 2


def test_a_shared_deadline_cuts_the_limit_short():
    release = threading.Event()
    started = time.monotonic()
    with pytest.raises(PreparationTimeout):
        call(release.wait, limit=30.0, deadline=started + 0.2)
    assert time.monotonic() - started < 1.0
    release.set()


def test_cancellation_is_seen_within_a_second_and_the_helper_is_left_to_finish():
    release, cancel = threading.Event(), threading.Event()
    threading.Timer(0.1, cancel.set).start()
    started = time.monotonic()
    with pytest.raises(InterruptedError):
        call(release.wait, limit=30.0, cancel_event=cancel)
    assert time.monotonic() - started < 1.0
    release.set()


def test_a_cancelled_job_never_starts_the_work():
    cancel = threading.Event()
    cancel.set()
    ran = []
    with pytest.raises(InterruptedError):
        call(lambda: ran.append(1), cancel_event=cancel)
    assert not ran


def test_late_result_reaches_the_hook():
    release, seen = threading.Event(), []
    with pytest.raises(PreparationTimeout):
        call(lambda: [release.wait(), "done"][1], limit=0.1, on_late=lambda *a: seen.append(a))
    release.set()
    deadline = time.monotonic() + 2
    while not seen and time.monotonic() < deadline:
        time.sleep(0.01)
    assert seen == [("ok", "done")]


def test_status_is_repeated_with_elapsed_time_and_the_way_out():
    release, messages = threading.Event(), []
    threading.Timer(0.45, release.set).start()
    call(release.wait, limit=5.0, status=messages.append)
    assert len(messages) >= 4
    assert messages[0] == "Preparing X. 0 s so far; going on in 5 s if it is not ready."
    assert all("going on in" in message for message in messages)


# The guard: nothing in the engine may wait without a bound ----------------------

# Waits that are not preparation phases, each with the reason it cannot hang.
ALLOWED_WAITS = {
    ("core/batch_scheduler.py", "thread.join()"): "workers end after the stop flag is set",
    ("core/child_processes.py", "process.wait()"): "the process was just killed",
    ("core/external_ffmpeg.py", "code = process.wait()"): "an encode, not a preparation",
    ("core/gpu_guard.py", "process.wait()"): "the preflight child was just killed",
    ("core/onnx_runtime.py", "watcher.join()"): "the watcher ends within 0.1 s of the event",
    ("core/system_codecs.py", "process.wait()"): "the process was just killed",
    ("core/video_pipeline.py", "worker.join()"): "the frame producer stops with its consumer",
    ("desktop_launcher.py", "return subprocess.run("): "the app's launcher, not the engine",
    ("worker/edit_guard.py", "return process.wait()"): "the guarded process ends with the job",
}
BOUNDED_CALLS = {"join", "wait", "communicate"}
SUBPROCESS_CALLS = {"run", "check_output", "check_call", "call"}


def unbounded_waits() -> set[tuple[str, str]]:
    root = Path(__file__).parents[1] / "src" / "localsr"
    found = set()
    for path in sorted(root.rglob("*.py")):
        if "vendor" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        lines = source.splitlines()
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            name, receiver = node.func.attr, node.func.value
            has_timeout = any(keyword.arg == "timeout" for keyword in node.keywords)
            waits = name in BOUNDED_CALLS and not node.args and not has_timeout
            if name == "join" and isinstance(receiver, ast.Constant | ast.JoinedStr):
                waits = False
            runs = (
                name in SUBPROCESS_CALLS
                and isinstance(receiver, ast.Name)
                and receiver.id == "subprocess"
                and not has_timeout
            )
            if waits or runs:
                found.add((path.relative_to(root).as_posix(), lines[node.lineno - 1].strip()))
    return found


def test_the_engine_has_no_unbounded_wait_outside_the_helper():
    """A new thread join, event wait or subprocess call without a timeout fails here:
    give it a timeout, run the phase through bounded_preparation, or list it above
    with the reason it cannot hang."""
    found = unbounded_waits()
    assert found - set(ALLOWED_WAITS) == set()
    assert set(ALLOWED_WAITS) - found == set(), "an allowed wait is gone: drop it from the list"


def test_a_checkpoint_that_cannot_be_read_stops_the_job_with_a_sentence(monkeypatch, tmp_path):
    from localsr.core import model_adapter

    monkeypatch.setattr(model_adapter, "MODEL_READ_SECONDS", 0.2)
    release = threading.Event()
    path = tmp_path / "model.pth"
    path.write_bytes(b"x")
    adapter = model_adapter.ModelAdapter(allow_unverified_checkpoints=True)
    adapter.loader = SimpleNamespace(load_from_file=lambda _path: release.wait())
    messages = []
    adapter.status_callback = messages.append
    started = time.monotonic()
    with pytest.raises(RuntimeError, match=r"Reading the model file model\.pth did not finish"):
        adapter.inspect(str(path))
    assert time.monotonic() - started < 2.0
    assert messages and "the job stops in" in messages[0]
    release.set()
    deadline = time.monotonic() + 2
    while preparation.busy_slot("model-read") and time.monotonic() < deadline:
        time.sleep(0.01)


def test_the_face_detector_download_before_a_job_has_a_short_limit(monkeypatch, tmp_path):
    from localsr.core import face_detection

    seen = {}

    def download(model, path, **options):
        seen.update(options)
        raise RuntimeError("offline")

    monkeypatch.setattr(face_detection, "download_model", download)
    store = SimpleNamespace(
        path_for=lambda model: tmp_path / "yunet.onnx", is_installed=lambda model: False
    )
    with pytest.raises(face_detection.FaceDetectionUnavailable, match="offline"):
        face_detection._detector_model_path(store=store, cancel_event=None, allow_download=True)
    assert seen["max_seconds"] == face_detection.DOWNLOAD_SECONDS <= 120
