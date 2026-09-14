from types import SimpleNamespace

import pytest

from localsr.core import video_engines
from localsr.worker.server import WorkerServer, _engine_features, _video_engines


@pytest.mark.parametrize("backend", ["directml:0", "xpu:0"])
def test_seedvr2_rejects_unsupported_gpu_before_loading_or_verifying_a_bundle(backend):
    with pytest.raises(ValueError, match="Choose frame-by-frame video"):
        WorkerServer._run_video_job(
            SimpleNamespace(), "unsupported", {"model_kind": "seedvr2", "device": backend}
        )


def test_directml_runtime_keeps_frame_engine_and_explains_seedvr2_incompatibility(monkeypatch):
    versions = {"torch": "2.4.1+cpu", "diffusers": "0.38.0"}
    monkeypatch.setattr(video_engines, "version", versions.__getitem__)
    assert _video_engines() == ["spandrel_image"]
    assert "video_seedvr2" not in _engine_features()
    with pytest.raises(ValueError, match="PyTorch version"):
        WorkerServer._run_video_job(
            SimpleNamespace(), "unsupported-cpu", {"model_kind": "seedvr2", "device": "cpu"}
        )
    assert video_engines.resolve_video_engine("spandrel_image") is None
    factory = video_engines.resolve_video_engine("seedvr2")
    assert callable(factory)
    from localsr.video_models.seedvr2.engine import _generation_utils

    with pytest.raises(video_engines.TemporalEngineUnavailable, match="PyTorch version"):
        _generation_utils()
    versions["torch"] = "2.10.0+cu128"
    assert video_engines.seedvr2_runtime_issue() is None
    assert "seedvr2" in _video_engines()
    assert "video_seedvr2" in _engine_features()
