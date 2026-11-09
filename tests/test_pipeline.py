import json
import threading

import numpy as np
import pytest
import torch

from localsr.core.model_adapter import NormalizedModelInfo
from localsr.core.model_catalog import CATALOG_BY_ID
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


@pytest.mark.parametrize("count", range(4))
@pytest.mark.parametrize("restoring", [False, True])
def test_ordered_fix_topology(count, restoring):
    from localsr.core.pipeline import resolve_image_pipeline

    prefix = [
        _stage(kind, fix, f"{fix}.pth", fix=fix, role="fix")
        for kind, fix in [("restore", "blur"), ("deblock", "jpeg"), ("restore", "noise")][:count]
    ]
    stages = prefix + [
        _stage("restore" if restoring else "upscale", "main", "main.pth", role="primary")
    ]
    if not restoring:
        stages.append(_stage("face_restore", "face", "face.pth", execution="fused-with-upscale"))
    pipeline = resolve_image_pipeline(
        raw_stages=stages,
        model_path="main.pth",
        output_scale=1 if restoring else 2,
        face_model_path=None if restoring else "face.pth",
    )
    assert pipeline.primary_index == count
    assert [stage.model_id for stage in pipeline.preprocess_stages] == [
        s["model_id"] for s in prefix
    ]


@pytest.mark.parametrize(
    "extra",
    [
        [_stage("restore", "tail", "tail")],
        [_stage("upscale", "other", "other")],
        [_stage("face_restore", "face", "face", execution="fused-with-upscale")] * 2,
    ],
)
def test_multi_fix_rejects_invalid_tail(extra):
    with pytest.raises(ValueError):
        parse_pipeline_stages(
            [_stage("restore", "a", "a"), _stage("upscale", "main", "main"), *extra]
        )


def test_multi_fix_rejects_duplicate_semantics_and_primary_roles():
    for stages in (
        [
            _stage("restore", "a", "a", fix="noise"),
            _stage("restore", "b", "b", fix="noise"),
            _stage("upscale", "main", "main"),
        ],
        [
            _stage("restore", "a", "a", role="primary"),
            _stage("upscale", "main", "main", role="primary"),
        ],
        [_stage("restore", "a", "a", fix="unknown"), _stage("upscale", "main", "main")],
        [_stage("restore", str(i), str(i)) for i in range(4)] + [_stage("upscale", "main", "main")],
        [_stage("restore", str(i), str(i)) for i in range(5)] + [_stage("upscale", "main", "main")],
    ):
        with pytest.raises(ValueError):
            parse_pipeline_stages(stages)


@pytest.mark.parametrize("skipped", [(), (0,), (1,), (2,), (0, 1, 2)])
@pytest.mark.parametrize("restoring", [False, True])
def test_multi_fix_feeds_outputs_skips_only_unstable_and_releases_each_stage(
    tmp_path, monkeypatch, capsys, skipped, restoring
):
    from localsr.core.image_io import ImageManager
    from localsr.core.inference import UnstableRestorationError

    log = []
    server = WorkerServer()
    server.model_adapter = _Adapter(log)
    engine = _PipelineEngine(log=log)
    server.engine = engine
    data = _job_data(tmp_path)
    fixes = [
        ("restore", "nafnet_gopro_deblur", "blur"),
        ("deblock", "fbcnn_color", "jpeg"),
        ("restore", "nafnet_sidd_width64", "noise"),
    ]
    paths = [str(tmp_path / f"{i}-restore.pth") for i in range(3)]
    data["stages"] = [
        _stage(kind, model, path, role="fix", fix=fix)
        for (kind, model, fix), path in zip(fixes, paths, strict=True)
    ] + data["stages"][1:]
    if restoring:
        data["model_path"] = str(tmp_path / "main-restore.pth")
        data["output_scale"] = 1
        data["stages"][-1] = _stage("restore", "main", data["model_path"], role="primary")
    process = engine.process_image

    def run_stage(**kwargs):
        if kwargs["model_path"] in [paths[i] for i in skipped]:
            engine.calls.append(kwargs["model_path"])
            engine.inputs.append(kwargs["img_data"]["tensor"].clone())
            log.append(("process", kwargs["model_path"]))
            kwargs["progress_callback"](1, 2, 64)
            raise UnstableRestorationError("unstable values")
        return process(**kwargs)

    engine.process_image = run_stage
    source = torch.zeros((3, 5, 7))
    metadata = {
        "safe_exif": {"artist": "author"},
        "icc_profile": b"profile",
        "alpha": torch.ones(5, 7),
    }
    monkeypatch.setattr(ImageManager, "load", lambda *_: {"tensor": source, **metadata})
    saves = []
    monkeypatch.setattr(ImageManager, "save", lambda *_args, **kwargs: saves.append(kwargs))
    server._run_job("pipeline-job", data)
    assert engine.calls == paths + [data["model_path"]]
    previous = source
    for index, input_tensor in enumerate(engine.inputs):
        assert torch.equal(input_tensor, previous)
        if index not in skipped:
            previous = torch.full_like(source, (index + 1) * 20 / 255)
    assert len(saves) == 1
    assert saves[0]["safe_exif"] == metadata["safe_exif"]
    assert saves[0]["icc_profile"] == b"profile"
    assert all(writer.cleaned for writer in engine.writers)
    for path in paths:
        assert _fix_released_before_upscale(log, path)
    expected_halos = [max(8, CATALOG_BY_ID[model].recommended_halo) for _, model, _ in fixes]
    assert engine.halos == [halo for i, halo in enumerate(expected_halos) if i not in skipped] + [8]
    messages = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [m["data"]["stage_index"] for m in messages if m["type"] == "stage_started"] == list(
        range(4)
    )
    percentages = [m["data"]["percentage"] for m in messages if m["type"] == "progress"]
    assert percentages == sorted(percentages)
    notes = next(m["data"]["notes"] for m in messages if m["type"] == "job_completed")
    assert notes.count("was skipped") == len(skipped)
    # Each note names the problem and the model a person chose, not an internal ID.
    names = {"blur": "blur", "jpeg": "JPEG", "noise": "noise"}
    for index in skipped:
        _, model, fix = fixes[index]
        display = CATALOG_BY_ID[model].display_name
        assert f"The {names[fix]} fix ({display}) was skipped for this image" in notes
        assert model not in notes


@pytest.mark.parametrize("boundary", range(4))
def test_multi_fix_cancellation_at_each_boundary(tmp_path, monkeypatch, capsys, boundary):
    from localsr.core.image_io import ImageManager

    server = WorkerServer()
    server.model_adapter = _Adapter()
    engine = _PipelineEngine()
    server.engine = engine
    data = _job_data(tmp_path)
    data["stages"] = [
        _stage("restore", str(i), str(tmp_path / f"{i}-restore.pth")) for i in range(3)
    ] + data["stages"][1:]
    process = engine.process_image

    def run_stage(**kwargs):
        result = process(**kwargs)
        if len(engine.calls) == boundary:
            server.cancel_event.set()
        return result

    engine.process_image = run_stage
    if boundary == 0:
        server.cancel_event.set()
    monkeypatch.setattr(ImageManager, "load", lambda *_: {"tensor": torch.zeros((3, 4, 4))})
    monkeypatch.setattr(
        ImageManager, "save", lambda *_a, **_kw: pytest.fail("Cancelled job saved output")
    )
    server._run_job("pipeline-job", data)
    assert len(engine.calls) == boundary
    assert all(writer.cleaned for writer in engine.writers)
    assert '"type": "job_cancelled"' in capsys.readouterr().out


def _five_stage_job(tmp_path):
    """Blur, JPEG and noise fixes, then the upscaler with its fused face pass."""
    data = _job_data(tmp_path)
    data["face_model_path"] = str(tmp_path / "face.pth")
    data["stages"] = [
        _stage("restore", "blur", str(tmp_path / "0-restore.pth"), role="fix", fix="blur"),
        _stage("deblock", "jpeg", str(tmp_path / "1-restore.pth"), role="fix", fix="jpeg"),
        _stage("restore", "noise", str(tmp_path / "2-restore.pth"), role="fix", fix="noise"),
        _stage("upscale", "upscale", data["model_path"], role="primary"),
        _stage(
            "face_restore",
            "face",
            data["face_model_path"],
            fidelity=0.7,
            execution="fused-with-upscale",
        ),
    ]
    return data


@pytest.mark.parametrize(
    ("skipped", "percentages", "stage_progress"),
    [
        (
            (),
            [10, 20, 30, 40, 50, 60, 80, 100],
            # The upscaler and its fused face pass share one run: two stages of five.
            [(0, 10), (0, 20), (1, 30), (1, 40), (2, 50), (2, 60), (3, 80), (4, 80)]
            + [(3, 100), (4, 100)],
        ),
        (
            # A fix skipped halfway hands over to the next stage's own share.
            (1,),
            [10, 20, 30, 50, 60, 80, 100],
            [(0, 10), (0, 20), (1, 30), (2, 50), (2, 60), (3, 80), (4, 80), (3, 100), (4, 100)],
        ),
    ],
)
def test_several_fixes_and_a_fused_face_pass_report_exact_progress(
    tmp_path, monkeypatch, capsys, skipped, percentages, stage_progress
):
    from localsr.core.image_io import ImageManager
    from localsr.core.inference import UnstableRestorationError

    data = _five_stage_job(tmp_path)
    server = WorkerServer()
    server.model_adapter = _Adapter()
    engine = _PipelineEngine()
    server.engine = engine

    def process(**kwargs):
        # Two tiles per stage: half way, then done.
        engine.calls.append(kwargs["model_path"])
        kwargs["progress_callback"](1, 2, kwargs["tile_size"])
        if kwargs["model_path"] in [data["stages"][i]["model_path"] for i in skipped]:
            raise UnstableRestorationError("unstable values")
        kwargs["progress_callback"](2, 2, kwargs["tile_size"])
        tensor = kwargs["img_data"]["tensor"]
        scale = kwargs["model_info"].scale
        writer = _Writer(
            np.zeros((3, tensor.shape[1] * scale, tensor.shape[2] * scale), dtype=np.uint8)
        )
        engine.writers.append(writer)
        return writer

    engine.process_image = process
    monkeypatch.setattr(
        "localsr.worker.image_job.ImageJobRunner._process_faces", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(ImageManager, "load", lambda *_: {"tensor": torch.zeros((3, 4, 4))})
    monkeypatch.setattr(ImageManager, "save", lambda *_a, **_kw: None)
    server._run_job("pipeline-job", data)

    messages = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    of = lambda kind: [m["data"] for m in messages if m["type"] == kind]  # noqa: E731
    assert [round(m["percentage"], 6) for m in of("progress")] == percentages
    assert [(m["stage_index"], round(m["percentage"], 6)) for m in of("stage_progress")] == (
        stage_progress
    )
    for kind in ("stage_started", "stage_completed"):
        assert [(m["stage_index"], m["stage_count"]) for m in of(kind)] == [
            (index, 5) for index in range(5)
        ]
    assert [m["stage_kind"] for m in of("stage_started")] == [
        "restore",
        "deblock",
        "restore",
        "upscale",
        "face_restore",
    ]


@pytest.mark.parametrize("boundary", range(4))
def test_a_cancel_between_finished_stages_stops_before_the_next_one(
    tmp_path, monkeypatch, capsys, boundary
):
    # Cancel arrives after a stage has finished and released its model, so only the checks
    # between stages can stop the job: no later stage runs and nothing is saved.
    from localsr.core.image_io import ImageManager
    from localsr.worker.job_progress import ImageJobProgress

    data = _five_stage_job(tmp_path)
    paths = [stage["model_path"] for stage in data["stages"][:4]]
    server = WorkerServer()
    server.model_adapter = _Adapter()
    engine = _PipelineEngine()
    server.engine = engine
    complete = ImageJobProgress.complete

    def complete_then_cancel(self, index, stage):
        complete(self, index, stage)
        if index == boundary:
            server.cancel_event.set()

    monkeypatch.setattr(ImageJobProgress, "complete", complete_then_cancel)
    monkeypatch.setattr(
        "localsr.worker.image_job.ImageJobRunner._process_faces", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(ImageManager, "load", lambda *_: {"tensor": torch.zeros((3, 4, 4))})
    monkeypatch.setattr(
        ImageManager, "save", lambda *_a, **_kw: pytest.fail("Cancelled job saved output")
    )
    server._run_job("pipeline-job", data)
    assert engine.calls == paths[: boundary + 1]
    assert all(writer.cleaned for writer in engine.writers)
    output = capsys.readouterr().out
    assert '"type": "job_cancelled"' in output
    assert '"type": "job_completed"' not in output


def test_engine_advertises_ordered_image_fixes():
    from localsr.worker.server import _engine_features

    assert "image_multi_fix" in _engine_features()


@pytest.mark.parametrize("invalid", ["scale", "channels", "dimensions"])
def test_every_fix_checks_native_shape_and_channels(tmp_path, monkeypatch, invalid):
    from localsr.core.image_io import ImageManager

    server = WorkerServer()
    adapter = _Adapter()
    inspect = adapter.inspect

    def inspect_stage(path):
        if "1-restore" not in path:
            return inspect(path)
        return NormalizedModelInfo(
            "Test",
            2 if invalid == "scale" else 1,
            1 if invalid == "channels" else 3,
            3,
            True,
            False,
            1,
            1,
            path,
            [],
        )

    adapter.inspect = inspect_stage
    server.model_adapter = adapter
    engine = _PipelineEngine()
    server.engine = engine
    process = engine.process_image

    def process_stage(**kwargs):
        writer = process(**kwargs)
        if invalid == "dimensions" and "1-restore" in kwargs["model_path"]:
            writer.array = np.zeros((3, 1, 1), dtype=np.uint8)
        return writer

    engine.process_image = process_stage
    data = _job_data(tmp_path)
    data["stages"] = [
        _stage("restore", str(i), str(tmp_path / f"{i}-restore.pth")) for i in range(3)
    ] + data["stages"][1:]
    monkeypatch.setattr(ImageManager, "load", lambda *_: {"tensor": torch.zeros((3, 4, 4))})
    with pytest.raises(RuntimeError, match=r"restore stage \(1\) failed"):
        server._run_job("pipeline-job", data)
    assert all(writer.cleaned for writer in engine.writers)


@pytest.mark.parametrize(
    "stages",
    [
        [_stage("restore", "a", "a", fix=["noise"]), _stage("upscale", "b", "b")],
        [_stage("restore", "a", "a", fix=3), _stage("upscale", "b", "b")],
        [_stage("restore", "a", "a"), _stage("upscale", "b", "b", fix="noise")],
        [
            _stage("upscale", "b", "b"),
            _stage("face_restore", "f", "f", execution="fused-with-upscale", fix="blur"),
        ],
    ],
)
def test_a_fix_kind_is_text_and_belongs_to_a_restoration_stage(stages):
    with pytest.raises(ValueError, match="fix"):
        parse_pipeline_stages(stages)


def test_explicit_roles_require_exactly_one_primary():
    with pytest.raises(ValueError, match="primary"):
        parse_pipeline_stages(
            [_stage("restore", "a", "a", role="fix"), _stage("upscale", "b", "b", role="fix")]
        )


def test_restore_still_fails_when_its_own_model_is_unstable_after_a_fix(tmp_path, monkeypatch):
    # Only the fixes before the Restore model are optional; the model is the job.
    from localsr.core.image_io import ImageManager
    from localsr.core.inference import UnstableRestorationError

    data = _job_data(tmp_path)
    data["model_path"] = str(tmp_path / "main-restore.pth")
    data["output_scale"] = 1
    data["stages"] = [
        _stage("restore", "blur", str(tmp_path / "restore.pth"), role="fix", fix="blur"),
        _stage("restore", "noise", data["model_path"], role="primary", fix="noise"),
    ]
    server = WorkerServer()
    server.model_adapter = _Adapter()
    server.engine = _PipelineEngine(
        fail_path=data["model_path"], failure=UnstableRestorationError("unstable values")
    )
    monkeypatch.setattr(ImageManager, "load", lambda *_: {"tensor": torch.zeros((3, 4, 4))})
    monkeypatch.setattr(ImageManager, "save", lambda *_a, **_kw: pytest.fail("saved output"))
    with pytest.raises(RuntimeError, match=r"restore stage \(noise\) failed"):
        server._run_job("pipeline-job", data)
    assert server.engine.calls == [str(tmp_path / "restore.pth"), data["model_path"]]


def test_restore_primary_keeps_the_job_halo_while_its_fixes_use_their_own(tmp_path, monkeypatch):
    # The halo setting belongs to the main model (Restore sets it when the model is
    # chosen); only the optional fixes before it raise their overlap.
    from localsr.core.image_io import ImageManager

    data = _job_data(tmp_path)
    data["model_path"] = str(tmp_path / "main-restore.pth")
    data["output_scale"] = 1
    data["stages"] = [
        _stage(
            "restore", "nafnet_gopro_deblur", str(tmp_path / "restore.pth"), role="fix", fix="blur"
        ),
        _stage("restore", "nafnet_sidd_width64", data["model_path"], role="primary", fix="noise"),
    ]
    server = WorkerServer()
    server.model_adapter = _Adapter()
    server.engine = _PipelineEngine()
    monkeypatch.setattr(ImageManager, "load", lambda *_: {"tensor": torch.zeros((3, 4, 4))})
    monkeypatch.setattr(ImageManager, "save", lambda *_a, **_kw: None)
    server._run_job("pipeline-job", data)
    assert server.engine.halos == [32, 8]


@pytest.mark.parametrize("restoring", [False, True])
def test_multi_fix_final_write_preserves_alpha_and_cleans_real_scratch(
    tmp_path, monkeypatch, restoring
):
    from PIL import Image

    from localsr.core.output_writer import OutputWriter

    source = Image.new("RGBA", (7, 5), (32, 64, 96, 120))
    source.putpixel((0, 0), (32, 64, 96, 0))
    source.save(tmp_path / "input.png")
    data = _job_data(tmp_path)
    data["stages"] = [
        _stage("restore", str(i), str(tmp_path / f"{i}-restore.pth")) for i in range(3)
    ] + data["stages"][1:]
    if restoring:
        data["model_path"] = str(tmp_path / "main-restore.pth")
        data["output_scale"] = 1
        data["stages"][-1] = _stage("restore", "main", data["model_path"], role="primary")
    server = WorkerServer()
    server.model_adapter = _Adapter()
    server.engine = _PipelineEngine()
    writers = []

    def process(**kwargs):
        tensor = kwargs["img_data"]["tensor"]
        scale = kwargs["model_info"].scale
        writer = OutputWriter(
            (3, tensor.shape[1] * scale, tensor.shape[2] * scale),
            temporary_directory=data["scratch_directory"],
        )
        writer.mmap[:] = (len(writers) + 1) * 20
        writers.append(writer)
        return writer

    monkeypatch.setattr(server.engine, "process_image", process)
    server._run_job("pipeline-job", data)
    scale = 1 if restoring else 2
    with Image.open(data["output_path"]) as output:
        assert output.size == (7 * scale, 5 * scale)
        np.testing.assert_array_equal(
            np.asarray(output.getchannel("A")),
            np.asarray(source.getchannel("A").resize(output.size, Image.Resampling.LANCZOS)),
        )
        np.testing.assert_array_equal(
            np.asarray(output)[:, :, :3], np.full((5 * scale, 7 * scale, 3), 80, dtype=np.uint8)
        )
    assert all(writer.mmap is None for writer in writers)
    assert not list((tmp_path / "scratch").glob("localsr-output-*.dat"))
