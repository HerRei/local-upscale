"""Quick and Best pick the same model in Python (presets.py) and in the desktop
(`choosePresetModel` in plan.ts). Both sides assert the picks in
tests/fixtures/preset-picks.json, so a ranking change has to update both."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from localsr.core.model_catalog import MODEL_CATALOG, ModelPurpose, QualityTier, SpeedTier
from localsr.core.presets import PresetMode, select_model_for_preset

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "preset-picks.json").read_text(encoding="utf-8")
)
PURPOSE_OF_CONTENT = {"photo": ModelPurpose.PHOTO, "illustration": ModelPurpose.ILLUSTRATION}
MODE_OF_QUALITY = {"quick": PresetMode.QUICK_UPSCALE, "best": PresetMode.BEST_UPSCALE}


def _catalog_model(entry: dict):
    return replace(
        MODEL_CATALOG[0],
        model_id=entry["model_id"],
        purposes=tuple(ModelPurpose(purpose) for purpose in entry["purposes"]),
        native_scale=entry["native_scale"],
        quality_tier=QualityTier(entry["quality_tier"]),
        speed_tier=SpeedTier(entry["speed_tier"]),
        speed_factor=entry["speed_factor"],
        memory_factor=entry["memory_factor"],
        size_bytes=entry["size_bytes"],
        commercial_use_status="allowed" if entry["commercial_use_allowed"] else "non-commercial",
        preset_eligible=entry["preset_eligible"],
    )


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda case: json.dumps(case))
def test_quick_and_best_pick_what_the_shared_fixture_expects(case):
    models = [
        _catalog_model(entry)
        for entry in FIXTURE["models"]
        if entry["model_id"] not in case["without"]
    ]
    picked = select_model_for_preset(
        models, MODE_OF_QUALITY[case["quality"]], purpose=PURPOSE_OF_CONTENT[case["content"]]
    )
    assert picked.model_id == case["expected"]
