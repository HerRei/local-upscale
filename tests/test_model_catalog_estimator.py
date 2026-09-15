import hashlib
import io

import pytest

from localsr.core.estimator import estimate_resources
from localsr.core.model_catalog import (
    CATALOG_REVISION,
    MODEL_CATALOG,
    CatalogModel,
    ModelDownloadError,
    ModelPurpose,
    ModelStore,
    download_model,
)


def _small_model(content=b"verified model"):
    return CatalogModel(
        model_id="test",
        name="Test",
        filename="test.pth",
        description="Test model",
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        download_url="https://example.test/test.pth",
    )


def test_catalog_has_pinned_optional_downloads():
    assert [model.model_id for model in MODEL_CATALOG] == [
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
    ]
    assert len({model.filename for model in MODEL_CATALOG}) == 16
    upstream_hat = [
        model
        for model in MODEL_CATALOG
        if model.architecture == "HAT" and ModelPurpose.FACE not in model.purposes
    ]
    assert all(CATALOG_REVISION in model.download_url for model in upstream_hat)
    assert all(model.download_url.startswith("https://") for model in MODEL_CATALOG)
    assert {model.license_name for model in MODEL_CATALOG} == {
        "Apache-2.0",
        "CC BY 4.0",
        "Checkpoint rights unverified",
        "BSD-3-Clause",
        "MIT",
    }
    assert all(len(model.sha256) == 64 for model in MODEL_CATALOG)


def test_model_download_is_atomic_and_checksum_verified(tmp_path):
    content = b"verified model"
    model = _small_model(content)
    destination = tmp_path / model.filename
    progress = []

    def opener(_request, timeout):
        assert timeout == 15.0
        return io.BytesIO(content)

    result = download_model(
        model,
        destination,
        opener=opener,
        chunk_size=4,
        progress_callback=lambda done, total: progress.append((done, total)),
    )

    assert result == destination
    assert destination.read_bytes() == content
    assert progress[-1] == (len(content), len(content))
    assert not (tmp_path / "test.pth.part").exists()
    assert ModelStore(tmp_path).is_installed(model)


def test_bad_model_checksum_removes_partial_download(tmp_path):
    content = b"tampered"
    model = _small_model(b"expected")
    destination = tmp_path / model.filename

    with pytest.raises(ModelDownloadError, match="SHA-256"):
        download_model(
            model,
            destination,
            opener=lambda _request, timeout: io.BytesIO(content),
        )

    assert not destination.exists()
    assert not (tmp_path / "test.pth.part").exists()


def test_estimate_blocks_an_unsafe_device_configuration():
    estimate = estimate_resources(
        image_width=4000,
        image_height=3000,
        scale=4,
        tile_size=512,
        halo=64,
        precision="fp32",
        device_type="cuda",
        available_device_memory=512 * 1024**2,
        available_system_memory=16 * 1024**3,
        available_disk=100 * 1024**3,
        model_file_size=160 * 1024**2,
        memory_factor=1.8,
        time_factor=1.8,
    )

    assert estimate.blocking
    assert estimate.tile_count > 0
    assert any("smaller tile" in warning for warning in estimate.warnings)


def test_mps_memory_pressure_warns_but_does_not_block():
    estimate = estimate_resources(
        image_width=4000,
        image_height=3000,
        scale=4,
        tile_size=512,
        halo=64,
        precision="fp32",
        device_type="mps",
        available_device_memory=512 * 1024**2,
        available_system_memory=512 * 1024**2,
        available_disk=100 * 1024**3,
        model_file_size=160 * 1024**2,
        memory_factor=1.8,
        time_factor=1.8,
    )

    assert not estimate.blocking
    assert any("run is still allowed" in warning.lower() for warning in estimate.warnings)


def test_measured_estimate_uses_a_narrower_calibrated_range():
    common = {
        "image_width": 1536,
        "image_height": 1024,
        "scale": 4,
        "tile_size": 128,
        "halo": 16,
        "precision": "fp32",
        "device_type": "mps",
        "available_device_memory": 8 * 1024**3,
        "available_system_memory": 8 * 1024**3,
        "available_disk": 100 * 1024**3,
        "model_file_size": 80 * 1024**2,
    }
    first_run = estimate_resources(**common)
    calibrated = estimate_resources(**common, measured_seconds_per_megapixel=30.0)

    assert not first_run.calibrated
    assert calibrated.calibrated
    assert calibrated.seconds_high / calibrated.seconds_low < 2
    assert first_run.seconds_high / first_run.seconds_low > 4
