import base64
import io

from PIL import Image
from PySide6.QtCore import QSettings, QSize
from PySide6.QtGui import QColor, QImage

from localsr.core.model_catalog import CATALOG_BY_ID, MODEL_CATALOG, ModelPurpose
from localsr.core.presets import PresetMode, rank_models_for_preset, resolve_settings_for_model
from localsr.ui.preview_provider import PreviewImageProvider
from localsr.ui.qml_app import create_qml_application


def test_quick_and_best_rank_distinct_catalog_models():
    quick = rank_models_for_preset(
        MODEL_CATALOG,
        PresetMode.QUICK_UPSCALE,
        purpose=ModelPurpose.PHOTO,
        output_scale=4,
    )
    best = rank_models_for_preset(
        MODEL_CATALOG,
        PresetMode.BEST_UPSCALE,
        purpose=ModelPurpose.PHOTO,
        output_scale=4,
    )

    assert quick[0].model_id == "span_x4_official"
    assert best[0].model_id == "hat_l_x4_imagenet"


def test_quick_preset_selects_accelerator_fp16_and_fitting_tile():
    gib = 1024**3
    model = CATALOG_BY_ID["span_x4_official"]
    settings = resolve_settings_for_model(
        model=model,
        mode=PresetMode.QUICK_UPSCALE,
        devices=[
            {
                "id": "cpu",
                "type": "cpu",
                "name": "CPU",
                "free_memory": 8 * gib,
                "supports_fp16": False,
                "recommended_tile_sizes": [64, 128],
            },
            {
                "id": "cuda:0",
                "type": "cuda",
                "name": "GPU",
                "free_memory": 6 * gib,
                "supports_fp16": True,
                "recommended_tile_sizes": [64, 128, 256],
            },
        ],
        image_width=1200,
        image_height=800,
        output_scale=4,
        available_system_memory=8 * gib,
        available_disk=80 * gib,
        model_half_supported=True,
        installed_model_ids={model.model_id},
    )

    assert settings.device_id == "cuda:0"
    assert settings.precision == "fp16"
    assert settings.tile_size in {64, 128, 256}
    assert settings.requires_download is False
    assert not settings.estimate.blocking


def test_best_preset_preserves_fp32_quality():
    gib = 1024**3
    model = CATALOG_BY_ID["hat_l_x4_imagenet"]
    settings = resolve_settings_for_model(
        model=model,
        mode=PresetMode.BEST_UPSCALE,
        devices=[
            {
                "id": "mps",
                "type": "mps",
                "name": "Apple GPU",
                "free_memory": 8 * gib,
                "supports_fp16": True,
                "recommended_tile_sizes": [64, 128],
                "is_integrated": True,
            }
        ],
        image_width=640,
        image_height=480,
        output_scale=4,
        available_system_memory=8 * gib,
        available_disk=80 * gib,
        model_half_supported=True,
    )

    assert settings.device_id == "mps"
    assert settings.precision == "fp32"


def test_progressive_preview_composites_a_completed_tile():
    provider = PreviewImageProvider(maximum_dimension=100)
    source = QImage(50, 25, QImage.Format_RGB32)
    source.fill(QColor("#243044"))
    provider.set_source_image(source)
    provider.reset_progressive(200, 100)

    tile_buffer = io.BytesIO()
    Image.new("RGB", (20, 20), (240, 30, 30)).save(tile_buffer, format="JPEG", quality=95)
    assert provider.apply_tile(
        jpeg_base64=base64.b64encode(tile_buffer.getvalue()).decode("ascii"),
        output_x=100,
        output_y=0,
        output_width=100,
        output_height=100,
        image_width=200,
        image_height=100,
    )

    result = provider.requestImage("progressive", None, QSize())
    assert result.size() == QSize(100, 50)
    left = result.pixelColor(10, 25)
    right = result.pixelColor(75, 25)
    assert right.red() > left.red()


def test_qml_interface_loads_offscreen(qapp, tmp_path):
    settings = QSettings(str(tmp_path / "qml-settings.ini"), QSettings.IniFormat)
    engine, controller = create_qml_application(start_worker=False, settings=settings)
    try:
        roots = engine.rootObjects()
        assert len(roots) == 1
        assert roots[0].property("title") == "LocalSR"
        assert roots[0].width() >= 1180
        assert roots[0].height() >= 760
        assert roots[0].property("localSR") == controller
        assert len(controller.modelRows) == len(MODEL_CATALOG) + 1
    finally:
        controller.shutdown()
