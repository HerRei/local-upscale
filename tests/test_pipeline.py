import json
import threading

import numpy as np
import pytest
import torch

from localsr.core.model_adapter import NormalizedModelInfo
from localsr.core.pipeline import parse_pipeline_stages
from localsr.worker.server import WorkerServer


def _stage(kind: str, model_id: str, path: str, **extra):
    return {"kind": kind, "model_id": model_id, "model_path": path, **extra}


def test_pipeline_schema_accepts_restore_then_upscale_and_fused_face():
    stages = parse_pipeline_stages(
        [
            _stage("deblock", "fbcnn", "/models/fbcnn.pth"),
            _stage("upscale", "x2", "/models/x2.pth"),
            _stage(
                "face_restore",
                "face",
                "/models/face.pth",
                fidelity=0.65,
                execution="fused-with-upscale",
            ),
        ]
    )
    assert [stage.kind for stage in stages] == ["deblock", "upscale", "face_restore"]
    assert stages[-1].fidelity == 0.65


@pytest.mark.parametrize(
    "stages, message",
    [
        ([_stage("upscale", "a", "a"), _stage("upscale", "b", "b")], "one upscale"),
        ([_stage("deblock", "a", "a")], "restore-only"),
        (
            [
                _stage("upscale", "a", "a"),
                _stage("face_restore", "f", "f", execution="sequential"),
            ],
            "fused",
        ),
    ],
)
def test_pipeline_schema_rejects_unbounded_or_ambiguous_order(stages, message):
    with pytest.raises(ValueError, match=message):
        parse_pipeline_stages(stages)


class _Writer:
    def __init__(self, array):
        self.array = array
        self.cleaned = False

    def get_array(self):
        return self.array

    def cleanup(self):
        self.cleaned = True


class _Adapter:
    allow_unverified_checkpoints = False

    def inspect(self, path):
        scale = 1 if path.endswith("restore.pth") else 2
        return NormalizedModelInfo("Test", scale, 3, 3, True, False, 1, 1, path, [])

    def release(self):
        pass


class _IncompatibleFaceAdapter(_Adapter):
    def inspect(self, path):
        if not path.endswith("face.pth"):
            return super().inspect(path)
        return NormalizedModelInfo("Test", 3, 3, 3, True, False, 1, 1, path, [])


class _PipelineEngine:
    def __init__(self, cancel_event: threading.Event | None = None, fail_path: str = ""):
        self.calls = []
        self.writers = []
        self.cancel_event = cancel_event
        self.fail_path = fail_path

    def process_image(self, **kwargs):
        path = kwargs["model_path"]
        self.calls.append(path)
        if path == self.fail_path:
            raise RuntimeError("checkpoint exploded")
        tensor = kwargs["img_data"]["tensor"]
        scale = kwargs["model_info"].scale
        output = np.full(
            (3, int(tensor.shape[1]) * scale, int(tensor.shape[2]) * scale),
            len(self.calls) * 20,
            dtype=np.uint8,
        )
        kwargs["progress_callback"](1, 1, kwargs["tile_size"])
        writer = _Writer(output)
        self.writers.append(writer)
        if self.cancel_event is not None and len(self.calls) == 1:
            self.cancel_event.set()
        return writer

    def release_model(self, _path=None):
        pass


def _job_data(tmp_path):
    return {
        "job_id": "pipeline-job",
        "image_path": str(tmp_path / "input.png"),
        "model_path": str(tmp_path / "upscale.pth"),
        "output_path": str(tmp_path / "output.png"),
        "scratch_directory": str(tmp_path / "scratch"),
        "output_format": "png",
        "device": "cpu",
        "precision": "fp32",
        "tile_size": 64,
        "halo": 8,
        "jpeg_quality": 98,
        "preserve_metadata": True,
        "safe_memory": True,
        "output_scale": 2,
        "preview_enabled": False,
        "stages": [
            _stage("restore", "restore", str(tmp_path / "restore.pth")),
            _stage("upscale", "upscale", str(tmp_path / "upscale.pth")),
        ],
    }


def test_worker_runs_stages_in_order_without_lossy_intermediate(tmp_path, monkeypatch, capsys):
    from localsr.core.image_io import ImageManager

    server = WorkerServer()
    server.model_adapter = _Adapter()
    engine = _PipelineEngine()
    server.engine = engine
    saved = {}
    monkeypatch.setattr(
        ImageManager,
        "load",
        lambda *_: {"tensor": torch.zeros((3, 5, 7)), "safe_exif": {}, "icc_profile": None},
    )

    def save(_self, writer, _path, **kwargs):
        saved["shape"] = writer.get_array().shape
        saved.update(kwargs)

    monkeypatch.setattr(ImageManager, "save", save)
    server._run_job("pipeline-job", _job_data(tmp_path))

    messages = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert engine.calls == [str(tmp_path / "restore.pth"), str(tmp_path / "upscale.pth")]
    assert saved["shape"] == (3, 10, 14)
    assert saved["scale"] == 2
    assert [
        item["data"]["stage_kind"] for item in messages if item["type"] == "stage_completed"
    ] == [
        "restore",
        "upscale",
    ]
    assert all(writer.cleaned for writer in engine.writers)


def test_worker_cancels_between_stages_and_cleans_intermediate(tmp_path, monkeypatch, capsys):
    from localsr.core.image_io import ImageManager

    server = WorkerServer()
    server.model_adapter = _Adapter()
    engine = _PipelineEngine(cancel_event=server.cancel_event)
    server.engine = engine
    monkeypatch.setattr(ImageManager, "load", lambda *_: {"tensor": torch.zeros((3, 4, 4))})
    monkeypatch.setattr(ImageManager, "save", lambda *_args, **_kwargs: None)

    server._run_job("pipeline-job", _job_data(tmp_path))
    output = capsys.readouterr().out
    assert engine.calls == [str(tmp_path / "restore.pth")]
    assert engine.writers[0].cleaned
    assert '"type": "job_cancelled"' in output


def test_worker_failure_names_the_actual_pipeline_stage(tmp_path, monkeypatch):
    from localsr.core.image_io import ImageManager

    server = WorkerServer()
    server.model_adapter = _Adapter()
    server.engine = _PipelineEngine(fail_path=str(tmp_path / "restore.pth"))
    monkeypatch.setattr(ImageManager, "load", lambda *_: {"tensor": torch.zeros((3, 4, 4))})

    with pytest.raises(RuntimeError, match=r"restore stage \(restore\) failed"):
        server._run_job("pipeline-job", _job_data(tmp_path))


def test_worker_rejects_incompatible_face_companion_before_primary_inference(tmp_path, monkeypatch):
    from localsr.core.image_io import ImageManager

    data = _job_data(tmp_path)
    data["face_model_path"] = str(tmp_path / "face.pth")
    data["stages"].append(
        _stage(
            "face_restore",
            "face",
            str(tmp_path / "face.pth"),
            fidelity=0.7,
            execution="fused-with-upscale",
        )
    )
    server = WorkerServer()
    server.model_adapter = _IncompatibleFaceAdapter()
    engine = _PipelineEngine()
    server.engine = engine
    monkeypatch.setattr(ImageManager, "load", lambda *_: {"tensor": torch.zeros((3, 4, 4))})

    with pytest.raises(
        RuntimeError,
        match=r"face_restore stage \(face\) failed: Face and primary checkpoints must have the same native scale",
    ):
        server._run_job("pipeline-job", data)
    assert engine.calls == [str(tmp_path / "restore.pth")]
