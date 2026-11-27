"""Step stages that bring a catalog model or set the alpha, and jobs made only of steps.

No shipped step does either yet, so the steps and the model here exist only in the tests,
the way ``test_pipeline.py`` proves model-less steps.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import spandrel.architectures.ESRGAN as E
import torch
from PIL import Image

from localsr.core.model_adapter import NormalizedModelInfo
from localsr.core.steps import StepDefinition, StepParameter
from localsr.worker.server import WorkerServer

ROOT = Path(__file__).resolve().parents[1]

CUTOUT = StepDefinition(
    id="test_cutout",
    media=("image",),
    phase="cleanup",
    order=10,
    needs_model=True,
    in_image_pipeline=True,
    label="Test cut-out",
    model_task="test_cutout",
    output_alpha=True,
)

KEY = StepDefinition(
    id="test_key",
    media=("image",),
    phase="cleanup",
    order=20,
    needs_model=False,
    in_image_pipeline=True,
    label="Test key",
    output_alpha=True,
    parameters=(StepParameter(name="floor", kind="number", default=0.2, minimum=0, maximum=1),),
)


def _register(monkeypatch, *steps: StepDefinition) -> None:
    import localsr.core.pipeline as pipeline_mod
    import localsr.core.steps as steps_mod

    registered = (*steps_mod.STEPS, *steps)
    monkeypatch.setattr(steps_mod, "STEPS", registered)
    monkeypatch.setattr(steps_mod, "STEPS_BY_ID", {step.id: step for step in registered})
    monkeypatch.setattr(pipeline_mod, "STEPS", registered)


def _key_matte(height: int, width: int, floor: float) -> np.ndarray:
    """What the test key step returns: a left-to-right ramp from ``floor`` to 1."""
    return np.broadcast_to(np.linspace(floor, 1.0, width, dtype=np.float32), (height, width))


def _register_key(monkeypatch, phase: str = "cleanup") -> StepDefinition:
    from localsr.core.steps import resolved_step_params
    from localsr.worker import image_job

    step = replace(KEY, phase=phase)
    _register(monkeypatch, step)

    def run_key(_runner, image, stage, _index, _data, _progress, _notes):
        height, width = image["tensor"].shape[-2:]
        floor = float(resolved_step_params(stage.step_id, stage.params)["floor"])
        return _key_matte(int(height), int(width), floor)

    monkeypatch.setitem(image_job.IMAGE_STEP_HANDLERS, step.id, run_key)
    return step


class _Writer:
    def __init__(self, array: np.ndarray) -> None:
        self.array = array
        self.shape = array.shape

    def get_array(self) -> np.ndarray:
        return self.array

    def cleanup(self) -> None:
        pass


class _NearestUpscaler:
    """Stands in for an upscaling model: repeats each pixel, so the output is predictable."""

    def process_image(self, **kwargs):
        scale = kwargs["model_info"].scale
        tensor = kwargs["img_data"]["tensor"].clamp(0, 1)
        if kwargs.get("output_bit_depth") == 16:
            pixels = tensor.mul(65535).round().to(torch.int32).numpy().astype(np.uint16)
        else:
            pixels = tensor.mul(255).round().byte().numpy()
        kwargs["progress_callback"](1, 1, kwargs["tile_size"])
        return _Writer(pixels.repeat(scale, axis=1).repeat(scale, axis=2))

    def release_model(self, path=None) -> None:
        pass

    def release_models_except(self, keep) -> None:
        pass


class _Adapter:
    allow_unverified_checkpoints = False

    def inspect(self, path):
        return NormalizedModelInfo("Test", 2, 3, 3, True, False, 1, 1, path, [])

    def release(self) -> None:
        pass


def _source(tmp_path: Path, width: int = 12, height: int = 8) -> tuple[Path, np.ndarray]:
    x = np.linspace(30, 220, width, dtype=np.float32)[None, :, None]
    y = np.linspace(0, 25, height, dtype=np.float32)[:, None, None]
    rgb = np.broadcast_to(x + y + np.array([0, 9, 18]), (height, width, 3)).astype(np.uint8)
    alpha = np.full((height, width), 200, dtype=np.uint8)
    alpha[0, 0] = 0
    alpha[-1, :4] = 90
    rgba = np.dstack((rgb, alpha))
    path = tmp_path / "input.png"
    Image.fromarray(rgba).save(path)
    return path, rgba


def _job(tmp_path: Path, stages: list[dict], *, output_scale: int, bit_depth: int = 8) -> dict:
    return {
        "job_id": "step-job",
        "image_path": str(tmp_path / "input.png"),
        "model_path": str(tmp_path / "upscale.pth") if output_scale > 1 else "",
        "output_path": str(tmp_path / "output.png"),
        "scratch_directory": str(tmp_path / "scratch"),
        "output_format": "png",
        "output_bit_depth": bit_depth,
        "device": "cpu",
        "precision": "fp32",
        "tile_size": 64,
        "halo": 8,
        "jpeg_quality": 98,
        "preserve_metadata": True,
        "safe_memory": True,
        "output_scale": output_scale,
        "preview_enabled": False,
        "stages": stages,
    }


def _upscale(tmp_path: Path) -> dict:
    return {
        "kind": "upscale",
        "role": "primary",
        "model_id": "upscale",
        "model_path": str(tmp_path / "upscale.pth"),
    }


def _run_fake(tmp_path: Path, data: dict) -> None:
    server = WorkerServer()
    server.model_adapter = _Adapter()
    server.engine = _NearestUpscaler()
    server._run_job(data["job_id"], data)


def _tiny_matte_checkpoint(path: Path) -> str:
    """A 1x ESRGAN with one output channel, as small as Spandrel loads."""
    torch.manual_seed(3)
    weights = E.ESRGAN(in_nc=3, out_nc=1, num_filters=8, num_blocks=1, scale=1).state_dict()
    # Untrained, it answers just below zero, which the descriptor clamps away; a steeper,
    # raised last layer spreads its answer over 0..1.
    with torch.no_grad():
        weights["model.4.weight"].mul_(8)
        weights["model.4.bias"].add_(0.5)
    torch.save(weights, path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _register_cutout(monkeypatch, tmp_path: Path, seen: list) -> Path:
    """The cut-out step, its handler and a catalog that verifies its tiny checkpoint."""
    from localsr.core.model_catalog import CATALOG_BY_FILENAME, CATALOG_BY_ID
    from localsr.worker import image_job

    _register(monkeypatch, CUTOUT)
    checkpoint = tmp_path / "test-cutout.pth"
    digest = _tiny_matte_checkpoint(checkpoint)
    entry = SimpleNamespace(
        model_id="test_cutout_model",
        name="Test cut-out model",
        display_name="Test cut-out model",
        filename=checkpoint.name,
        size_bytes=checkpoint.stat().st_size,
        sha256=digest,
        recommended_halo=0,
        time_factor=1.0,
        architecture="Test",
    )
    monkeypatch.setitem(CATALOG_BY_FILENAME, checkpoint.name, entry)
    monkeypatch.setitem(CATALOG_BY_ID, entry.model_id, entry)

    def run_cutout(runner, image, _stage, _index, _data, _progress, _notes, model):
        seen.append((model, runner.engine.active_model is model.module))
        batch = image["tensor"][None].to(model.device, model.precision)
        with torch.inference_mode():
            return model.module(batch)[0, 0].float()

    monkeypatch.setitem(image_job.IMAGE_MODEL_STEP_HANDLERS, CUTOUT.id, run_cutout)
    return checkpoint


def _cutout_stage(checkpoint: Path) -> dict:
    return {
        "kind": CUTOUT.id,
        "step": CUTOUT.id,
        "model_id": "test_cutout_model",
        "model_path": str(checkpoint),
    }


def _real_server():
    from localsr.core.inference import InferenceEngine
    from localsr.core.model_adapter import ModelAdapter

    adapter = ModelAdapter(allow_unverified_checkpoints=False)
    engine = InferenceEngine(adapter)
    server = WorkerServer()
    server.model_adapter = adapter
    server.engine = engine
    return server, engine


def test_a_step_with_a_catalog_model_loads_it_through_the_engine_and_sets_the_alpha(
    tmp_path, monkeypatch
):
    """Restore with nothing but the step: no fix, no main model, output at 1x."""
    from spandrel import ModelLoader

    _, rgba = _source(tmp_path)
    seen: list = []
    checkpoint = _register_cutout(monkeypatch, tmp_path, seen)
    data = _job(tmp_path, [_cutout_stage(checkpoint)], output_scale=1)
    server, engine = _real_server()

    server._run_job("step-job", data)

    assert len(seen) == 1
    model, active = seen[0]
    assert active
    assert model.path == str(checkpoint)
    assert model.info.scale == 1 and model.info.out_channels == 1
    # Released after its stage, as a fix's model is.
    assert str(checkpoint) not in engine._loaded_models

    reference = ModelLoader().load_from_file(str(checkpoint)).eval()
    source = torch.from_numpy(rgba[..., :3].astype(np.float32) / 255.0).permute(2, 0, 1)
    with torch.inference_mode():
        matte = reference(source[None])[0, 0].numpy()
    expected = np.rint(matte * (rgba[..., 3].astype(np.float32) / 255.0) * 255.0)
    with Image.open(data["output_path"]) as output:
        assert output.mode == "RGBA" and output.size == (12, 8)
        pixels = np.asarray(output)
    np.testing.assert_array_equal(pixels[..., :3], rgba[..., :3])
    assert np.abs(pixels[..., 3].astype(np.int16) - expected.astype(np.int16)).max() <= 1
    assert pixels[0, 0, 3] == 0
    assert len(np.unique(pixels[..., 3])) > 4


def test_a_step_model_that_fails_its_catalog_check_is_never_loaded(tmp_path, monkeypatch):
    from localsr.core.pipeline import PipelineStageError

    _source(tmp_path)
    seen: list = []
    checkpoint = _register_cutout(monkeypatch, tmp_path, seen)
    tampered = bytearray(checkpoint.read_bytes())
    tampered[len(tampered) // 2] ^= 0xFF
    checkpoint.write_bytes(bytes(tampered))
    server, engine = _real_server()
    data = _job(tmp_path, [_cutout_stage(checkpoint)], output_scale=1)

    with pytest.raises(PipelineStageError, match="SHA-256"):
        server._run_job("step-job", data)
    assert seen == []
    assert not Path(data["output_path"]).exists()
    assert engine._loaded_models == {}


@pytest.mark.parametrize("bit_depth", [8, 16])
def test_an_alpha_step_alone_sets_the_alpha_from_its_matte_and_keeps_the_colour(
    tmp_path, monkeypatch, bit_depth
):
    _register_key(monkeypatch)
    _, rgba = _source(tmp_path)
    data = _job(
        tmp_path,
        [{"kind": KEY.id, "step": KEY.id, "params": {"floor": 0.4}}],
        output_scale=1,
        bit_depth=bit_depth,
    )
    _run_fake(tmp_path, data)

    matte = _key_matte(8, 12, 0.4) * (rgba[..., 3].astype(np.float32) / 255.0)
    peak = 65535.0 if bit_depth == 16 else 255.0
    decoded = _decoded(Path(data["output_path"]), bit_depth)
    assert decoded.shape == (8, 12, 4)
    rgb = decoded[..., :3].astype(np.float64) / peak
    np.testing.assert_allclose(rgb, rgba[..., :3] / 255.0, atol=0.5 / 255.0)
    assert np.abs(decoded[..., 3].astype(np.int64) - np.rint(matte * peak)).max() <= 1


def _decoded(path: Path, bit_depth: int) -> np.ndarray:
    if bit_depth == 8:
        with Image.open(path) as image:
            return np.asarray(image)
    from localsr.core.image_io_16bit import read_png_16

    pixels, colour = read_png_16(path)
    assert colour == 6
    return pixels


def test_an_alpha_step_before_the_upscaler_scales_its_alpha_with_the_image(tmp_path, monkeypatch):
    _register_key(monkeypatch, phase="cleanup")
    _, rgba = _source(tmp_path)
    data = _job(
        tmp_path,
        [{"kind": KEY.id, "step": KEY.id}, _upscale(tmp_path)],
        output_scale=2,
    )
    _run_fake(tmp_path, data)

    matte = _key_matte(8, 12, 0.2) * (rgba[..., 3].astype(np.float32) / 255.0)
    alpha = Image.fromarray(np.rint(matte * 255.0).astype(np.uint8))
    expected = np.asarray(alpha.resize((24, 16), Image.Resampling.LANCZOS))
    with Image.open(data["output_path"]) as output:
        assert output.size == (24, 16) and output.mode == "RGBA"
        pixels = np.asarray(output)
    np.testing.assert_array_equal(pixels[..., 3], expected)
    np.testing.assert_array_equal(pixels[..., :3], rgba[..., :3].repeat(2, 0).repeat(2, 1))


def test_an_alpha_step_after_the_upscaler_multiplies_the_scaled_source_alpha(tmp_path, monkeypatch):
    _register_key(monkeypatch, phase="grade")
    _, rgba = _source(tmp_path)
    data = _job(
        tmp_path,
        [_upscale(tmp_path), {"kind": KEY.id, "step": KEY.id, "params": {"floor": 0.5}}],
        output_scale=2,
    )
    _run_fake(tmp_path, data)

    source_alpha = Image.fromarray(rgba[..., 3].astype(np.float32) / 255.0)
    scaled = np.clip(np.asarray(source_alpha.resize((24, 16), Image.Resampling.LANCZOS)), 0, 1)
    expected = np.rint(_key_matte(16, 24, 0.5) * scaled * 255.0)
    with Image.open(data["output_path"]) as output:
        assert output.size == (24, 16) and output.mode == "RGBA"
        pixels = np.asarray(output)
    assert np.abs(pixels[..., 3].astype(np.int16) - expected.astype(np.int16)).max() <= 1
    np.testing.assert_array_equal(pixels[..., :3], rgba[..., :3].repeat(2, 0).repeat(2, 1))


def test_an_alpha_step_with_a_mask_keeps_the_alpha_outside_it(tmp_path, monkeypatch):
    from localsr.worker import image_job

    step = replace(KEY, supports_mask=True)
    _register(monkeypatch, step)

    monkeypatch.setitem(
        image_job.IMAGE_STEP_HANDLERS,
        step.id,
        lambda _r, image, _s, _i, _d, _p, _n: np.zeros(image["tensor"].shape[-2:], np.float32),
    )
    _, rgba = _source(tmp_path)
    mask = np.zeros((8, 12), dtype=np.uint8)
    mask[:, 6:] = 255
    Image.fromarray(mask).save(tmp_path / "mask.png")
    data = _job(
        tmp_path,
        [{"kind": step.id, "step": step.id, "mask_path": str(tmp_path / "mask.png")}],
        output_scale=1,
    )
    _run_fake(tmp_path, data)

    with Image.open(data["output_path"]) as output:
        alpha = np.asarray(output)[..., 3]
    np.testing.assert_array_equal(alpha[:, :6], rgba[:, :6, 3])
    assert not alpha[:, 6:].any()


def test_a_matte_of_the_wrong_size_fails_the_stage(tmp_path, monkeypatch):
    from localsr.core.pipeline import PipelineStageError
    from localsr.worker import image_job

    _register(monkeypatch, KEY)
    monkeypatch.setitem(
        image_job.IMAGE_STEP_HANDLERS,
        KEY.id,
        lambda *_args: np.ones((3, 3), np.float32),
    )
    _source(tmp_path)
    data = _job(tmp_path, [{"kind": KEY.id, "step": KEY.id}], output_scale=1)
    with pytest.raises(PipelineStageError, match="matte"):
        _run_fake(tmp_path, data)


def test_step_stages_with_a_model_need_one_and_count_apart_from_the_model_stages(monkeypatch):
    from localsr.core.pipeline import resolve_image_pipeline

    late = replace(CUTOUT, id="test_late_cutout", phase="grade", model_task="test_late")
    _register(monkeypatch, CUTOUT, late)
    cutout = {"step": CUTOUT.id, "model_id": "cut", "model_path": "/models/cut.pth"}
    fixes = [
        {"kind": "restore", "role": "fix", "fix": fix, "model_id": fix, "model_path": f"/{fix}"}
        for fix in ("blur", "noise", "colour")
    ] + [{"kind": "deblock", "role": "fix", "fix": "jpeg", "model_id": "j", "model_path": "/j"}]
    upscale = {"kind": "upscale", "role": "primary", "model_id": "up", "model_path": "/up.pth"}
    face = {
        "kind": "face_restore",
        "model_id": "face",
        "model_path": "/face.pth",
        "execution": "fused-with-upscale",
    }

    full = resolve_image_pipeline(
        raw_stages=[cutout, *fixes, upscale, face],
        model_path="/up.pth",
        output_scale=2,
        face_model_path="/face.pth",
    )
    assert [stage.step_id for stage in full.preprocess_stages][0] == CUTOUT.id
    assert full.preprocess_stages[0].model_path == "/models/cut.pth"

    after = resolve_image_pipeline(
        raw_stages=[upscale, {"step": late.id, "model_id": "late", "model_path": "/late.pth"}],
        model_path="/up.pth",
        output_scale=2,
    )
    assert [stage.step_id for stage in after.postprocess_stages] == [late.id]

    alone = resolve_image_pipeline(raw_stages=[cutout], model_path="", output_scale=1)
    assert alone.primary is None
    assert [(stage.step_id, stage.model_id) for stage in alone.stages] == [(CUTOUT.id, "cut")]

    for raw, message in [
        ([{"step": CUTOUT.id}], "requires a model ID"),
        ([{"step": CUTOUT.id, "model_id": "cut"}], "requires a model ID"),
        ([cutout, cutout, upscale], "only once"),
        ([{**cutout, "role": "primary"}], "primary"),
        ([upscale, {**cutout, "step": late.id, "kind": late.id}, face], "Face restoration"),
    ]:
        with pytest.raises(ValueError, match=message):
            resolve_image_pipeline(
                raw_stages=raw,
                model_path="/up.pth" if upscale in raw else "",
                output_scale=2 if upscale in raw else 1,
                face_model_path="/face.pth",
            )
    with pytest.raises(ValueError, match="does not run on video"):
        resolve_image_pipeline(raw_stages=[cutout], model_path="", output_scale=1, media="video")


def test_the_hosts_model_step_sample_resolves_in_the_worker(monkeypatch):
    from localsr.core.pipeline import resolve_image_pipeline
    from localsr.worker.request_validation import validate_image_job

    _register(monkeypatch, CUTOUT)
    samples = json.loads(
        (ROOT / "tests" / "fixtures" / "job_request_samples.json").read_text(encoding="utf-8")
    )
    data = samples["image_step_with_model_alone"]["data"]
    validate_image_job(data)
    assert data["model_path"] == "" and data["output_scale"] == 1
    pipeline = resolve_image_pipeline(
        raw_stages=data["stages"],
        model_path=data["model_path"],
        output_scale=data["output_scale"],
        face_model_path=data["face_model_path"],
    )
    assert pipeline.primary is None
    (stage,) = pipeline.stages
    assert stage.step_id == CUTOUT.id
    assert stage.model_id == "test_cutout_model"
    assert stage.model_path == "/models/test-cutout.safetensors"


def test_the_registry_keeps_models_and_alpha_on_step_stages_only():
    from localsr.core.steps import PHASES, STEPS, is_model_step_stage, is_step_stage

    primary, face = PHASES.index("primary"), PHASES.index("face")
    for step in STEPS:
        if step.model_task is not None:
            assert step.needs_model and is_model_step_stage(step), step.id
        if step.output_alpha:
            assert is_step_stage(step) and step.media == ("image",), step.id
        if is_step_stage(step):
            assert not primary <= PHASES.index(step.phase) <= face, step.id
    for step in (CUTOUT, KEY):
        assert is_step_stage(step)
    assert is_model_step_stage(CUTOUT) and not is_model_step_stage(KEY)
    assert not is_step_stage(replace(CUTOUT, model_task=None))
