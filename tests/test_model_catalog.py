import dataclasses
import hashlib
import io
import re
import shutil
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from localsr.core.model_catalog import (
    CATALOG_BY_ID,
    FACE_DETECTOR_MODEL,
    MODEL_CATALOG,
    CatalogModel,
    ModelDownloadCancelled,
    ModelDownloadError,
    ModelPurpose,
    ModelStore,
    QualityTier,
    SpeedTier,
    default_model_directory,
    download_model,
)


def _synthetic_test_model(
    model_id: str = "synthetic_model",
    content: bytes = b"verified synthetic model content for testing",
    architecture: str = "SPAN",
    scale: int = 4,
    purpose: ModelPurpose = ModelPurpose.ILLUSTRATION,
) -> CatalogModel:
    return CatalogModel(
        model_id=model_id,
        name="Synthetic Model",
        filename=f"{model_id}.pth",
        description="Synthetic model description for testing",
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        download_url=f"https://example.test/models/{model_id}.pth",
        architecture=architecture,
        native_scale=scale,
        purposes=(purpose, ModelPurpose.GENERAL),
        quality_tier=QualityTier.HIGH,
        speed_tier=SpeedTier.FAST,
        speed_factor=0.85,
        memory_factor=0.88,
        time_factor=0.85,
        vram_estimate_mb=800,
    )


def test_catalog_has_all_curated_models():
    expected_ids = [
        "hat_s_x4",
        "hat_s_x4_face",
        "hat_l_x4_imagenet",
        "hat_l_x4_face",
        "denoise_realplksr_1x",
        "nafnet_sidd_width64",
        "realplksr_hfa2k_anime_x4",
        "span_photo_x4",
        "realplksr_nomoswebphoto_x4",
        "realesrgan_x2plus",
        "fbcnn_color",
        "nafnet_gopro_deblur",
        "realesrgan_x4plus",
        "realesrgan_x4plus_anime_6b",
        "swinir_m_real_x4_gan",
        "scunet_color_real_psnr",
        "bhi_dat2_real_x4",
        "realwebphoto_v4_dat2_x4",
    ]
    catalog_ids = [model.model_id for model in MODEL_CATALOG]
    assert catalog_ids == expected_ids
    assert len(MODEL_CATALOG) == 18

    # Verify uniqueness of IDs and filenames
    assert len({model.model_id for model in MODEL_CATALOG}) == 18
    assert len({model.filename for model in MODEL_CATALOG}) == 18


def test_recent_same_size_rewrite_cannot_reuse_a_checksum_verdict(tmp_path, monkeypatch):
    import time

    model = _synthetic_test_model(content=b"good")
    path = tmp_path / model.filename
    path.write_bytes(b"good")
    original_stat = path.stat()
    real_stat = Path.stat
    # Reproduce Windows returning identical inode/size/timestamps for rapid writes.
    monkeypatch.setattr(
        Path,
        "stat",
        lambda self, *args, **kwargs: (
            original_stat if self == path else real_stat(self, *args, **kwargs)
        ),
    )
    monkeypatch.setattr(time, "time_ns", lambda: original_stat.st_mtime_ns + 100_000_000)
    store = ModelStore(tmp_path)
    assert store.is_installed(model)
    path.write_bytes(b"evil")
    assert not store.is_installed(model)
    path.write_bytes(b"good")
    assert store.is_installed(model)
    path.write_bytes(b"evil")
    monkeypatch.setattr(time, "time_ns", lambda: original_stat.st_mtime_ns + 3_000_000_000)
    assert not store.is_installed(model)


def test_face_detector_is_pinned_but_not_exposed_as_a_restoration_model():
    assert FACE_DETECTOR_MODEL not in MODEL_CATALOG
    assert FACE_DETECTOR_MODEL.filename == "face_detection_yunet_2023mar.onnx"
    assert FACE_DETECTOR_MODEL.size_bytes == 232_589
    assert (
        FACE_DETECTOR_MODEL.sha256
        == "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"
    )
    assert FACE_DETECTOR_MODEL.download_url.startswith("https://github.com/opencv/opencv_zoo/")
    assert FACE_DETECTOR_MODEL.license_name == "MIT"


def test_catalog_attributes_and_integrity():
    hex_pattern = re.compile(r"^[0-9a-f]{64}$")
    for model in MODEL_CATALOG:
        assert isinstance(model.model_id, str) and len(model.model_id) > 0
        assert isinstance(model.name, str) and len(model.name) > 0
        assert isinstance(model.filename, str) and model.filename.endswith((".pth", ".safetensors"))
        assert isinstance(model.description, str) and len(model.description) > 0
        assert model.size_bytes > 0
        assert model.size_megabytes == model.size_bytes / 1_000_000
        assert hex_pattern.match(model.sha256) is not None, f"Invalid SHA-256 for {model.model_id}"
        assert model.download_url.startswith("https://")
        assert model.native_scale in (1, 2, 4)
        assert len(model.purposes) >= 1
        assert all(isinstance(p, ModelPurpose) for p in model.purposes)
        assert model.license_name in {
            "Apache-2.0",
            "CC-BY-4.0",
            "CC BY 4.0",
            "CC BY-NC-SA 4.0",
            "Checkpoint rights unverified",
            "BSD-3-Clause",
            "MIT",
        }
        assert len(model.author) > 0
        assert 0.0 < model.memory_factor <= 2.0
        assert 0.0 < model.time_factor <= 2.0
        assert 0.0 < model.speed_factor <= 1.0
        assert model.vram_estimate_mb >= 0
        assert model.commercial_use_status in {"allowed", "not-allowed", "unclear"}

    for model_id in ("hat_s_x4_face", "hat_l_x4_face"):
        face_model = CATALOG_BY_ID.get(model_id)
        assert face_model is not None
        assert face_model.commercial_use_status == "unclear"
        assert face_model.license_name == "Checkpoint rights unverified"
        assert "user-supplied" in face_model.description

    for model_id in ("realplksr_hfa2k_anime_x4", "realplksr_nomoswebphoto_x4"):
        model = CATALOG_BY_ID.get(model_id)
        assert model is not None
        assert model.commercial_use_status == "allowed"
        assert model.license_name == "CC BY 4.0"
        assert model.source_url.startswith("https://huggingface.co/Phips/")

    native_x2 = CATALOG_BY_ID.get("realesrgan_x2plus")
    assert native_x2 is not None
    assert native_x2.native_scale == 2
    assert native_x2.architecture == "RealESRGAN"
    assert native_x2.license_name == "BSD-3-Clause"
    assert native_x2.sha256 == "49fafd45f8fd7aa8d31ab2a22d14d91b536c34494a5cfe31eb5d89c2fa266abb"

    deblock = CATALOG_BY_ID.get("fbcnn_color")
    assert deblock is not None
    assert deblock.native_scale == 1
    assert deblock.purposes == (ModelPurpose.RESTORATION, ModelPurpose.PHOTO)
    assert deblock.license_name == "Apache-2.0"
    assert deblock.sha256 == "8b0e4ef23d59cf7ac934a342cb31a17619e4fa4a0b3374a9d78c5174312387e8"


def test_spandrel_compatible_models_metadata():
    # 1. RealPLKSR Anime
    anime = CATALOG_BY_ID.get("realplksr_hfa2k_anime_x4")
    assert anime is not None
    assert anime.name == "RealPLKSR 4x HFA2k — Anime"
    assert anime.architecture == "RealPLKSR"
    assert anime.scale == 4
    assert ModelPurpose.ILLUSTRATION in anime.purposes
    assert "4xHFA2k_ludvae_realplksr_dysample.pth" in anime.download_url
    assert anime.speed_factor == 0.85
    assert anime.memory_factor == 0.88
    assert anime.quality_tier == QualityTier.HIGH
    assert anime.role == "Anime video frames · Labs"
    assert "soften fine lines and screentones" in anime.description
    assert "darken white paper" in anime.description
    assert "reduce colour saturation" in anime.description
    assert not anime.preset_eligible

    # 2. SPAN Photo
    span_photo = CATALOG_BY_ID.get("span_photo_x4")
    assert span_photo is not None
    assert span_photo.name == "SPAN 4x NomosUni — Quick"
    assert span_photo.architecture == "SPAN"
    assert span_photo.scale == 4
    assert ModelPurpose.PHOTO in span_photo.purposes
    assert "4xNomosUni_span_multijpg.pth" in span_photo.download_url
    assert span_photo.speed_factor == 0.96
    assert span_photo.memory_factor == 0.92
    assert span_photo.speed_tier == SpeedTier.FAST
    assert "Quick Start preset" in span_photo.description

    # 3. RealPLKSR NomosWebPhoto
    realplksr = CATALOG_BY_ID.get("realplksr_nomoswebphoto_x4")
    assert realplksr is not None
    assert realplksr.name == "RealPLKSR 4x NomosWebPhoto — Best"
    assert realplksr.architecture == "RealPLKSR"
    assert realplksr.scale == 4
    assert ModelPurpose.PHOTO in realplksr.purposes
    assert "4xNomosWebPhoto_RealPLKSR.pth" in realplksr.download_url
    assert realplksr.speed_factor == 0.70
    assert realplksr.memory_factor == 0.80
    assert realplksr.vram_estimate_mb == 1200
    assert "Best Quality preset" in realplksr.description

    # 4. NAFNet Deblur
    nafnet_deblur = CATALOG_BY_ID.get("nafnet_gopro_deblur")
    assert nafnet_deblur is not None
    assert nafnet_deblur.name == "NAFNet GoPro Deblur"
    assert nafnet_deblur.architecture == "NAFNet"
    assert nafnet_deblur.scale == 1
    assert ModelPurpose.DEBLUR in nafnet_deblur.purposes
    assert ModelPurpose.RESTORATION in nafnet_deblur.purposes
    assert "NAFNet-GoPro-width64.pth" in nafnet_deblur.download_url
    assert nafnet_deblur.speed_factor == 0.65
    assert nafnet_deblur.memory_factor == 0.75
    assert nafnet_deblur.quality_tier == QualityTier.HIGH
    assert "Single-image camera motion deblurring model" in nafnet_deblur.description


def test_model_purpose_enum():
    assert ModelPurpose.GENERAL == "general"
    assert ModelPurpose.PHOTO == "photo"
    assert ModelPurpose.ANIME == "anime"
    assert ModelPurpose.ILLUSTRATION == "illustration"
    assert ModelPurpose.DENOISE == "denoise"
    assert ModelPurpose.DEBLUR == "deblur"
    assert ModelPurpose.RESTORATION == "restoration"
    assert ModelPurpose.FACE == "face"
    assert ModelPurpose.VIDEO == "video"


def test_catalog_model_properties_and_immutability():
    model = CATALOG_BY_ID.get("realplksr_hfa2k_anime_x4")
    assert model is not None
    assert model.scale == 4
    assert model.file_size_bytes == model.size_bytes
    assert model.purpose == ModelPurpose.ILLUSTRATION

    with pytest.raises(dataclasses.FrozenInstanceError):
        model.name = "Mutated Name"  # type: ignore[misc]


def test_model_download_atomic_and_verified(tmp_path: Path):
    content = b"verified model bytes for testing"
    model = _synthetic_test_model(content=content)
    destination = tmp_path / model.filename
    progress_updates: list[tuple[int, int]] = []

    def mock_opener(_request, timeout: float):
        assert timeout == 15.0
        return io.BytesIO(content)

    result_path = download_model(
        model,
        destination,
        opener=mock_opener,
        chunk_size=8,
        progress_callback=lambda done, total: progress_updates.append((done, total)),
    )

    assert result_path == destination
    assert destination.is_file()
    assert destination.read_bytes() == content
    assert progress_updates[-1] == (len(content), len(content))
    assert not (tmp_path / f"{model.filename}.part").exists()
    assert ModelStore(tmp_path).is_installed(model)


def test_model_download_checksum_failure_unlinks_partial(tmp_path: Path):
    expected_content = b"expected original content"
    corrupt_content = b"tampered original content"
    assert len(expected_content) == len(corrupt_content)
    model = _synthetic_test_model(content=expected_content)
    destination = tmp_path / model.filename

    def corrupt_opener(_request, timeout: float):
        return io.BytesIO(corrupt_content)

    with pytest.raises(ModelDownloadError, match="failed SHA-256 verification"):
        download_model(
            model,
            destination,
            opener=corrupt_opener,
        )

    assert not destination.exists()
    assert not (tmp_path / f"{model.filename}.part").exists()
    assert not ModelStore(tmp_path).is_installed(model)


def test_model_download_size_mismatch_fails_and_cleans_up(tmp_path: Path):
    expected_content = b"expected original content"
    oversized_content = b"this content is much larger than expected by size_bytes"
    model = _synthetic_test_model(content=expected_content)
    destination = tmp_path / model.filename

    def oversized_opener(_request, timeout: float):
        return io.BytesIO(oversized_content)

    with pytest.raises(ModelDownloadError, match="Download produced"):
        download_model(
            model,
            destination,
            opener=oversized_opener,
        )

    assert not destination.exists()
    assert not (tmp_path / f"{model.filename}.part").exists()


def test_model_download_never_writes_past_the_pinned_size(tmp_path: Path):
    model = _synthetic_test_model(content=b"x" * 64)
    destination = tmp_path / model.filename
    partial = tmp_path / f"{model.filename}.part"
    reads = []

    class Endless:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, size):
            # What is on disk before this read must still be within the pinned size.
            assert not partial.exists() or partial.stat().st_size <= model.size_bytes
            reads.append(size)
            assert len(reads) < 1000, "the download kept reading an endless response"
            return b"y" * size

    with pytest.raises(ModelDownloadError, match="Download produced more than"):
        download_model(
            model, destination, opener=lambda _request, timeout: Endless(), chunk_size=48
        )

    assert not destination.exists()
    assert not partial.exists()


def test_model_download_rejects_the_chunk_that_would_exceed_the_size_before_writing(
    tmp_path: Path,
):
    model = _synthetic_test_model(content=b"x" * 16)
    partial = tmp_path / f"{model.filename}.part"
    written = []
    real_open = Path.open

    def spying_open(self, *args, **kwargs):
        handle = real_open(self, *args, **kwargs)
        if self == partial:
            real_write = handle.write
            handle.write = lambda data: written.append(len(data)) or real_write(data)
        return handle

    with patch.object(Path, "open", spying_open):
        with pytest.raises(ModelDownloadError, match="Download produced more than"):
            download_model(
                model,
                tmp_path / model.filename,
                opener=lambda _request, timeout: io.BytesIO(b"y" * 1000),
                chunk_size=100,
            )
    assert sum(written) <= model.size_bytes
    assert not partial.exists()


def test_model_download_stops_when_the_server_trickles_past_the_time_limit(tmp_path: Path):
    import time

    model = _synthetic_test_model(content=b"x" * 100_000)
    partial = tmp_path / f"{model.filename}.part"
    reads = []

    class Trickle:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size):
            reads.append(1)
            assert len(reads) < 2000, "the download never gave up on a trickling response"
            time.sleep(0.01)
            return b"y"

    started = time.monotonic()
    with pytest.raises(ModelDownloadError, match="took too long"):
        download_model(
            model,
            tmp_path / model.filename,
            opener=lambda _request, timeout: Trickle(),
            chunk_size=1,
            max_seconds=0.2,
        )

    assert time.monotonic() - started < 5
    assert not (tmp_path / model.filename).exists()
    # The bytes that did arrive stay for a later resume, as after a cancel.
    assert partial.exists() and partial.stat().st_size < model.size_bytes


def test_a_buffered_read_that_blocks_for_a_large_request_does_not_hold_the_download(
    tmp_path: Path,
):
    import time

    model = _synthetic_test_model(content=b"x" * 100_000)
    reads = []

    class BlocksOnLargeReads:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, size=-1):
            # A buffered read waits until the whole request is filled: hours at a trickle.
            raise AssertionError("read() would block until its full size arrived")

        def read1(self, size=-1):
            assert size <= 1024 * 1024
            reads.append(size)
            time.sleep(0.01)
            return b"y"

    started = time.monotonic()
    with pytest.raises(ModelDownloadError, match="took too long"):
        download_model(
            model,
            tmp_path / model.filename,
            opener=lambda _request, timeout: BlocksOnLargeReads(),
            max_seconds=0.2,
        )
    assert time.monotonic() - started < 5
    assert reads


def test_each_wait_for_download_bytes_is_limited_by_the_remaining_time(tmp_path: Path):
    model = _synthetic_test_model(content=b"x" * 100_000)
    timeouts = []

    class Socket:
        def settimeout(self, seconds):
            timeouts.append(seconds)

    class Response:
        fp = type("Fp", (), {"raw": type("Raw", (), {"_sock": Socket()})()})()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read1(self, size=-1):
            return b""

    with pytest.raises(ModelDownloadError):
        download_model(
            model,
            tmp_path / model.filename,
            opener=lambda _request, timeout: Response(),
            max_seconds=3.0,
            max_attempts=1,
        )
    assert timeouts and all(0 < seconds <= 3.0 for seconds in timeouts)


def test_model_download_cancellation_preserves_partial(tmp_path: Path):
    content = b"a" * 1024 * 1024  # 1MB
    model = _synthetic_test_model(content=content)
    destination = tmp_path / model.filename
    cancel_event = threading.Event()

    class ChunkingStream(io.BytesIO):
        def read1(self, size=-1):
            cancel_event.set()
            return super().read1(size)

    def cancel_opener(_request, timeout: float):
        return ChunkingStream(content)

    with pytest.raises(ModelDownloadCancelled, match="cancelled"):
        download_model(
            model,
            destination,
            opener=cancel_opener,
            cancel_event=cancel_event,
            chunk_size=1024,
        )

    assert not destination.exists()
    partial_file = tmp_path / f"{model.filename}.part"
    assert partial_file.exists()
    assert partial_file.stat().st_size > 0


def test_model_download_resumes_range_request(tmp_path: Path):
    full_content = b"0123456789ABCDEF" * 1024  # 16KB
    model = _synthetic_test_model(content=full_content)
    destination = tmp_path / model.filename
    partial_file = tmp_path / f"{model.filename}.part"

    # Pre-write half the file
    half_length = len(full_content) // 2
    partial_file.write_bytes(full_content[:half_length])

    recorded_headers = {}

    class Response206(io.BytesIO):
        status = 206

    def range_opener(request, timeout: float):
        recorded_headers.update(request.headers)
        return Response206(full_content[half_length:])

    download_model(
        model,
        destination,
        opener=range_opener,
        chunk_size=1024,
    )

    assert destination.is_file()
    assert destination.read_bytes() == full_content
    assert recorded_headers.get("Range") == f"bytes={half_length}-"
    assert not partial_file.exists()


def test_model_download_insufficient_disk_space(tmp_path: Path):
    content = b"model content"
    model = _synthetic_test_model(content=content)
    destination = tmp_path / model.filename

    # Mock disk_usage to return 0 free bytes
    with patch.object(
        shutil, "disk_usage", return_value=shutil._ntuple_diskusage(100000, 100000, 0)
    ):
        with pytest.raises(ModelDownloadError, match="Not enough disk space"):
            download_model(model, destination)


def test_model_download_resume_needs_room_only_for_what_is_still_missing(tmp_path: Path):
    full_content = b"0123456789ABCDEF" * 1024  # 16 KB
    model = _synthetic_test_model(content=full_content)
    destination = tmp_path / model.filename
    kept = len(full_content) * 3 // 4
    (tmp_path / f"{model.filename}.part").write_bytes(full_content[:kept])

    class Response206(io.BytesIO):
        status = 206

    # Room for the last quarter but not for the whole file: the first 12 KB already sit
    # on the disk.
    room = (len(full_content) - kept) * 2
    with patch.object(
        shutil, "disk_usage", return_value=shutil._ntuple_diskusage(10**6, 10**6 - room, room)
    ):
        download_model(
            model,
            destination,
            opener=lambda request, timeout: Response206(full_content[kept:]),
            chunk_size=1024,
        )
    assert destination.read_bytes() == full_content
    # Starting afresh with that little room is still refused.
    destination.unlink()
    with patch.object(
        shutil, "disk_usage", return_value=shutil._ntuple_diskusage(10**6, 10**6 - room, room)
    ):
        with pytest.raises(ModelDownloadError, match="Not enough disk space"):
            download_model(model, destination)


def test_model_store_is_installed(tmp_path: Path):
    store = ModelStore(tmp_path)
    model = _synthetic_test_model(content=b"model payload")
    model_path = store.path_for(model)

    assert not store.is_installed(model)

    # Incomplete file size
    model_path.write_bytes(b"short")
    assert not store.is_installed(model)

    # Exact size and checksum match
    model_path.write_bytes(b"model payload")
    assert store.is_installed(model)

    # A same-size replacement must not be trusted merely because its length matches.
    model_path.write_bytes(b"evil! payload")
    assert not store.is_installed(model)


def test_default_model_directory():
    dir_path = default_model_directory()
    assert isinstance(dir_path, Path)
    assert dir_path.name == "models"


def test_specialist_models_stay_out_of_the_presets():
    assert {model.model_id for model in MODEL_CATALOG if not model.preset_eligible} == {
        "bhi_dat2_real_x4",
        "realplksr_hfa2k_anime_x4",
        "realwebphoto_v4_dat2_x4",
    }


@pytest.mark.parametrize("corrupt", [False, True])
def test_complete_partial_is_verified_before_request(tmp_path, corrupt):
    content = b"verified model bytes"
    model = _synthetic_test_model(content=content)
    destination = tmp_path / model.filename
    destination.with_name(f"{destination.name}.part").write_bytes(
        b"x" * len(content) if corrupt else content
    )
    requests = []

    def opener(request, timeout):
        requests.append(request)
        assert not request.has_header("Range")
        return io.BytesIO(content)

    assert download_model(model, destination, opener=opener).read_bytes() == content
    assert len(requests) == int(corrupt)
