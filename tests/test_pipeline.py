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

    def __init__(self, log=None):
        self.log = log if log is not None else []

    def inspect(self, path):
        scale = 1 if path.endswith("restore.pth") else 2
        return NormalizedModelInfo("Test", scale, 3, 3, True, False, 1, 1, path, [])

    def release(self):
        self.log.append(("adapter.release",))


class _IncompatibleFaceAdapter(_Adapter):
    def inspect(self, path):
        if not path.endswith("face.pth"):
            return super().inspect(path)
        return NormalizedModelInfo("Test", 3, 3, 3, True, False, 1, 1, path, [])


class _PipelineEngine:
    def __init__(
        self,
        cancel_event: threading.Event | None = None,
        fail_path: str = "",
        failure: Exception | None = None,
        log: list | None = None,
    ):
        self.log = log if log is not None else []
        self.halos = []
        self.calls = []
        self.writers = []
        self.cancel_event = cancel_event
        self.fail_path = fail_path
        self.failure = failure or RuntimeError("checkpoint exploded")
        self.inputs = []

    def process_image(self, **kwargs):
        path = kwargs["model_path"]
        self.calls.append(path)
        self.halos.append(kwargs["halo"])
        self.log.append(("process", path))
        self.inputs.append(kwargs["img_data"]["tensor"].clone())
        if path == self.fail_path:
            raise self.failure
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

    def release_model(self, path=None):
        self.log.append(("engine.release", path))

    def release_models_except(self, keep):
        self.log.append(("engine.keep_only", frozenset(keep)))


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


def test_a_fix_the_model_cannot_process_is_skipped_and_the_job_says_so(
    tmp_path, monkeypatch, capsys
):
    # Issue #95: NAFNet's deblur runs away on line art; the job still upscales the image.
    from localsr.core.image_io import ImageManager
    from localsr.core.inference import UnstableRestorationError

    server = WorkerServer()
    server.model_adapter = _Adapter()
    engine = _PipelineEngine(
        fail_path=str(tmp_path / "restore.pth"),
        failure=UnstableRestorationError("NAFNet produced unstable values"),
    )
    server.engine = engine
    source = torch.rand((3, 5, 7))
    monkeypatch.setattr(ImageManager, "load", lambda *_: {"tensor": source.clone()})
    saved = {}
    monkeypatch.setattr(
        ImageManager,
        "save",
        lambda _self, writer, _path, **_kw: saved.update(shape=writer.get_array().shape),
    )
    server._run_job("pipeline-job", _job_data(tmp_path))

    messages = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert engine.calls == [str(tmp_path / "restore.pth"), str(tmp_path / "upscale.pth")]
    assert torch.equal(engine.inputs[1], source)  # the upscale got the unchanged image
    assert saved["shape"] == (3, 10, 14)
    (completed,) = [m["data"] for m in messages if m["type"] == "job_completed"]
    assert "was skipped for this image" in completed["notes"]
    assert any(m["type"] == "warning" and "skipped" in m["data"]["message"] for m in messages)
    assert not any(m["type"] == "job_failed" for m in messages)


def test_a_restore_only_job_still_fails_when_its_model_cannot_process_the_image(
    tmp_path, monkeypatch
):
    from localsr.core.image_io import ImageManager
    from localsr.core.inference import UnstableRestorationError

    data = _job_data(tmp_path)
    data["output_scale"] = 1
    data["model_path"] = str(tmp_path / "restore.pth")
    data["stages"] = [_stage("restore", "restore", str(tmp_path / "restore.pth"))]
    server = WorkerServer()
    server.model_adapter = _Adapter()
    server.engine = _PipelineEngine(
        fail_path=str(tmp_path / "restore.pth"),
        failure=UnstableRestorationError("NAFNet produced unstable values"),
    )
    monkeypatch.setattr(ImageManager, "load", lambda *_: {"tensor": torch.zeros((3, 4, 4))})
    with pytest.raises(RuntimeError, match=r"restore stage \(restore\) failed"):
        server._run_job("pipeline-job", data)


@pytest.mark.parametrize(("job_halo", "fix_halo"), [(8, 32), (48, 48)])
def test_a_fix_runs_with_at_least_the_overlap_its_model_needs(
    tmp_path, monkeypatch, job_halo, fix_halo
):
    # NAFNet SIDD recommends 32 pixels; a fast upscaler's 8 would leave seams in the fix.
    from localsr.core.image_io import ImageManager

    data = _job_data(tmp_path)
    data["halo"] = job_halo
    data["stages"][0] = _stage("restore", "nafnet_sidd_width64", str(tmp_path / "restore.pth"))
    server = WorkerServer()
    server.model_adapter = _Adapter()
    engine = _PipelineEngine()
    server.engine = engine
    monkeypatch.setattr(ImageManager, "load", lambda *_: {"tensor": torch.zeros((3, 4, 4))})
    monkeypatch.setattr(ImageManager, "save", lambda *_args, **_kwargs: None)

    server._run_job("pipeline-job", data)
    assert engine.halos == [fix_halo, job_halo]


def _fix_released_before_upscale(log, fix_path):
    after_fix = log[log.index(("process", fix_path)) + 1 :]
    upscale = [i for i, entry in enumerate(after_fix) if entry[0] == "process"]
    before_upscale = after_fix[: upscale[0]] if upscale else after_fix
    return ("engine.release", fix_path) in before_upscale and ("adapter.release",) in before_upscale


@pytest.mark.parametrize("outcome", ["success", "unstable", "failure", "cancel"])
def test_the_fix_model_leaves_memory_before_the_upscaler_runs(tmp_path, monkeypatch, outcome):
    from localsr.core.image_io import ImageManager
    from localsr.core.inference import UnstableRestorationError

    fix_path = str(tmp_path / "restore.pth")
    log = []
    server = WorkerServer()
    server.model_adapter = _Adapter(log)
    server.engine = _PipelineEngine(
        cancel_event=server.cancel_event if outcome == "cancel" else None,
        fail_path=fix_path if outcome in ("unstable", "failure") else "",
        failure=UnstableRestorationError("unstable") if outcome == "unstable" else None,
        log=log,
    )
    monkeypatch.setattr(ImageManager, "load", lambda *_: {"tensor": torch.zeros((3, 4, 4))})
    monkeypatch.setattr(ImageManager, "save", lambda *_args, **_kwargs: None)

    if outcome == "failure":
        with pytest.raises(RuntimeError):
            server._run_job("pipeline-job", _job_data(tmp_path))
    else:
        server._run_job("pipeline-job", _job_data(tmp_path))
    assert _fix_released_before_upscale(log, fix_path)


@pytest.mark.parametrize("with_fix", [True, False])
def test_an_image_job_keeps_only_the_models_it_runs_next_cached(tmp_path, monkeypatch, with_fix):
    # The engine cache otherwise holds every upscaler used since the engine started. With
    # a fix, even this job's upscaler (cached by the previous image) must not sit beside it.
    from localsr.core.image_io import ImageManager

    log = []
    data = _job_data(tmp_path)
    data["face_model_path"] = str(tmp_path / "face.pth")
    if not with_fix:
        data["stages"] = data["stages"][1:]
    server = WorkerServer()
    server.model_adapter = _Adapter(log)
    server.engine = _PipelineEngine(log=log)
    monkeypatch.setattr(ImageManager, "load", lambda *_: {"tensor": torch.zeros((3, 4, 4))})
    monkeypatch.setattr(ImageManager, "save", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "localsr.worker.image_job.ImageJobRunner._process_faces", lambda *_args, **_kwargs: None
    )

    server._run_job("pipeline-job", data)
    keep = [entry for entry in log if entry[0] == "engine.keep_only"]
    expected = (
        {str(tmp_path / "restore.pth")}
        if with_fix
        else {str(tmp_path / "upscale.pth"), str(tmp_path / "face.pth")}
    )
    assert keep == [("engine.keep_only", frozenset(expected))]
    assert log.index(keep[0]) < next(i for i, entry in enumerate(log) if entry[0] == "process")


def test_an_image_job_caps_memory_with_the_users_safe_memory_choice(monkeypatch):
    # Turning safe memory off must also lift the allocator ceiling, not only the tile backoff.
    from localsr.core import inference

    applied = []
    monkeypatch.setattr(
        inference, "_apply_gpu_memory_limit", lambda device, safe: applied.append(safe)
    )
    engine = inference.InferenceEngine(None)

    class _LoadingAdapter:
        def load(self, *_args, **_kwargs):
            return object(), None

    engine.model_adapter = _LoadingAdapter()
    monkeypatch.setattr(engine, "_attach_status", lambda _model: None)
    info = NormalizedModelInfo("Test", 2, 3, 3, True, False, 1, 1, "a.pth", [])
    engine.load_model("a.pth", "cpu", "fp32", info, safe_memory=False)
    engine.load_model("b.pth", "cpu", "fp32", info, safe_memory=False)
    # The same, already cached model with the other choice must still move the cap.
    engine.load_model("a.pth", "cpu", "fp32", info, safe_memory=True)
    assert applied == [False, True]


def test_the_gpu_cap_counts_memory_localsr_already_holds(monkeypatch):
    # After the cache is emptied between images, PyTorch still holds its reserved blocks,
    # which the driver reports as used. The cap must not shrink because of LocalSR itself.
    from localsr.core import inference

    gib = 1024**3
    fractions = []
    monkeypatch.setattr(inference, "_integrated_gpu", lambda *_args: False)
    monkeypatch.setattr(torch.cuda, "mem_get_info", lambda *_args: (int(1.8 * gib), 8 * gib))
    monkeypatch.setattr(torch.cuda, "memory_reserved", lambda *_args: 6 * gib)
    monkeypatch.setattr(
        torch.cuda,
        "set_per_process_memory_fraction",
        lambda fraction, *_args: fractions.append(fraction),
    )
    inference._apply_gpu_memory_limit(torch.device("cuda", 0), False)
    assert fractions == [pytest.approx(0.9 * 7.8 / 8)]


def test_the_engine_releases_every_cached_model_but_the_ones_to_keep():
    from localsr.core.inference import InferenceEngine

    engine = InferenceEngine(None)
    for path in ("old-best.pth", "old-quick.pth", "upscale.pth"):
        engine._loaded_models[path] = (object(), torch.device("cpu"), torch.float32)
        engine._loaded_backend_ids[path] = "cpu"
    engine._active_path = "old-best.pth"

    engine.release_models_except({"upscale.pth", "face.pth"})
    assert list(engine._loaded_models) == ["upscale.pth"]
    assert list(engine._loaded_backend_ids) == ["upscale.pth"]
    assert engine._active_path == "upscale.pth"
