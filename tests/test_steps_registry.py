"""Step registry contract and desktop export synchronization."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_step_registry_matches_current_pipeline_and_catalog() -> None:
    from localsr.core.model_catalog import MODEL_CATALOG
    from localsr.core.pipeline import FIX_KINDS, MAX_FIX_STAGES
    from localsr.core.steps import (
        ALL_FIX_CHIPS,
        DEFAULT_FIX_ORDER,
        FIX_STAGE_KINDS,
        IMAGE_PIPELINE_STAGE_KINDS,
        LIMITS,
        PHASES,
        REQUIRED_STEP_FEATURES,
        STEPS,
        STEPS_BY_ID,
        parameter_for,
    )
    from localsr.worker.server import _engine_features

    assert len(STEPS_BY_ID) == len(STEPS)
    for step in STEPS:
        assert step.phase in PHASES, step.id
        assert set(step.media) <= {"image", "video"}, step.id
        assert step.media, step.id

    fix_chip_order = tuple(
        step.fix_kind
        for step in sorted(
            (s for s in STEPS if s.is_fix_stage),
            key=lambda s: s.fix_chip_order if s.fix_chip_order is not None else s.order,
        )
        if step.fix_kind is not None
    )
    recipe_stage_kinds = frozenset(
        step.stage_kind for step in STEPS if step.in_recipe_stages and step.stage_kind is not None
    )

    assert FIX_KINDS == frozenset({"blur", "jpeg", "noise", "colour"})
    assert DEFAULT_FIX_ORDER == ("blur", "jpeg", "noise", "colour")
    assert fix_chip_order == ("noise", "blur", "jpeg", "colour")
    assert set(ALL_FIX_CHIPS) == {"blur", "jpeg", "noise", "colour", "faces"}
    assert MAX_FIX_STAGES == 4
    assert LIMITS.max_fix_stages == 4
    assert LIMITS.max_fix_chips == 5
    assert LIMITS.max_face_stages == 1
    assert LIMITS.max_recipe_stages == 6
    assert LIMITS.max_image_pipeline_stages == 6
    assert LIMITS.max_upscale_prefix_stages == 4
    assert LIMITS.max_denoise_prefix_stages == 3
    assert LIMITS.max_video_upscale_prefix_stages == 3
    assert LIMITS.multi_fix_feature == "image_multi_fix"

    assert FIX_STAGE_KINDS == frozenset({"deblock", "restore"})
    assert IMAGE_PIPELINE_STAGE_KINDS == frozenset(
        {"deblock", "restore", "upscale", "face_restore"}
    )
    assert recipe_stage_kinds == frozenset(
        {"deblock", "restore", "upscale", "face_restore", "video"}
    )

    assert STEPS_BY_ID["jpeg"].stage_kind == "deblock"
    for fix in ("blur", "noise", "colour"):
        assert STEPS_BY_ID[fix].stage_kind == "restore"

    for model in MODEL_CATALOG:
        assert model.stage_kind in IMAGE_PIPELINE_STAGE_KINDS, model.model_id
        assert set(model.fixes) <= FIX_KINDS, model.model_id

    features = set(_engine_features())
    assert LIMITS.multi_fix_feature in features
    for feature in REQUIRED_STEP_FEATURES:
        assert feature in features

    assert parameter_for("deflicker", "deflicker_window").minimum == 1
    assert parameter_for("deflicker", "deflicker_window").maximum == 9
    assert parameter_for("face_restore", "face_fidelity").minimum == 0
    assert parameter_for("face_restore", "face_fidelity").maximum == 100
    assert parameter_for("face_restore", "fidelity").minimum == 0.0
    assert parameter_for("face_restore", "fidelity").maximum == 1.0
    assert parameter_for("interpolate", "interpolation").choices == (
        "off",
        "x2",
        "x4",
        "fps60",
        "fps120",
    )
    assert parameter_for("interpolate", "interpolation_order").choices == (
        "after_upscale",
        "before_upscale",
    )
    assert parameter_for("edit", "max_dimension").int_choices == (512, 768, 1024)
    assert parameter_for("edit", "steps").minimum == 1
    assert parameter_for("edit", "steps").maximum == 60
    assert parameter_for("edit", "steps").ui_minimum == 0


def test_exported_desktop_steps_json_is_in_sync() -> None:
    spec = importlib.util.spec_from_file_location(
        "export_desktop_steps", ROOT / "scripts" / "export_desktop_steps.py"
    )
    assert spec and spec.loader
    exporter = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = exporter
    spec.loader.exec_module(exporter)

    manifest = exporter.build_manifest()
    shipped_path = ROOT / "desktop" / "src-tauri" / "resources" / "steps.json"
    assert shipped_path.is_file(), "missing desktop/src-tauri/resources/steps.json"
    assert shipped_path.read_text(encoding="utf-8") == exporter.encoded_manifest()
    assert json.loads(shipped_path.read_text(encoding="utf-8")) == manifest
