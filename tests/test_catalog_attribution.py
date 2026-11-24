"""Attribution, source, and license completeness across every model catalog kind."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from localsr.core.model_catalog import (
    AUDIO_MODEL_CATALOG,
    FACE_DETECTOR_MODEL,
    INTERPOLATION_MODEL_CATALOG,
    MODEL_CATALOG,
    VIDEO_MODEL_CATALOG,
)

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "export_desktop_catalog", ROOT / "scripts" / "export_desktop_catalog.py"
)
assert SPEC and SPEC.loader
exporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(exporter)


def test_every_python_catalog_model_has_author_source_and_license() -> None:
    for model in (*MODEL_CATALOG, FACE_DETECTOR_MODEL):
        assert model.author.strip(), model.model_id
        assert model.source_url.startswith("https://"), model.model_id
        assert model.license_name.strip(), model.model_id
        if model.commercial_use_status == "allowed":
            assert model.license_url.startswith("https://"), model.model_id
        else:
            assert model.license_url == "", model.model_id

    for model in (*VIDEO_MODEL_CATALOG, *INTERPOLATION_MODEL_CATALOG, *AUDIO_MODEL_CATALOG):
        assert model.author.strip(), model.model_id
        assert model.source_url.startswith("https://"), model.model_id
        assert model.license_name.strip(), model.model_id
        assert model.license_url.startswith("https://"), model.model_id


def test_desktop_catalog_export_carries_attribution_fields_for_all_kinds() -> None:
    manifest = exporter.build_manifest()
    shipped = json.loads(
        (ROOT / "desktop" / "src-tauri" / "resources" / "model-catalog.json").read_text(
            encoding="utf-8"
        )
    )
    assert shipped == manifest

    for section in ("models", "video_models", "interpolation_models", "audio_models"):
        entries = manifest[section]
        assert entries, f"{section} must not be empty"
        for entry in entries:
            assert entry["author"].strip(), entry["model_id"]
            assert entry["source_url"].startswith("https://"), entry["model_id"]
            assert entry["license_name"].strip(), entry["model_id"]
            assert isinstance(entry["attribution_required"], bool), entry["model_id"]
            if entry.get("commercial_use_allowed") is True:
                assert entry["license_url"].startswith("https://"), entry["model_id"]
            else:
                assert entry["license_url"] == "", entry["model_id"]


def test_edit_catalog_entries_have_author_source_license_and_rights() -> None:
    edit_models = json.loads(
        (ROOT / "src" / "localsr" / "core" / "edit_catalog.json").read_text(encoding="utf-8")
    )
    assert edit_models
    for entry in edit_models:
        assert entry["author"].strip(), entry["model_id"]
        assert entry["source_url"].startswith("https://"), entry["model_id"]
        assert entry["license_name"].strip(), entry["model_id"]
        assert entry["license_url"].startswith("https://"), entry["model_id"]
        assert isinstance(entry["attribution_required"], bool), entry["model_id"]
        assert entry["commercial_use_allowed"] is True, entry["model_id"]
