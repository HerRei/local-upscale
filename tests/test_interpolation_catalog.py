"""The frame-interpolation catalog entry, its verified bundle and the desktop export."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from urllib.parse import urlparse

from localsr.core.model_catalog import (
    DEFAULT_INTERPOLATION_MODEL_ID,
    INTERPOLATION_CATALOG_BY_ID,
    INTERPOLATION_MODEL_CATALOG,
    VIDEO_CATALOG_BY_ID,
    ModelStore,
)

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "export_desktop_catalog", ROOT / "scripts" / "export_desktop_catalog.py"
)
assert SPEC and SPEC.loader
exporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(exporter)


def test_rife_426_is_the_default_pinned_mit_checkpoint():
    model = INTERPOLATION_CATALOG_BY_ID[DEFAULT_INTERPOLATION_MODEL_ID]
    assert model.model_id == "rife_4_26"
    assert model.engine_kind == "rife"
    assert model.license_name == "MIT"
    assert model.source_url == "https://github.com/hzwer/Practical-RIFE"
    (checkpoint,) = model.files
    assert checkpoint.filename == "flownet_v4.26.pkl"
    assert checkpoint.size_bytes == 24_636_301
    # The bytes of train_log/flownet.pkl in the official v4.26 Google Drive archive.
    assert checkpoint.sha256 == "45c7f74156704769dc9f85cfcaf8552e1e926f9399dcfa3a553dee88fac6f53f"
    assert urlparse(checkpoint.download_url).scheme == "https"


def test_interpolation_models_never_appear_as_video_engines():
    for model in INTERPOLATION_MODEL_CATALOG:
        assert model.model_id not in VIDEO_CATALOG_BY_ID


def test_the_bundle_is_installed_only_with_the_exact_bytes(tmp_path):
    model = INTERPOLATION_CATALOG_BY_ID[DEFAULT_INTERPOLATION_MODEL_ID]
    store = ModelStore(tmp_path)
    assert store.bundle_dir_for(model) == tmp_path / "rife_4_26"
    assert not store.is_bundle_installed(model)
    (checkpoint,) = model.files
    path = store.bundle_file_path(model, checkpoint)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\0" * 16)
    assert not store.is_bundle_installed(model)
    assert hashlib.sha256(path.read_bytes()).hexdigest() != checkpoint.sha256


def test_the_desktop_catalog_lists_interpolation_models_separately():
    manifest = exporter.build_manifest()
    assert manifest["schema_version"] == 3
    (entry,) = manifest["interpolation_models"]
    assert entry["model_id"] == "rife_4_26"
    assert entry["engine_id"] == "localsr.video.rife"
    assert entry["support_tier"] == "labs"
    assert entry["automated_download_allowed"] is True
    assert entry["commercial_use_allowed"] is True
    assert entry["license_url"] == "https://opensource.org/license/mit"
    assert "rife_4_26" not in {model["model_id"] for model in manifest["video_models"]}
    shipped = json.loads(
        (ROOT / "desktop" / "src-tauri" / "resources" / "model-catalog.json").read_text()
    )
    assert shipped == manifest
