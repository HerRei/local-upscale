"""Model-library metadata: catalog fields, rights policy, and the desktop export."""

from __future__ import annotations

import importlib.util
from dataclasses import replace
from pathlib import Path

from localsr.core.model_catalog import MODEL_CATALOG, CatalogModel, ModelPurpose, get_model_by_id
from localsr.core.presets import PresetMode, rank_models_for_preset, select_model_for_preset

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "export_desktop_catalog", ROOT / "scripts" / "export_desktop_catalog.py"
)
assert SPEC and SPEC.loader
exporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(exporter)


def test_every_catalog_model_has_a_role_and_a_stage() -> None:
    for model in MODEL_CATALOG:
        assert model.role, model.model_id
        assert model.stage_kind in {"upscale", "deblock", "restore", "face_restore"}, model.model_id
        if model.native_scale > 1:
            assert model.stage_kind in {"upscale", "face_restore"}
        else:
            assert model.stage_kind in {"deblock", "restore"}
            assert model.fixes, f"{model.model_id} must serve at least one Fix chip"


def test_fix_chips_map_to_the_expected_checkpoints() -> None:
    assert get_model_by_id("fbcnn_color").fixes == ("jpeg",)
    assert get_model_by_id("nafnet_gopro_deblur").fixes == ("blur",)
    assert get_model_by_id("denoise_realplksr_1x").fixes == ("noise",)
    assert get_model_by_id("nafnet_sidd_width64").fixes == ("noise",)
    assert get_model_by_id("span_photo_x4").fixes == ()


def test_rights_status_is_separate_from_support_tier() -> None:
    manifest = exporter.build_manifest()
    by_id = {entry["model_id"]: entry for entry in manifest["models"]}
    assert manifest["schema_version"] == 2

    # Labs models with verified rights stay preset-eligible; Labs is validation only.
    best = by_id["realplksr_nomoswebphoto_x4"]
    assert best["support_tier"] == "labs"
    assert best["rights_status"] == "attribution"
    assert best["automated_download_allowed"] is True

    face = by_id["hat_l_x4_face"]
    assert face["rights_status"] == "unresolved"
    assert face["automated_download_allowed"] is False

    for entry in manifest["models"]:
        assert entry["display_name"] and " — " not in entry["display_name"]
        assert "4x" not in entry["display_name"]
        assert entry["role"]
        assert entry["stage"] in {"upscale", "deblock", "restore", "face_restore"}
        assert set(entry["fixes"]) <= {"noise", "jpeg", "blur"}
        assert set(entry["content"]) <= {"photo", "illustration", "face"}
        assert entry["rights_status"] in {"verified", "attribution", "unresolved", "non_commercial"}


def test_display_names_read_like_the_library_rows() -> None:
    assert get_model_by_id("realplksr_nomoswebphoto_x4").display_name == (
        "RealPLKSR ×4 NomosWebPhoto"
    )
    assert get_model_by_id("span_photo_x4").display_name == "SPAN ×4 NomosUni"
    assert get_model_by_id("hat_l_x4_imagenet").display_name == "HAT-L ×4 ImageNet"
    assert get_model_by_id("fbcnn_color").display_name == "FBCNN Color"


def test_presets_skip_checkpoints_with_unresolved_rights() -> None:
    base = get_model_by_id("realplksr_nomoswebphoto_x4")
    assert base is not None
    tempting = replace(
        base,
        model_id="tempting_unverified_x4",
        quality_tier=base.quality_tier,
        speed_tier=base.speed_tier,
        size_bytes=base.size_bytes + 1,
        license_name="Checkpoint rights unverified",
        commercial_use_status="unclear",
    )
    catalog: tuple[CatalogModel, ...] = (*MODEL_CATALOG, tempting)
    ranked = rank_models_for_preset(catalog, PresetMode.BEST_UPSCALE, purpose=ModelPurpose.PHOTO)
    assert all(model.model_id != "tempting_unverified_x4" for model in ranked)
    assert select_model_for_preset(catalog, PresetMode.BEST_UPSCALE).model_id == base.model_id


def test_face_companions_stay_selectable_by_pairing() -> None:
    selected = select_model_for_preset(MODEL_CATALOG, PresetMode.QUICK_UPSCALE, purpose="face")
    assert selected.commercial_use_status == "unclear"
    assert ModelPurpose.FACE in selected.purposes
