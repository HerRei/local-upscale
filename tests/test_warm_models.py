"""Tests for checkpoint SHA-256 caching and prepared backend reuse across jobs."""

from __future__ import annotations

import hashlib
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import av
import numpy as np
import pytest
import spandrel.architectures.ESRGAN as E
import torch
from PIL import Image

import localsr.core.coreml_runtime as coreml_runtime
import localsr.core.model_catalog as model_catalog
import localsr.worker.server as server_module
from localsr.core import preparation
from localsr.core.coreml_runtime import NEURAL_ENGINE
from localsr.core.inference import InferenceEngine
from localsr.core.model_adapter import CheckpointSecurityError, ModelAdapter
from localsr.core.model_catalog import CATALOG_BY_FILENAME
from localsr.worker.server import WorkerServer


@pytest.fixture
def settled_clock(monkeypatch):
    """Advance model_catalog's clock past the 2 s settle window and clear hash cache."""
    model_catalog._VERIFIED_FILE_HASHES.clear()
    real_ns = time.time_ns
    monkeypatch.setattr(model_catalog.time, "time_ns", lambda: real_ns() + 3600 * 10**9)
    yield
    model_catalog._VERIFIED_FILE_HASHES.clear()


def _tiny_esrgan_checkpoint(path: Path, *, num_filters: int = 8) -> tuple[Path, str]:
    torch.manual_seed(0)
    net = E.ESRGAN(in_nc=3, out_nc=3, num_filters=num_filters, num_blocks=1, scale=2)
    torch.save(net.state_dict(), path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return path, digest


def _make_photo(path: Path, size: tuple[int, int] = (24, 24)) -> None:
    w, h = size
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    rgb = np.stack(
        [
            (x / max(1, w) * 200 + 20),
            (y / max(1, h) * 200 + 20),
            ((x + y) / max(1, w + h) * 200 + 20),
        ],
        axis=-1,
    ).astype(np.uint8)
    Image.fromarray(rgb).save(path)


def _make_video(path: Path, *, frames: int = 3, size: tuple[int, int] = (32, 24)) -> None:
    w, h = size
    with av.open(str(path), "w") as container:
        stream = container.add_stream("libvpx-vp9", rate=12)
        stream.width, stream.height, stream.pix_fmt = w, h, "yuv420p"
        for idx in range(frames):
            rgb = np.full((h, w, 3), 40 + idx * 30, dtype=np.uint8)
            rgb[:, : w // 2, 0] = 180
            for packet in stream.encode(av.VideoFrame.from_ndarray(rgb, format="rgb24")):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)


# ── Checkpoint SHA-256 stamp cache ──────────────────────────────────────────


def test_checkpoint_hash_cache_hits_on_unchanged_and_misses_on_touch_or_replace(
    tmp_path, monkeypatch, settled_clock
):
    checkpoint = tmp_path / "custom.pth"
    checkpoint.write_bytes(b"first-checkpoint-bytes")
    first_digest = hashlib.sha256(b"first-checkpoint-bytes").hexdigest()

    hash_calls = 0
    real_sha256 = model_catalog.file_sha256

    def counting_sha256(path, *args, **kwargs):
        nonlocal hash_calls
        hash_calls += 1
        return real_sha256(path, *args, **kwargs)

    monkeypatch.setattr(model_catalog, "file_sha256", counting_sha256)

    adapter = ModelAdapter(allow_unverified_checkpoints=True)
    adapter.approved_checkpoint = (str(checkpoint), first_digest)

    # First check hashes once; second check and checkpoint_identity hit cache.
    assert adapter._checkpoint_trust(str(checkpoint)) == "explicitly-trusted"
    assert hash_calls == 1
    assert adapter._checkpoint_trust(str(checkpoint)) == "explicitly-trusted"
    assert coreml_runtime.checkpoint_identity(str(checkpoint)) == first_digest
    assert hash_calls == 1

    # Touching mtime_ns misses and re-hashes even with identical bytes.
    stat = checkpoint.stat()
    os.utime(checkpoint, ns=(stat.st_atime_ns, stat.st_mtime_ns - 5 * 10**9))
    assert adapter._checkpoint_trust(str(checkpoint)) == "explicitly-trusted"
    assert hash_calls == 2
    assert adapter._checkpoint_trust(str(checkpoint)) == "explicitly-trusted"
    assert hash_calls == 2

    # Replacing the file misses and rejects the old approved digest.
    checkpoint.write_bytes(b"replaced-checkpoint!!")
    with pytest.raises(CheckpointSecurityError, match="changed"):
        adapter._checkpoint_trust(str(checkpoint))
    assert hash_calls == 3


def test_catalog_hash_cache_hits_and_misses_on_tamper(tmp_path, monkeypatch, settled_clock):
    payload = b"catalog-model-bytes"
    checkpoint = tmp_path / "catalog-cached.pth"
    checkpoint.write_bytes(payload)
    # A cached file is older than the settle window; the tamper below writes a later
    # timestamp, also on Windows, where writes within one tick keep the same time.
    aged = checkpoint.stat().st_mtime - 60
    os.utime(checkpoint, (aged, aged))
    entry = SimpleNamespace(
        name="Catalog Cached",
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    monkeypatch.setitem(CATALOG_BY_FILENAME, checkpoint.name, entry)

    hash_calls = 0
    real_sha256 = model_catalog.file_sha256

    def counting_sha256(path, *args, **kwargs):
        nonlocal hash_calls
        hash_calls += 1
        return real_sha256(path, *args, **kwargs)

    monkeypatch.setattr(model_catalog, "file_sha256", counting_sha256)
    adapter = ModelAdapter(allow_unverified_checkpoints=False)
    assert adapter._checkpoint_trust(str(checkpoint)) == "catalog-verified"
    assert adapter._checkpoint_trust(str(checkpoint)) == "catalog-verified"
    assert hash_calls == 1

    # Same-size replacement invalidates and raises.
    checkpoint.write_bytes(b"tampered-model-byte")
    with pytest.raises(CheckpointSecurityError, match="SHA-256"):
        adapter._checkpoint_trust(str(checkpoint))
    assert hash_calls == 2


def test_unsettled_file_is_not_reused_for_checkpoint_trust(tmp_path, monkeypatch):
    model_catalog._VERIFIED_FILE_HASHES.clear()
    checkpoint = tmp_path / "rapid.pth"
    checkpoint.write_bytes(b"good-data")
    digest = hashlib.sha256(b"good-data").hexdigest()
    original_stat = checkpoint.stat()
    real_stat = Path.stat
    monkeypatch.setattr(
        Path,
        "stat",
        lambda self, *a, **kw: original_stat if self == checkpoint else real_stat(self, *a, **kw),
    )
    monkeypatch.setattr(model_catalog.time, "time_ns", lambda: original_stat.st_mtime_ns + 100_000)

    adapter = ModelAdapter(allow_unverified_checkpoints=True)
    adapter.approved_checkpoint = (str(checkpoint), digest)
    assert adapter._checkpoint_trust(str(checkpoint)) == "explicitly-trusted"
    checkpoint.write_bytes(b"evil-data")
    with pytest.raises(CheckpointSecurityError, match="changed"):
        adapter._checkpoint_trust(str(checkpoint))


# ── ModelAdapter.inspect cache ──────────────────────────────────────────────


def test_model_adapter_inspect_reuses_metadata_after_release_until_file_changes(
    tmp_path, monkeypatch, settled_clock
):
    checkpoint, digest = _tiny_esrgan_checkpoint(tmp_path / "esrgan.pth")
    adapter = ModelAdapter(allow_unverified_checkpoints=True)
    adapter.approved_checkpoint = (str(checkpoint), digest)

    load_calls = 0
    orig_load = adapter.loader.load_from_file

    def counting_load(path):
        nonlocal load_calls
        load_calls += 1
        return orig_load(path)

    monkeypatch.setattr(adapter.loader, "load_from_file", counting_load)

    info1 = adapter.inspect(str(checkpoint))
    adapter.release()
    info2 = adapter.inspect(str(checkpoint))
    assert load_calls == 1
    assert info1 == info2

    # Touching mtime forces re-inspect.
    stat = checkpoint.stat()
    os.utime(checkpoint, ns=(stat.st_atime_ns, stat.st_mtime_ns - 5 * 10**9))
    info3 = adapter.inspect(str(checkpoint))
    assert load_calls == 2
    assert info3.architecture == info1.architecture


# ── Prepared backend cache (Core ML & InferenceEngine) ──────────────────────


def _install_fake_coreml(monkeypatch, model_path: Path):
    from test_coreml_runtime import FakeProgram, FakeRunner, tiny_descriptor

    FakeProgram.stores = {}
    FakeProgram.conversions = 0
    FakeProgram.fail_conversion = False
    loads: list[tuple[str, str]] = []
    runners: list[FakeRunner] = []
    original = tiny_descriptor()

    def reference(image):
        with torch.no_grad():
            return original.model(image)

    def load(path, unit):
        loads.append((str(path), unit))
        runner = FakeRunner(unit, "match", reference)
        runners.append(runner)
        return runner

    class Compiled:
        def __truediv__(self, _name):
            return SimpleNamespace(exists=lambda: True)

    def program(root, architecture, identity, shape):
        value = FakeProgram(root, architecture, identity, shape)
        value.compiled = Compiled()
        return value

    monkeypatch.setattr(coreml_runtime, "_Program", program)
    monkeypatch.setattr(coreml_runtime, "_load_compiled", load)
    monkeypatch.setattr(coreml_runtime, "coreml_units_for", lambda _arch: (NEURAL_ENGINE,))
    monkeypatch.setattr(
        coreml_runtime._CoreMLImage,
        "_time",
        staticmethod(lambda function, synchronize=None, repeats=2: 1.0),
    )
    preparation._abandoned.clear()

    class StubAdapter:
        def __init__(self):
            self.load_calls = 0
            self.info = SimpleNamespace(
                architecture="SPAN",
                scale=2,
                in_channels=3,
                out_channels=3,
                tiling_supported=True,
                half_supported=True,
                size_requirements_min=1,
                size_requirements_mult=1,
                size_requirements_square=False,
                filename=model_path.name,
                warnings=[],
            )

        def load(self, path, device, precision, *, onnx_route=None, coreml_units=()):
            self.load_calls += 1
            descriptor = coreml_runtime.CoreMLImageDescriptor(
                original,
                units=coreml_units,
                identity=coreml_runtime.checkpoint_identity(path),
            )
            return descriptor, original

        def release(self):
            pass

    return SimpleNamespace(loads=loads, runners=runners, adapter=StubAdapter(), original=original)


def test_prepared_coreml_backend_hits_across_video_release_and_misses_on_touch_shape_precision_or_pressure(
    tmp_path, monkeypatch, settled_clock
):
    model_path = tmp_path / "span.safetensors"
    model_path.write_bytes(b"fake-span-v1")
    fake = _install_fake_coreml(monkeypatch, model_path)
    engine = InferenceEngine(fake.adapter)
    monkeypatch.setattr(
        "localsr.core.inference._resolve_torch_device",
        lambda _dev: SimpleNamespace(type="mps", index=None, __eq__=lambda a, b: True),
    )
    monkeypatch.setattr("localsr.core.inference._apply_gpu_memory_limit", lambda *_: None)

    frame = torch.rand(3, 20, 20)
    # Job 1: loads, converts, compiles, gates.
    engine.load_model(str(model_path), "mps", "auto", fake.adapter.info)
    out1 = engine.process_frame(
        frame, fake.adapter.info, 16, 4, threading.Event(), lambda *_: None, safe_memory=False
    )
    assert len(fake.loads) == 1

    # Parking under memory pressure does not break the prepared backend.
    engine.park_models()
    out_parked = engine.process_frame(
        frame, fake.adapter.info, 16, 4, threading.Event(), lambda *_: None, safe_memory=False
    )
    assert len(fake.loads) == 1
    np.testing.assert_array_equal(out1, out_parked)

    # End of video job releases GPU model, keeping prepared Core ML backend.
    engine.release_model()
    assert engine._loaded_models == {}

    # Job 2 (unchanged file, same shape 32x32, same precision fp32): cache HIT (no _load_compiled!).
    engine.load_model(str(model_path), "mps", "auto", fake.adapter.info)
    out2 = engine.process_frame(
        frame, fake.adapter.info, 16, 4, threading.Event(), lambda *_: None, safe_memory=False
    )
    assert len(fake.loads) == 1
    np.testing.assert_array_equal(out1, out2)

    # Different tile shape -> misses for new shape (loads 48x48).
    engine.release_model()
    engine.load_model(str(model_path), "mps", "auto", fake.adapter.info)
    engine.process_frame(
        frame, fake.adapter.info, 32, 4, threading.Event(), lambda *_: None, safe_memory=False
    )
    assert len(fake.loads) == 2

    # Different precision -> misses prepared backend cache.
    engine.release_model()
    engine.load_model(str(model_path), "mps", "fp16", fake.adapter.info)
    engine.process_frame(
        frame, fake.adapter.info, 16, 4, threading.Event(), lambda *_: None, safe_memory=False
    )
    assert len(fake.loads) == 3

    # Touching the file invalidates the prepared backend cache.
    engine.release_model()
    stat = model_path.stat()
    os.utime(model_path, ns=(stat.st_atime_ns, stat.st_mtime_ns - 5 * 10**9))
    engine.load_model(str(model_path), "mps", "auto", fake.adapter.info)
    engine.process_frame(
        frame, fake.adapter.info, 16, 4, threading.Event(), lambda *_: None, safe_memory=False
    )
    assert len(fake.loads) == 4

    # Replacing the file invalidates the prepared backend cache.
    engine.release_model()
    model_path.write_bytes(b"fake-span-v2-longer")
    engine.load_model(str(model_path), "mps", "auto", fake.adapter.info)
    engine.process_frame(
        frame, fake.adapter.info, 16, 4, threading.Event(), lambda *_: None, safe_memory=False
    )
    assert len(fake.loads) == 5

    # Releasing under memory pressure evicts the prepared backend cache.
    engine.release_model(memory_pressure=True)
    engine.load_model(str(model_path), "mps", "auto", fake.adapter.info)
    engine.process_frame(
        frame, fake.adapter.info, 16, 4, threading.Event(), lambda *_: None, safe_memory=False
    )
    assert len(fake.loads) == 6


def test_release_models_except_evicts_unused_prepared_backends(
    tmp_path, monkeypatch, settled_clock
):
    model_a = tmp_path / "a.safetensors"
    model_b = tmp_path / "b.safetensors"
    model_a.write_bytes(b"a-bytes")
    model_b.write_bytes(b"b-bytes")
    fake = _install_fake_coreml(monkeypatch, model_a)
    monkeypatch.setattr(
        "localsr.core.inference._resolve_torch_device",
        lambda _dev: SimpleNamespace(type="mps", index=None, __eq__=lambda a, b: True),
    )
    monkeypatch.setattr("localsr.core.inference._apply_gpu_memory_limit", lambda *_: None)

    engine = InferenceEngine(fake.adapter)
    frame = torch.rand(3, 20, 20)
    engine.load_model(str(model_a), "mps", "auto", fake.adapter.info)
    engine.process_frame(
        frame, fake.adapter.info, 16, 4, threading.Event(), lambda *_: None, safe_memory=False
    )
    assert len(fake.loads) == 1

    # End of job 1 drops _loaded_models; job 2 requests only model_b -> evicts model_a's backend.
    engine.release_model()
    engine.release_models_except({str(model_b)})

    engine.load_model(str(model_a), "mps", "auto", fake.adapter.info)
    engine.process_frame(
        frame, fake.adapter.info, 16, 4, threading.Event(), lambda *_: None, safe_memory=False
    )
    assert len(fake.loads) == 2


# ── End-to-end byte-identical consecutive jobs ──────────────────────────────


def test_consecutive_photo_and_video_jobs_produce_byte_identical_outputs(
    tmp_path, monkeypatch, settled_clock
):
    checkpoint, digest = _tiny_esrgan_checkpoint(tmp_path / "tiny.pth")
    photo_in = tmp_path / "in.png"
    _make_photo(photo_in)
    video_in = tmp_path / "in.mkv"
    _make_video(video_in)

    monkeypatch.setattr(server_module, "send_message", lambda _msg: None)
    import localsr.worker.image_job as image_job_module

    monkeypatch.setattr(image_job_module, "require_image_room", lambda *a, **k: None)

    server = WorkerServer()
    server.model_adapter = ModelAdapter(allow_unverified_checkpoints=True)
    server.engine = InferenceEngine(server.model_adapter)

    # Two identical photo jobs in a row.
    photo_outs = [tmp_path / "p1.png", tmp_path / "p2.png"]
    for idx, out_path in enumerate(photo_outs):
        server._run_image_job(
            {
                "job_id": f"p-{idx}",
                "image_path": str(photo_in),
                "model_path": str(checkpoint),
                "output_path": str(out_path),
                "output_format": "png",
                "device": "cpu",
                "tile_size": 16,
                "halo": 4,
                "precision": "fp32",
                "jpeg_quality": 95,
                "preserve_metadata": False,
                "safe_memory": False,
                "output_scale": 2,
                "allow_unverified_checkpoint": True,
                "approved_checkpoint_sha256": digest,
            }
        )
    assert photo_outs[0].read_bytes() == photo_outs[1].read_bytes()

    # Two identical video jobs in a row.
    video_outs = [tmp_path / "v1.mkv", tmp_path / "v2.mkv"]
    for idx, out_path in enumerate(video_outs):
        server.model_adapter.approved_checkpoint = (str(checkpoint), digest)
        server._check_approved_checkpoint(
            {"model_path": str(checkpoint), "approved_checkpoint_sha256": digest}
        )
        server._run_video_job(
            f"v-{idx}",
            {
                "job_id": f"v-{idx}",
                "video_path": str(video_in),
                "model_path": str(checkpoint),
                "output_video_path": str(out_path),
                "container": "mkv",
                "video_codec": "ffv1",
                "crf": 18,
                "device": "cpu",
                "tile_size": 16,
                "halo": 4,
                "precision": "fp32",
                "safe_memory": False,
                "output_scale": 2,
                "allow_unverified_checkpoint": True,
                "approved_checkpoint_sha256": digest,
                "preview_enabled": False,
            },
        )

    def decoded_frames(p: Path) -> list[bytes]:
        with av.open(str(p)) as container:
            return [f.to_ndarray(format="rgb24").tobytes() for f in container.decode(video=0)]

    assert decoded_frames(video_outs[0]) == decoded_frames(video_outs[1])


def test_idle_worker_releases_warm_models_once_after_the_idle_time():
    from localsr.worker import server as server_module

    server = server_module.WorkerServer()
    releases = []

    class Engine:
        def release_under_memory_pressure(self):
            releases.append("engine")

    class Adapter:
        def release(self, *, clear_metadata=False):
            releases.append("adapter")

    server._engine = Engine()
    server._model_adapter = Adapter()
    idle = server_module.IDLE_RELEASE_SECONDS

    server._release_when_idle(100.0)
    server._release_when_idle(100.0 + idle - 1)
    assert releases == []
    server._release_when_idle(100.0 + idle)
    assert releases == ["adapter", "engine"]
    server._release_when_idle(100.0 + 3 * idle)
    assert releases == ["adapter", "engine"]

    # Any message ends the idle stretch; the next one counts from scratch.
    server._note_activity()
    server._release_when_idle(1000.0)
    server._release_when_idle(1000.0 + idle - 1)
    assert releases == ["adapter", "engine"]
    server._release_when_idle(1000.0 + idle)
    assert releases == ["adapter", "engine", "adapter", "engine"]
