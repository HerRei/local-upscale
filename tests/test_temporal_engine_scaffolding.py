"""Temporal video-engine scaffolding: windows, stitching, bundles, routing."""

import hashlib
import json
import threading
from pathlib import Path

import numpy as np
import pytest

from localsr.core.model_catalog import (
    CatalogVideoModel,
    ModelFile,
    ModelStore,
    download_bundle,
)
from localsr.core.temporal import clip_windows, crossfade_weights, stitch_clips
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


def test_clip_windows_cover_every_frame_without_gaps():
    for total, window, overlap in [
        (1, 5, 2),
        (5, 5, 2),
        (6, 5, 2),
        (100, 16, 4),
        (17, 16, 15),
    ]:
        windows = clip_windows(total, window, overlap)
        covered = set()
        for start, end in windows:
            assert 0 <= start <= end < total
            covered.update(range(start, end + 1))
        assert covered == set(range(total)), (total, window, overlap)
        # Consecutive windows overlap or touch — never leave a gap.
        for (_, prev_end), (next_start, _) in zip(windows, windows[1:], strict=False):
            assert next_start <= prev_end + 1


def test_clip_windows_share_exactly_the_requested_overlap_mid_sequence():
    windows = clip_windows(100, 16, 4)
    for (_, prev_end), (next_start, _) in zip(windows[:-2], windows[1:-1], strict=False):
        assert prev_end - next_start + 1 == 4


def test_clip_windows_reject_bad_parameters():
    with pytest.raises(ValueError):
        clip_windows(10, 0, 0)
    with pytest.raises(ValueError):
        clip_windows(10, 4, 4)


def test_crossfade_weights_are_strictly_interior_and_rising():
    weights = crossfade_weights(4)
    assert weights.shape == (4,)
    assert np.all(weights > 0) and np.all(weights < 1)
    assert np.all(np.diff(weights) > 0)
    assert crossfade_weights(0).shape == (0,)


def test_stitch_clips_emits_each_frame_once_and_blends_boundaries():
    total, window, overlap = 12, 5, 2
    windows = clip_windows(total, window, overlap)

    # Fake engine: each clip returns frames filled with (frame_index + clip_bias).
    def clips():
        for clip_index, (start, end) in enumerate(windows):
            bias = 10 * clip_index
            yield [
                np.full((4, 4, 3), index + bias, dtype=np.uint8) for index in range(start, end + 1)
            ]

    stitched = list(stitch_clips(clips(), windows))
    assert len(stitched) == total
    # Frames outside overlaps are passed through untouched.
    assert int(stitched[0][0, 0, 0]) == 0
    # Frames inside an overlap mix both neighbours: value strictly between
    # the outgoing clip's and the incoming clip's versions of that frame.
    start_2 = windows[1][0]
    outgoing = start_2  # clip 0 value for that frame
    incoming = start_2 + 10  # clip 1 value
    blended = int(stitched[start_2][0, 0, 0])
    assert outgoing < blended < incoming


def test_stitch_clips_rejects_wrong_clip_length():
    windows = [(0, 4), (3, 7)]
    with pytest.raises(ValueError):
        list(stitch_clips(iter([[np.zeros((2, 2, 3))] * 3]), [windows[0]]))


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


def test_download_bundle_fetches_verifies_and_reports_progress(tmp_path):
    payloads = {"dit": b"d" * 2048, "vae": b"v" * 512}
    model, store = _bundle_fixture(tmp_path, payloads)

    def opener(request, timeout):
        role = request.full_url.rsplit("/", 1)[-1]
        return _FakeResponse(payloads[role])

    progress = []
    bundle_dir = download_bundle(
        model,
        store,
        progress_callback=lambda done, total: progress.append((done, total)),
        cancel_event=threading.Event(),
        opener=opener,
    )
    assert bundle_dir == store.bundle_dir_for(model)
    assert store.is_bundle_installed(model)
    assert progress[-1] == (model.total_size_bytes, model.total_size_bytes)
    # Progress is monotonic across the whole bundle.
    assert all(a[0] <= b[0] for a, b in zip(progress, progress[1:], strict=False))

    # A second call is a no-op that still reports completion.
    progress.clear()
    download_bundle(model, store, progress_callback=lambda d, t: progress.append((d, t)))
    assert progress[-1] == (model.total_size_bytes, model.total_size_bytes)


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
