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
        if model.stage_kind == "background":
            assert any(step.model_task in model.purposes for step in STEPS if step.model_task)
        elif model.stage_kind == "inpaint":
            # Dust & scratches runs it when the host names it on the repair stage.
            assert model.model_id == "migan" and STEPS_BY_ID["repair"].requires_mask
        else:
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


def test_generic_step_params_validator_with_test_only_modelless_step() -> None:
    from localsr.core.steps import StepDefinition, StepParameter, validate_step_params

    test_step = StepDefinition(
        id="test_tint",
        media=("image",),
        phase="grade",
        order=10,
        needs_model=False,
        supports_mask=True,
        in_image_pipeline=True,
        stage_kind="test_tint",
        parameters=(
            StepParameter(name="amount", kind="number", minimum=0.0, maximum=1.0, required=True),
            StepParameter(name="passes", kind="int", minimum=1, maximum=4, default=1),
            StepParameter(name="mode", kind="choice", choices=("add", "multiply"), default="add"),
            StepParameter(name="bucket", kind="int_choice", int_choices=(8, 16, 32), default=16),
            StepParameter(name="clamp", kind="bool", default=True),
            StepParameter(name="label", kind="str", nullable=True, default=None),
        ),
    )

    validated = validate_step_params(
        test_step,
        {
            "amount": 0.5,
            "passes": 2,
            "mode": "multiply",
            "bucket": 32,
            "clamp": False,
            "label": None,
        },
    )
    assert validated["amount"] == 0.5

    # Missing required parameter or None when not nullable must fail.
    for bad in (
        None,
        {},
        {"amount": None},
        {"amount": -0.1},
        {"amount": 1.1},
        {"amount": float("nan")},
        {"amount": True},
        {"amount": "0.5"},
        {"amount": 0.5, "passes": 0},
        {"amount": 0.5, "passes": 5},
        {"amount": 0.5, "passes": 1.5},
        {"amount": 0.5, "passes": True},
        {"amount": 0.5, "mode": "screen"},
        {"amount": 0.5, "bucket": 12},
        {"amount": 0.5, "bucket": True},
        {"amount": 0.5, "clamp": "yes"},
        {"amount": 0.5, "label": 42},
        {"amount": 0.5, "unknown": 1},
    ):
        try:
            validate_step_params(test_step, bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for {bad!r}")


def test_deinterlace_is_a_video_geometry_step() -> None:
    from localsr.core.steps import STEPS_BY_ID, parameter_for

    step = STEPS_BY_ID["deinterlace"]
    assert step.media == ("video",)
    assert step.phase == "geometry" and step.in_image_pipeline
    assert not step.dev_only and not step.needs_model
    assert step.required_feature == "video_deinterlace"
    assert (
        parameter_for("deinterlace", "mode").default,
        parameter_for("deinterlace", "mode").choices,
    ) == ("frame", ("frame", "field"))
    assert (
        parameter_for("deinterlace", "parity").default,
        parameter_for("deinterlace", "parity").choices,
    ) == ("auto", ("auto", "tff", "bff"))


def test_stabilise_is_a_shipped_video_geometry_step() -> None:
    from localsr.core.steps import REQUIRED_STEP_FEATURES, STEPS_BY_ID

    step = STEPS_BY_ID["stabilise"]
    assert step.media == ("video",)
    assert step.phase == "geometry" and step.in_image_pipeline
    assert step.order > STEPS_BY_ID["deinterlace"].order
    assert not step.dev_only and not step.needs_model
    assert step.required_feature == "video_stabilise"
    assert "video_stabilise" in REQUIRED_STEP_FEATURES


def test_repair_is_a_shipped_photo_cleanup_step() -> None:
    from localsr.core.steps import REQUIRED_STEP_FEATURES, STEPS_BY_ID

    step = STEPS_BY_ID["repair"]
    assert step.media == ("image",)
    assert step.phase == "cleanup" and step.in_image_pipeline
    assert step.requires_mask and step.supports_mask
    assert not step.dev_only and not step.needs_model
    assert step.required_feature == "image_repair"
    assert "image_repair" in REQUIRED_STEP_FEATURES
