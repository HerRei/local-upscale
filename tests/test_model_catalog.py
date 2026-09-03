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
    FACE_DETECTOR_MODEL,
    MODEL_CATALOG,
    CatalogModel,
    ModelCatalogEntry,
    ModelDownloadCancelled,
    ModelDownloadError,
    ModelPurpose,
    ModelStore,
    QualityTier,
    SpeedTier,
    default_model_directory,
    download_model,
    get_all_models,
    get_model_by_id,
    get_models_for_purpose,
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
        "denoise_realplksr_1x",
        "nafnet_sidd_width64",
        "realplksr_hfa2k_anime_x4",
        "span_photo_x4",
        "realplksr_nomoswebphoto_x4",
        "realesrgan_x2plus",
        "fbcnn_color",
        "nafnet_gopro_deblur",
    ]
    catalog_ids = [model.model_id for model in MODEL_CATALOG]
    assert catalog_ids == expected_ids
    assert len(MODEL_CATALOG) == 11

    # Verify uniqueness of IDs and filenames
    assert len({model.model_id for model in MODEL_CATALOG}) == 11
    assert len({model.filename for model in MODEL_CATALOG}) == 11


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
            "CC-BY-0.4 (upstream; clarify)",
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

    face_model = get_model_by_id("hat_s_x4_face")
    assert face_model is not None
    assert face_model.commercial_use_status == "unclear"
    assert face_model.license_name == "Checkpoint rights unverified"
    assert "user-supplied" in face_model.description

    for model_id in ("realplksr_hfa2k_anime_x4", "realplksr_nomoswebphoto_x4"):
        model = get_model_by_id(model_id)
        assert model is not None
        assert model.commercial_use_status == "unclear"
        assert "clarify" in model.license_name

    native_x2 = get_model_by_id("realesrgan_x2plus")
    assert native_x2 is not None
    assert native_x2.native_scale == 2
    assert native_x2.architecture == "RealESRGAN"
    assert native_x2.license_name == "BSD-3-Clause"
    assert native_x2.sha256 == "49fafd45f8fd7aa8d31ab2a22d14d91b536c34494a5cfe31eb5d89c2fa266abb"

    deblock = get_model_by_id("fbcnn_color")
    assert deblock is not None
    assert deblock.native_scale == 1
    assert deblock.purposes == (ModelPurpose.RESTORATION, ModelPurpose.PHOTO)
    assert deblock.license_name == "Apache-2.0"
    assert deblock.sha256 == "8b0e4ef23d59cf7ac934a342cb31a17619e4fa4a0b3374a9d78c5174312387e8"


def test_spandrel_compatible_models_metadata():
    # 1. RealPLKSR Anime
    anime = get_model_by_id("realplksr_hfa2k_anime_x4")
    assert anime is not None
    assert anime.name == "RealPLKSR 4x HFA2k — Anime"
    assert anime.architecture == "RealPLKSR"
    assert anime.scale == 4
    assert ModelPurpose.ILLUSTRATION in anime.purposes
    assert "4xHFA2k_ludvae_realplksr_dysample.pth" in anime.download_url
    assert anime.speed_factor == 0.85
    assert anime.memory_factor == 0.88
    assert anime.quality_tier == QualityTier.HIGH
    assert "anime, illustrations, and clean line art" in anime.description

    # 2. SPAN Photo
    span_photo = get_model_by_id("span_photo_x4")
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
    realplksr = get_model_by_id("realplksr_nomoswebphoto_x4")
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
    nafnet_deblur = get_model_by_id("nafnet_gopro_deblur")
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


def test_get_models_for_purpose():
    # Illustration
    illustration_models = get_models_for_purpose(ModelPurpose.ILLUSTRATION)
    assert any(m.model_id == "realplksr_hfa2k_anime_x4" for m in illustration_models)

    # String input compatibility
    illustration_by_str = get_models_for_purpose("illustration")
    assert illustration_by_str == illustration_models

    # Deblur
    deblur_models = get_models_for_purpose(ModelPurpose.DEBLUR)
    assert any(m.model_id == "nafnet_gopro_deblur" for m in deblur_models)
    assert get_models_for_purpose("deblur") == deblur_models

    # Photo
    photo_models = get_models_for_purpose(ModelPurpose.PHOTO)
    photo_ids = {m.model_id for m in photo_models}
    assert "hat_s_x4" in photo_ids
    assert "span_photo_x4" in photo_ids
    assert "realplksr_nomoswebphoto_x4" in photo_ids

    # Denoise
    denoise_models = get_models_for_purpose(ModelPurpose.DENOISE)
    denoise_ids = {m.model_id for m in denoise_models}
    assert "denoise_realplksr_1x" in denoise_ids
    assert "nafnet_sidd_width64" in denoise_ids


def test_get_model_by_id():
    assert get_model_by_id("realplksr_hfa2k_anime_x4") is not None
    assert get_model_by_id("realplksr_hfa2k_anime_x4").model_id == "realplksr_hfa2k_anime_x4"
    assert get_model_by_id("non_existent_id") is None


def test_get_all_models():
    all_models = get_all_models()
    assert all_models == MODEL_CATALOG


def test_catalog_model_properties_and_immutability():
    model = get_model_by_id("realplksr_hfa2k_anime_x4")
    assert model is not None
    assert model.scale == 4
    assert model.file_size_bytes == model.size_bytes
    assert model.purpose == ModelPurpose.ILLUSTRATION
    assert ModelCatalogEntry is CatalogModel

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


def test_model_download_cancellation_preserves_partial(tmp_path: Path):
    content = b"a" * 1024 * 1024  # 1MB
    model = _synthetic_test_model(content=content)
    destination = tmp_path / model.filename
    cancel_event = threading.Event()

    class ChunkingStream(io.BytesIO):
        def read(self, size=-1):
            cancel_event.set()
            return super().read(size)

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
