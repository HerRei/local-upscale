"""The `repair` step (Dust & scratches): registry entry, dev gate, mask requirement, handler."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from localsr.core.model_adapter import NormalizedModelInfo
from localsr.worker.server import WorkerServer

DEV_STEPS = "LOCALSR_DEV_STEPS"
WIDTH, HEIGHT = 64, 48


@pytest.fixture
def dev_steps(monkeypatch):
    monkeypatch.setenv(DEV_STEPS, "1")


class _Writer:
    def __init__(self, array: np.ndarray) -> None:
        self.array = array
        self.shape = array.shape

    def get_array(self) -> np.ndarray:
        return self.array

    def cleanup(self) -> None:
        pass


class _NearestUpscaler:
    def process_image(self, **kwargs):
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


def _gradient() -> np.ndarray:
    x = np.linspace(80, 150, WIDTH, dtype=np.float32)[None, :, None]
    y = np.linspace(0, 20, HEIGHT, dtype=np.float32)[:, None, None]
    return np.broadcast_to(x + y + np.array([0, 5, 10]), (HEIGHT, WIDTH, 3)).astype(np.uint8)


def _speck(picture: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The picture with a black 5 x 5 speck, and a mask a little larger than the speck."""
    marked = picture.copy()
    marked[20:25, 30:35] = 0
    mask = np.zeros((HEIGHT, WIDTH), dtype=np.uint8)
    mask[19:26, 29:36] = 255
    return marked, mask


def _repair(mask_path: Path) -> dict:
    return {"kind": "repair", "step": "repair", "mask_path": str(mask_path)}


def _run(
    tmp_path: Path, name: str, source: Path, stages: list[dict], *, scale: int = 1, depth: int = 8
) -> Path:
    data = {
        "job_id": name,
        "image_path": str(source),
        "model_path": str(tmp_path / "upscale.pth") if scale > 1 else "",
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
        "output_scale": scale,
        "preview_enabled": False,
        "stages": stages,
        **({"output_bit_depth": 16} if depth == 16 else {}),
    }
    server = WorkerServer()
    server.model_adapter = _Adapter()
    server.engine = _NearestUpscaler()
    server._run_job(name, data)
    return Path(data["output_path"])


def _write(tmp_path: Path, name: str, array: np.ndarray) -> Path:
    path = tmp_path / name
    Image.fromarray(array).save(path)
    return path


def _read(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB")).astype(np.int16)


def test_repair_is_a_photo_cleanup_step_that_needs_a_mask():
    from localsr.core.steps import PHASES, STEPS_BY_ID

    repair = STEPS_BY_ID["repair"]
    assert repair.media == ("image",)
    assert repair.phase == "cleanup"
    assert not repair.needs_model
    assert repair.supports_mask and repair.requires_mask
    assert repair.in_image_pipeline and not repair.is_fix_stage
    assert repair.required_feature == "image_repair"
    assert repair.dev_only
    assert PHASES.index(repair.phase) < PHASES.index("restore")
    assert (repair.phase, repair.order) < (
        STEPS_BY_ID["remove_background"].phase,
        STEPS_BY_ID["remove_background"].order,
    )
    assert [s.id for s in STEPS_BY_ID.values() if s.requires_mask] == ["repair"]


def test_the_engine_lists_the_repair_feature_only_with_the_dev_flag(monkeypatch):
    from localsr.worker.server import _engine_features

    monkeypatch.delenv(DEV_STEPS, raising=False)
    assert "image_repair" not in _engine_features([])
    monkeypatch.setenv(DEV_STEPS, "1")
    assert "image_repair" in _engine_features([])


@pytest.mark.usefixtures("dev_steps")
def test_a_stage_without_a_mask_is_refused():
    from localsr.core.pipeline import resolve_image_pipeline

    with pytest.raises(ValueError, match="The repair stage needs a mask."):
        resolve_image_pipeline(
            raw_stages=[{"kind": "repair", "step": "repair"}], model_path="", output_scale=1
        )


def test_the_worker_refuses_repair_without_the_dev_flag(monkeypatch, tmp_path):
    from localsr.core.pipeline import resolve_image_pipeline

    monkeypatch.delenv(DEV_STEPS, raising=False)
    with pytest.raises(ValueError, match="LOCALSR_DEV_STEPS"):
        resolve_image_pipeline(
            raw_stages=[_repair(tmp_path / "m.png")], model_path="", output_scale=1
        )


@pytest.mark.usefixtures("dev_steps")
def test_a_video_job_refuses_repair(tmp_path):
    from localsr.core.pipeline import resolve_image_pipeline

    with pytest.raises(ValueError, match="does not run on video"):
        resolve_image_pipeline(
            raw_stages=[_repair(tmp_path / "m.png")], model_path="", output_scale=1, media="video"
        )


@pytest.mark.usefixtures("dev_steps")
def test_a_painted_speck_is_filled_from_its_surroundings(tmp_path):
    clean = _gradient()
    marked, mask = _speck(clean)
    source = _write(tmp_path, "input.png", marked)
    mask_path = _write(tmp_path, "mask.png", mask)

    output = _read(_run(tmp_path, "speck", source, [_repair(mask_path)]))

    painted = mask > 0
    assert np.abs(output[painted] - clean.astype(np.int16)[painted]).max() <= 4


@pytest.mark.usefixtures("dev_steps")
def test_pixels_outside_the_mask_do_not_change(tmp_path):
    marked, mask = _speck(_gradient())
    # A second mark the mask does not cover stays too.
    marked[5:8, 5:8] = 255
    source = _write(tmp_path, "input.png", marked)
    mask_path = _write(tmp_path, "mask.png", mask)

    output = _read(_run(tmp_path, "outside", source, [_repair(mask_path)]))

    outside = mask == 0
    np.testing.assert_array_equal(output[outside], marked.astype(np.int16)[outside])
    assert not np.array_equal(output[mask > 0], marked.astype(np.int16)[mask > 0])


@pytest.mark.usefixtures("dev_steps")
def test_an_empty_mask_changes_nothing(tmp_path):
    marked, _ = _speck(_gradient())
    source = _write(tmp_path, "input.png", marked)
    mask_path = _write(tmp_path, "mask.png", np.zeros((HEIGHT, WIDTH), dtype=np.uint8))

    output = _read(_run(tmp_path, "empty", source, [_repair(mask_path)]))

    np.testing.assert_array_equal(output, marked.astype(np.int16))


@pytest.mark.usefixtures("dev_steps")
def test_16_bits_and_alpha_are_kept(tmp_path):
    clean = _gradient().astype(np.uint16) * 257
    marked, mask = _speck(_gradient())
    marked16 = marked.astype(np.uint16) * 257
    alpha = np.linspace(20000, 60000, WIDTH, dtype=np.uint16)[None, :].repeat(HEIGHT, axis=0)
    source = tmp_path / "input.png"
    cv2.imwrite(str(source), np.dstack([marked16[..., ::-1], alpha]))
    mask_path = _write(tmp_path, "mask.png", mask)

    path = _run(tmp_path, "sixteen", source, [_repair(mask_path)], depth=16)

    decoded = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    assert decoded.dtype == np.uint16 and decoded.shape == (HEIGHT, WIDTH, 4)
    np.testing.assert_array_equal(decoded[..., 3], alpha)
    rgb = decoded[..., 2::-1].astype(np.int32)
    painted = mask > 0
    assert np.abs(rgb[painted] - clean.astype(np.int32)[painted]).max() <= 4 * 257
    np.testing.assert_array_equal(rgb[~painted], marked16.astype(np.int32)[~painted])


@pytest.mark.usefixtures("dev_steps")
def test_the_mask_is_fitted_to_the_picture(tmp_path):
    marked, mask = _speck(_gradient())
    source = _write(tmp_path, "input.png", marked)
    full = _run(tmp_path, "whole", source, [_repair(_write(tmp_path, "full-mask.png", mask))])
    half = cv2.resize(mask, (WIDTH // 2, HEIGHT // 2), interpolation=cv2.INTER_NEAREST)
    half_path = _write(tmp_path, "half.png", half)

    fitted = _read(_run(tmp_path, "fitted", source, [_repair(half_path)]))

    clean = _gradient().astype(np.int16)
    # The place the speck was is repaired, though the mask came at half the size.
    assert np.abs(fitted[21:24, 31:34] - clean[21:24, 31:34]).max() <= 4
    assert _read(full).shape == fitted.shape


@pytest.mark.usefixtures("dev_steps")
def test_repair_runs_before_the_fixes_and_alone_at_same_size(tmp_path):
    from localsr.core.pipeline import parse_pipeline_stages

    stages = parse_pipeline_stages(
        [
            _repair(tmp_path / "m.png"),
            {
                "kind": "restore",
                "fix": "noise",
                "role": "fix",
                "model_id": "noise",
                "model_path": "/m/noise",
            },
            {"kind": "upscale", "role": "primary", "model_id": "up", "model_path": "/m/up"},
        ]
    )
    assert [stage.step_id for stage in stages] == ["repair", "noise", "upscale"]

    marked, mask = _speck(_gradient())
    source = _write(tmp_path, "input.png", marked)
    output = _run(tmp_path, "alone", source, [_repair(_write(tmp_path, "mask.png", mask))])
    assert output.is_file()
