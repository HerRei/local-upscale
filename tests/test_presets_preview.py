from localsr.core.model_catalog import CATALOG_BY_ID, MODEL_CATALOG, ModelPurpose
from localsr.core.presets import PresetMode, rank_models_for_preset, resolve_settings_for_model


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

    assert quick[0].model_id == "span_photo_x4"
    assert best[0].model_id == "realplksr_nomoswebphoto_x4"


def test_face_purpose_ranks_face_restoration_model():
    face_ranked = rank_models_for_preset(
        MODEL_CATALOG,
        PresetMode.QUICK_UPSCALE,
        purpose=ModelPurpose.FACE,
        output_scale=4,
    )
    assert face_ranked[0].model_id == "hat_s_x4_face"
    assert face_ranked[0].pair_with == "hat_s_x4"
    assert CATALOG_BY_ID["hat_s_x4"].pair_with == "hat_s_x4_face"


def test_denoise_recipes_rank_fast_and_maximum_fidelity_models():
    quick = rank_models_for_preset(
        MODEL_CATALOG,
        PresetMode.QUICK_DENOISE,
        purpose=ModelPurpose.DENOISE,
        output_scale=1,
    )
    best = rank_models_for_preset(
        MODEL_CATALOG,
        PresetMode.BEST_DENOISE,
        purpose=ModelPurpose.DENOISE,
        output_scale=1,
    )

    assert quick[0].model_id == "denoise_realplksr_1x"
    assert best[0].model_id == "nafnet_sidd_width64"


def test_quick_preset_selects_accelerator_fp16_and_fitting_tile():
    gib = 1024**3
    model = CATALOG_BY_ID["hat_s_x4"]
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
