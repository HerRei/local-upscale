import importlib.util
import json
from pathlib import Path

import pytest


def tool():
    path = Path(__file__).parents[1] / "scripts/prepare_beta_download_inventory.py"
    spec = importlib.util.spec_from_file_location("download_inventory", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_review_inventory_preserves_real_hashes_without_enabling_downloads(tmp_path, monkeypatch):
    import hashlib

    module = tool()
    (tmp_path / "ci").mkdir()
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    data = b"retained acceptance package"
    (artifacts / "review.msix").write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    plan = {
        "version": "0.1.0-beta",
        "targets": [
            {
                "id": "windows-x86_64-directml",
                "platform": "windows",
                "architecture": "x86_64",
                "backend": "DirectML",
                "coverage": "main-path",
                "review_artifacts": [{"path": "review.msix", "size": len(data), "sha256": digest}],
            },
            {
                "id": "windows-x86_64-cuda",
                "platform": "windows",
                "architecture": "x86_64",
                "backend": "CUDA",
                "coverage": "labs",
                "review_artifacts": [],
            },
        ],
    }
    (tmp_path / "ci/public-beta-release.json").write_text(json.dumps(plan))
    monkeypatch.setattr(module, "ROOT", tmp_path)
    output = tmp_path / "output/downloads.json"
    result = module.prepare(artifacts, output)
    assert not result["ready"] and not result["published"]
    assert all(
        t["download_url"] is None and t["final_artifact"] is None and not t["final_package_ready"]
        for t in result["targets"]
    )
    assert result["targets"][0]["review_artifacts"][0]["sha256"] == digest
    assert result["targets"][1]["status"] == "not-built"
    assert result["targets"][1]["review_artifacts"] == []
    assert digest in output.with_name("REVIEW-SHA256SUMS").read_text()
    # A changed package must never inherit the previous review's digest.
    (artifacts / "review.msix").write_bytes(b"substituted package")
    with pytest.raises(ValueError, match="Retained artifact changed"):
        module.prepare(artifacts, tmp_path / "invalid/downloads.json")
    assert not (tmp_path / "invalid/downloads.json").exists()
