from localsr.core.model_catalog import CATALOG_BY_ID, MODEL_CATALOG, ModelPurpose
from localsr.core.presets import PresetMode, rank_models_for_preset


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
