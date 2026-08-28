import hashlib
import io

import pytest
from PySide6.QtCore import QSettings

from localsr.core.estimator import estimate_resources
from localsr.core.model_catalog import (
    CATALOG_REVISION,
    MODEL_CATALOG,
    CatalogModel,
    ModelDownloadError,
    ModelStore,
    download_model,
)
from localsr.ui.main_window import CUSTOM_MODEL_ID, MainWindow


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
        "denoise_realplksr_1x",
        "nafnet_sidd_width64",
        "realplksr_hfa2k_anime_x4",
        "span_photo_x4",
        "realplksr_nomoswebphoto_x4",
        "nafnet_gopro_deblur",
    ]
    assert len({model.filename for model in MODEL_CATALOG}) == 9
    upstream_hat = [
        model
        for model in MODEL_CATALOG
        if model.architecture == "HAT" and model.model_id != "hat_s_x4_face"
    ]
    assert all(CATALOG_REVISION in model.download_url for model in upstream_hat)
    assert all(model.download_url.startswith("https://") for model in MODEL_CATALOG)
    assert {model.license_name for model in MODEL_CATALOG} == {
        "Apache-2.0",
        "CC-BY-4.0",
        "CC BY 4.0",
        "CC-BY-0.4 (upstream; clarify)",
        "CC BY-NC-SA 4.0",
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


def test_gui_restricts_controls_to_reported_capabilities(qapp, tmp_path):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    window = MainWindow(start_worker=False, settings=settings)
    custom_index = window.combo_model.findData(CUSTOM_MODEL_ID)
    window.combo_model.setCurrentIndex(custom_index)
    window.custom_model_path = str(tmp_path / "model.pth")
    window.model_path = window.custom_model_path
    window.image_path = str(tmp_path / "image.png")
    window.image_w = 1536
    window.image_h = 1024
    window.on_model_info(
        {
            "filename": "model.pth",
            "architecture": "HAT",
            "scale": 4,
            "half_supported": True,
            "parameter_count": 20_000_000,
            "model_file_size": 80_000_000,
            "warnings": [],
        }
    )
    window.on_capabilities(
        {
            "system_ram_total": 16 * 1024**3,
            "system_ram_available": 8 * 1024**3,
            "devices": [
                {
                    "id": "cuda:0",
                    "type": "cuda",
                    "name": "Test GPU",
                    "total_memory": 2 * 1024**3,
                    "free_memory": 2 * 1024**3,
                    "supports_fp16": True,
                    "recommended_tile_sizes": [64, 128],
                }
            ],
        }
    )

    assert [window.combo_tile.itemText(i) for i in range(window.combo_tile.count())] == [
        "64",
        "128",
    ]
    assert window.combo_precision.findText("fp16") >= 0
    assert window.check_safe_mem.isChecked()
    assert not window.check_safe_mem.isEnabled()
    assert "first-run range" in window.estimate_label.text()
    assert "VRAM now:" in window.memory_label.text()
    assert "RAM now:" in window.memory_label.text()
    assert "Estimated LocalSR peak:" in window.hardware_label.text()
    assert "of 2.0 GB" in window.hardware_label.text()
    assert "VRAM" in window.hardware_label.text()
    assert "of 16.0 GB" in window.hardware_label.text()
    assert "RAM" in window.hardware_label.text()

    window.on_progress(
        {
            "completed_tiles": 1,
            "total_tiles": 10,
            "percentage": 10.0,
            "active_tile_size": 64,
            "estimated_remaining_seconds": 90.0,
            "device_free_memory": 1024**3,
            "system_ram_available": 4 * 1024**3,
        }
    )
    assert "1.0 GB VRAM left" in window.live_resource_label.text()

    window.model_download_progress.setVisible(True)
    assert not window.model_download_progress.isHidden()
    window.on_model_download_finished()
    assert window.model_download_progress.isHidden()

    window.on_capabilities(
        {
            "system_ram_total": 16 * 1024**3,
            "system_ram_available": 8 * 1024**3,
            "devices": [
                {
                    "id": "mps",
                    "type": "mps",
                    "name": "Test Apple GPU",
                    "total_memory": 16 * 1024**3,
                    "free_memory": 8 * 1024**3,
                    "supports_fp16": True,
                    "recommended_tile_sizes": [64, 128],
                }
            ],
        }
    )
    assert "Estimated LocalSR peak:" in window.hardware_label.text()
    assert "of 16.0 GB" in window.hardware_label.text()
    assert "unified memory" in window.hardware_label.text()
    assert "Hard Metal allocation cap" in window.hardware_label.text()
    assert window.btn_upscale.isEnabled()

    assert [
        window.combo_output_scale.itemData(index)
        for index in range(window.combo_output_scale.count())
    ] == [2, 3, 4]
    window.combo_output_scale.setCurrentIndex(window.combo_output_scale.findData(3))
    assert window.selected_output_scale() == 3
    assert "4608x3072 (3×)" in window.predicted_size_label.text()
    assert window.get_output_path().endswith("image_upscaled_3x.png")
    window.close()
