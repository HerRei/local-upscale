"""The model-less `grain` step: registry entry, dev gate, handler, mask and job output."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import torch
from PIL import Image

from localsr.core.model_adapter import NormalizedModelInfo
from localsr.worker.server import WorkerServer

ROOT = Path(__file__).resolve().parents[1]
DEV_STEPS = "LOCALSR_DEV_STEPS"


@pytest.fixture
def dev_steps(monkeypatch):
    monkeypatch.setenv(DEV_STEPS, "1")


@pytest.fixture
def no_dev_steps(monkeypatch):
    monkeypatch.delenv(DEV_STEPS, raising=False)


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

    def __init__(self) -> None:
        self.calls: list[str] = []

    def process_image(self, **kwargs):
        self.calls.append(kwargs["model_path"])
        scale = kwargs["model_info"].scale
        pixels = kwargs["img_data"]["tensor"].clamp(0, 1).mul(255).round().byte().numpy()
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


def _gradient(width: int = 16, height: int = 12) -> np.ndarray:
    x = np.linspace(60, 190, width, dtype=np.float32)[None, :, None]
    y = np.linspace(0, 20, height, dtype=np.float32)[:, None, None]
    return np.broadcast_to(x + y + np.array([0, 5, 10]), (height, width, 3)).astype(np.uint8)


def _job(tmp_path: Path, name: str, stages: list[dict], *, output_scale: int = 2) -> dict:
    source = tmp_path / "input.png"
    if not source.exists():
        Image.fromarray(_gradient()).save(source)
    return {
        "job_id": name,
        "image_path": str(source),
        "model_path": str(tmp_path / "upscale.pth") if output_scale > 1 else "",
        "output_path": str(tmp_path / f"{name}.png"),
        "scratch_directory": str(tmp_path / "scratch"),
        "output_format": "png",
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


def _grain(**params) -> dict:
    return {"kind": "grain", "step": "grain", "params": params}


def _run(tmp_path: Path, name: str, stages: list[dict], **kwargs) -> bytes:
    data = _job(tmp_path, name, stages, **kwargs)
    server = WorkerServer()
    server.model_adapter = _Adapter()
    server.engine = _NearestUpscaler()
    server._run_job(name, data)
    return Path(data["output_path"]).read_bytes()


def _pixels(raw: bytes, tmp_path: Path) -> np.ndarray:
    path = tmp_path / "decoded.png"
    path.write_bytes(raw)
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB")).astype(np.int16)


def test_grain_is_a_dev_only_modelless_image_step_with_bounded_parameters():
    from localsr.core.steps import LIMITS, STEPS_BY_ID, parameter_for

    grain = STEPS_BY_ID["grain"]
    assert grain.media == ("image",)
    assert grain.phase == "grade"
    assert not grain.needs_model
    assert grain.supports_mask
    assert grain.dev_only
    assert grain.in_image_pipeline
    assert not grain.is_fix_stage
    assert grain.required_feature == "image_grain"
    strength = parameter_for("grain", "strength")
    assert (strength.kind, strength.minimum, strength.maximum, strength.default) == (
        "int",
        0,
        100,
        25,
    )
    seed = parameter_for("grain", "seed")
    assert (seed.kind, seed.minimum, seed.maximum, seed.default) == ("int", 0, 2_147_483_647, 0)
    assert LIMITS.max_step_stages == 4
    assert not any(
        step.dev_only for step in STEPS_BY_ID.values() if step.id not in {"grain", "stabilise"}
    )


def test_resolved_step_params_fill_declared_defaults():
    from localsr.core.steps import resolved_step_params

    assert resolved_step_params("grain", {}) == {"strength": 25, "seed": 0}
    assert resolved_step_params("grain", {"seed": 9}) == {"strength": 25, "seed": 9}


def test_engine_lists_the_grain_feature_only_with_the_dev_flag(monkeypatch):
    from localsr.worker.server import _engine_features

    monkeypatch.delenv(DEV_STEPS, raising=False)
    assert "image_grain" not in _engine_features([])
    monkeypatch.setenv(DEV_STEPS, "0")
    assert "image_grain" not in _engine_features([])
    monkeypatch.setenv(DEV_STEPS, "1")
    assert "image_grain" in _engine_features([])


def test_add_grain_is_seeded_monochrome_bounded_and_leaves_its_input_alone():
    from localsr.core.grain import add_grain

    pixels = torch.from_numpy(_gradient().transpose(2, 0, 1).copy()).float().div_(255)
    before = pixels.clone()
    first = add_grain(pixels, strength=40, seed=7)
    again = add_grain(pixels, strength=40, seed=7)
    other = add_grain(pixels, strength=40, seed=8)

    assert torch.equal(pixels, before)
    assert torch.equal(first, again)
    assert not torch.equal(first, other)
    assert torch.equal(add_grain(pixels, strength=0, seed=7), pixels)
    assert float(first.min()) >= 0.0 and float(first.max()) <= 1.0
    delta = first - pixels
    assert torch.allclose(delta[0], delta[1], atol=1e-6)
    assert torch.allclose(delta[0], delta[2], atol=1e-6)
    light = (add_grain(pixels, strength=10, seed=7) - pixels).abs().mean()
    heavy = (add_grain(pixels, strength=100, seed=7) - pixels).abs().mean()
    assert float(heavy) > 5 * float(light) > 0


@pytest.mark.usefixtures("dev_steps")
@pytest.mark.parametrize(
    "params",
    [
        {"strength": 101},
        {"strength": -1},
        {"strength": 12.5},
        {"strength": "50"},
        {"strength": True},
        {"strength": None},
        {"seed": -1},
        {"seed": 2_147_483_648},
        {"grain_size": 2},
    ],
)
def test_grain_parameters_out_of_range_or_type_are_refused(params):
    from localsr.core.pipeline import resolve_image_pipeline

    with pytest.raises(ValueError, match=r"[Pp]arameter .*grain stage"):
        resolve_image_pipeline(
            raw_stages=[_grain(**params)],
            model_path="",
            output_scale=1,
        )


@pytest.mark.usefixtures("no_dev_steps")
def test_the_worker_refuses_grain_without_the_dev_flag():
    from localsr.core.pipeline import resolve_image_pipeline

    with pytest.raises(ValueError, match="LOCALSR_DEV_STEPS"):
        resolve_image_pipeline(raw_stages=[_grain()], model_path="", output_scale=1)


@pytest.mark.usefixtures("dev_steps")
def test_a_step_for_another_media_is_refused():
    from localsr.core.pipeline import resolve_image_pipeline

    with pytest.raises(ValueError, match="video"):
        resolve_image_pipeline(raw_stages=[_grain()], model_path="", output_scale=1, media="video")


@pytest.mark.usefixtures("dev_steps")
def test_a_full_recipe_takes_grain_after_the_face_stage_but_only_once(tmp_path):
    from localsr.core.pipeline import parse_pipeline_stages

    fixes = [
        {"kind": kind, "fix": fix, "role": "fix", "model_id": fix, "model_path": f"/m/{fix}"}
        for kind, fix in (
            ("restore", "blur"),
            ("deblock", "jpeg"),
            ("restore", "noise"),
            ("restore", "colour"),
        )
    ]
    face = {
        "kind": "face_restore",
        "model_id": "face",
        "model_path": "/m/face",
        "execution": "fused-with-upscale",
    }
    stages = parse_pipeline_stages([*fixes, _upscale(tmp_path), face, _grain(strength=30)])
    assert [stage.step_id for stage in stages][-3:] == ["upscale", "face_restore", "grain"]
    assert stages[-1].params == {"strength": 30}
    with pytest.raises(ValueError, match="once"):
        parse_pipeline_stages([_upscale(tmp_path), _grain(), _grain(seed=1)])


@pytest.mark.usefixtures("dev_steps")
def test_grain_with_a_mask_changes_only_the_pixels_inside_it(tmp_path):
    mask = np.zeros((12, 16), dtype=np.uint8)
    mask[:, 8:] = 255
    mask_path = tmp_path / "mask.png"
    Image.fromarray(mask).save(mask_path)
    stage = {**_grain(strength=60, seed=3), "mask_path": str(mask_path)}

    first = _run(tmp_path, "masked", [stage], output_scale=1)
    again = _run(tmp_path, "masked-again", [stage], output_scale=1)

    assert first == again
    source = _gradient().astype(np.int16)
    output = _pixels(first, tmp_path)
    np.testing.assert_array_equal(output[:, :8], source[:, :8])
    changed = np.any(output[:, 8:] != source[:, 8:], axis=-1)
    assert changed.mean() > 0.9


@pytest.mark.usefixtures("dev_steps")
def test_grain_after_the_upscaler_is_deterministic_and_strength_zero_is_the_identity(
    tmp_path, monkeypatch
):
    from localsr.worker import image_job

    plain = _run(tmp_path, "plain", [_upscale(tmp_path)])
    zero = _run(tmp_path, "zero", [_upscale(tmp_path), _grain(strength=0, seed=5)])
    grained = _run(tmp_path, "grained", [_upscale(tmp_path), _grain(strength=40, seed=5)])
    grained_again = _run(
        tmp_path, "grained-again", [_upscale(tmp_path), _grain(strength=40, seed=5)]
    )

    assert zero == plain
    assert grained == grained_again
    assert grained != plain
    assert _pixels(grained, tmp_path).shape == (24, 32, 3)

    def untouched(*_args):
        raise AssertionError("a job without the grain step ran its handler")

    monkeypatch.setitem(image_job.IMAGE_STEP_HANDLERS, "grain", untouched)
    assert _run(tmp_path, "without", [_upscale(tmp_path)]) == plain


@pytest.mark.usefixtures("dev_steps")
def test_one_times_upscale_keeps_the_steps_after_its_primary(tmp_path):
    from localsr.core.pipeline import resolve_image_pipeline

    pipeline = resolve_image_pipeline(
        raw_stages=[_upscale(tmp_path), _grain(strength=20)],
        model_path=str(tmp_path / "upscale.pth"),
        output_scale=1,
    )
    assert pipeline.primary is None
    assert [stage.step_id for stage in pipeline.stages] == ["grain"]


@pytest.mark.usefixtures("dev_steps")
def test_the_hosts_grain_sample_resolves_in_the_worker():
    from localsr.core.pipeline import resolve_image_pipeline
    from localsr.worker.request_validation import validate_image_job

    samples = json.loads(
        (ROOT / "tests" / "fixtures" / "job_request_samples.json").read_text(encoding="utf-8")
    )
    data = samples["image_modelless_grain_step"]["data"]
    validate_image_job(data)
    pipeline = resolve_image_pipeline(
        raw_stages=data["stages"],
        model_path=data["model_path"],
        output_scale=data["output_scale"],
        face_model_path=data["face_model_path"],
    )
    assert [stage.step_id for stage in pipeline.stages] == ["noise", "upscale", "grain"]
    assert [stage.step_id for stage in pipeline.postprocess_stages] == ["grain"]
    grain = pipeline.postprocess_stages[0]
    assert grain.params == {"strength": 40, "seed": 7}
    assert grain.mask_path == "/in/mask.png"
