"""Video-engine bundles, routing, and downloads."""

import hashlib
import json
import threading
from pathlib import Path

import pytest

from localsr.core.model_catalog import (
    CatalogVideoModel,
    ModelFile,
    ModelStore,
)
from localsr.core.video_engines import (
    SPANDREL_IMAGE_KIND,
    TemporalEngineUnavailable,
    resolve_video_engine,
    seedvr2_runtime_issue,
)
from localsr.protocol.messages import VideoJobRequest


@pytest.mark.skipif(seedvr2_runtime_issue() is not None, reason=seedvr2_runtime_issue() or "")
def test_seedvr2_vendor_download_rejects_non_https(tmp_path, monkeypatch):
    from localsr.video_models.seedvr2.vendor.utils import downloads

    opened = False

    def unexpected_open(*_args, **_kwargs):
        nonlocal opened
        opened = True
        raise AssertionError("non-HTTPS URL reached urlopen")

    monkeypatch.setattr(downloads.urllib.request, "urlopen", unexpected_open)
    destination = tmp_path / "model.safetensors"
    assert not downloads.download_with_resume("file:///tmp/model.safetensors", str(destination))
    assert not opened
    assert not destination.exists()


@pytest.mark.skipif(seedvr2_runtime_issue() is not None, reason=seedvr2_runtime_issue() or "")
def test_seedvr2_text_embeddings_use_validated_safetensors(monkeypatch):
    import torch

    from localsr.video_models.seedvr2.vendor.core.generation_utils import load_text_embeddings

    package_dir = Path(__file__).parents[1] / "src" / "localsr" / "video_models" / "seedvr2"
    assert not list(package_dir.glob("*emb.pt"))
    monkeypatch.setattr(
        torch,
        "load",
        lambda *_args, **_kwargs: pytest.fail("SeedVR2 embeddings must not use torch.load"),
    )
    embeddings = load_text_embeddings(
        str(package_dir), torch.device("cpu"), torch.bfloat16, debug=None
    )
    assert tuple(embeddings["texts_pos"][0].shape) == (58, 5120)
    assert tuple(embeddings["texts_neg"][0].shape) == (64, 5120)
    assert embeddings["texts_pos"][0].dtype == torch.bfloat16
    assert embeddings["texts_neg"][0].dtype == torch.bfloat16


def _bundle_fixture(tmp_path, payloads):
    files = []
    for role, payload in payloads.items():
        files.append(
            ModelFile(
                role=role,
                filename=f"{role}.safetensors",
                size_bytes=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
                download_url=f"https://example.invalid/{role}",
            )
        )
    model = CatalogVideoModel(
        model_id="test_bundle",
        name="Test Bundle",
        description="fixture",
        family="test_family",
        engine_kind="seedvr2",
        files=tuple(files),
        license_name="Apache-2.0",
        author="fixture",
        source_url="https://example.invalid",
        min_unified_memory_gb=16,
        min_vram_gb=12,
        temporal_window=5,
        temporal_overlap=2,
    )
    return model, ModelStore(tmp_path / "models")


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload
        self._offset = 0

    def read(self, size: int) -> bytes:
        chunk = self._payload[self._offset : self._offset + size]
        self._offset += size
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_bundle_is_not_installed_when_a_file_is_missing_or_truncated(tmp_path):
    payloads = {"dit": b"d" * 128, "vae": b"v" * 64}
    model, store = _bundle_fixture(tmp_path, payloads)
    assert not store.is_bundle_installed(model)
    bundle_dir = store.bundle_dir_for(model)
    bundle_dir.mkdir(parents=True)
    for file, payload in zip(model.files, payloads.values(), strict=True):
        (bundle_dir / file.filename).write_bytes(payload)
    assert store.is_bundle_installed(model)
    first = bundle_dir / model.files[0].filename
    first.write_bytes(b"x" * model.files[0].size_bytes)
    assert not store.is_bundle_installed(model)
    first.write_bytes(payloads[model.files[0].role])
    assert store.is_bundle_installed(model)
    (bundle_dir / model.files[0].filename).write_bytes(b"short")
    assert not store.is_bundle_installed(model)


def test_video_engine_registry_routes_and_degrades():
    assert resolve_video_engine(SPANDREL_IMAGE_KIND) is None
    with pytest.raises(TemporalEngineUnavailable) as unknown:
        resolve_video_engine("not_a_real_engine")
    assert "Frame-by-frame" in str(unknown.value)


def test_seedvr2_engine_resolves_from_the_vendored_tree():
    engine_factory = resolve_video_engine("seedvr2")
    assert hasattr(engine_factory, "process_frames")


def test_seedvr2_catalog_bundles_are_pinned_and_complete():
    from localsr.core.model_catalog import VIDEO_CATALOG_BY_ID

    for model_id in ("seedvr2_3b", "seedvr2_3b_fp8"):
        model = VIDEO_CATALOG_BY_ID[model_id]
        roles = {file.role for file in model.files}
        assert roles == {"dit", "vae"}
        for file in model.files:
            assert len(file.sha256) == 64
            assert "/resolve/09ced71023636e9bc8cdf9cdecfb2625d1e691e8/" in file.download_url
        assert model.engine_kind == "seedvr2"
        assert model.temporal_window % 4 == 1  # SeedVR2's 4n+1 clip rule


def test_video_job_request_defaults_stay_frame_by_frame_compatible():
    request = VideoJobRequest(
        job_id="j",
        video_path="in.mp4",
        model_path="m.pth",
        output_video_path="out.mp4",
        container="mp4",
        crf=18,
        fps=None,
        device="cpu",
        tile_size=128,
        halo=16,
        precision="fp32",
        safe_memory=False,
    )
    data = json.loads(request.to_json())["data"]
    assert data["model_kind"] == "spandrel_image"
    assert data["bundle_dir"] is None
    assert data["temporal_window"] == 0


class _FlakyRangeOpener:
    """First attempt dies mid-stream; later attempts honor Range requests."""

    def __init__(self, payload: bytes, fail_after: int):
        self.payload = payload
        self.fail_after = fail_after
        self.attempts = 0
        self.range_offsets: list[int] = []

    def __call__(self, request, timeout):
        self.attempts += 1
        header = request.headers.get("Range", "")
        offset = int(header.split("=")[1].rstrip("-")) if header else 0
        self.range_offsets.append(offset)
        remaining = self.payload[offset:]

        class Response:
            status = 206 if offset else 200

            def __init__(self, data: bytes, fail_after: int | None):
                self._data = data
                self._offset = 0
                self._fail_after = fail_after

            def read(self, size: int) -> bytes:
                if self._fail_after is not None and self._offset >= self._fail_after:
                    raise TimeoutError("The read operation timed out")
                chunk = self._data[self._offset : self._offset + size]
                self._offset += size
                return chunk

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        fail_after = self.fail_after if self.attempts == 1 else None
        return Response(remaining, fail_after)


def test_download_resumes_after_a_mid_stream_timeout(tmp_path):
    from localsr.core.model_catalog import download_model

    payloads = {"dit": b"x" * 4096}
    model, _store = _bundle_fixture(tmp_path, payloads)
    file = model.files[0]
    opener = _FlakyRangeOpener(payloads["dit"], fail_after=1024)

    destination = tmp_path / file.filename
    progress = []
    result = download_model(
        file,
        destination,
        opener=opener,
        chunk_size=512,
        retry_wait_seconds=0.01,
        progress_callback=lambda done, total: progress.append(done),
    )
    assert result == destination
    assert destination.read_bytes() == payloads["dit"]
    # Attempt 2 resumed from the surviving partial rather than restarting.
    assert opener.attempts == 2
    assert opener.range_offsets == [0, 1024]
    assert not destination.with_name(f"{destination.name}.part").exists()


def test_cancelled_download_keeps_the_partial_for_resume(tmp_path):
    from localsr.core.model_catalog import ModelDownloadCancelled, download_model

    payloads = {"dit": b"y" * 2048}
    model, _store = _bundle_fixture(tmp_path, payloads)
    file = model.files[0]
    destination = tmp_path / file.filename
    cancel = threading.Event()

    def cancelling_progress(done, _total):
        if done >= 512:
            cancel.set()

    with pytest.raises(ModelDownloadCancelled):
        download_model(
            file,
            destination,
            opener=_FlakyRangeOpener(payloads["dit"], fail_after=10**9),
            chunk_size=512,
            cancel_event=cancel,
            progress_callback=cancelling_progress,
        )
    partial = destination.with_name(f"{destination.name}.part")
    assert partial.exists() and partial.stat().st_size >= 512
