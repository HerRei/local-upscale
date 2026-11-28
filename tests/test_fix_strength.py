"""The `strength` of a fix: a mix of the fix's result with its input, in every engine path."""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from PIL import Image
from test_patch_preview import Harness, _fix
from test_video_chain import (
    _DeterministicTraceEngine,
    _FakeClock,
    _make_clip,
    _make_info,
)

import localsr.core.steps as step_registry
import localsr.core.video_pipeline as video_pipeline
from localsr.core.inference import UnstableRestorationError
from localsr.core.video_io import decode_timed_frames
from localsr.core.video_pipeline import VideoFixStage, VideoJobConfig, run_video_job
from localsr.worker.server import _engine_features


@pytest.fixture
def harness(monkeypatch, tmp_path):
    return Harness(monkeypatch, tmp_path)


def _job(harness: Harness, name: str, stages: list[dict], **fields) -> Path:
    output = harness.tmp / name
    job = {
        "job_id": name,
        "image_path": harness.image,
        "model_path": "",
        "output_path": str(output),
        "scratch_directory": str(harness.tmp / "scratch"),
        "output_format": "png",
        "device": "cpu",
        "precision": "fp32",
        "tile_size": 256,
        "halo": 16,
        "jpeg_quality": 95,
        "preserve_metadata": False,
        "safe_memory": True,
        "output_scale": 1,
        "preview_enabled": False,
        "neural_engine": False,
        "stages": stages,
    }
    job.update(fields)
    harness.server._run_job(name, job)
    return output


def _at(stage: dict, strength: float | None) -> dict:
    return stage if strength is None else {**stage, "params": {"strength": strength}}


def _rgb(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB")).astype(np.float32)


def test_strength_is_declared_on_blur_jpeg_and_noise_only():
    for step_id in ("blur", "jpeg", "noise"):
        parameter = step_registry.parameter_for(step_id, "strength")
        assert (parameter.kind, parameter.minimum, parameter.maximum, parameter.default) == (
            "number",
            0.0,
            1.0,
            1.0,
        )
    assert step_registry.STEPS_BY_ID["colour"].parameters == ()


def test_the_engine_lists_fix_strength():
    assert "fix_strength" in _engine_features([])


def test_full_strength_is_byte_identical(harness):
    noise = _fix(harness.noise)
    plain = _job(harness, "plain.png", [noise])
    full = _job(harness, "full.png", [_at(noise, 1.0)])
    assert plain.read_bytes() == full.read_bytes()


def test_a_fix_is_mixed_with_its_input(harness):
    noise = _fix(harness.noise)
    whole = _rgb(_job(harness, "whole.png", [noise]))
    source = _rgb(Path(harness.image))
    assert np.abs(whole - source).max() > 20
    light = _rgb(_job(harness, "light.png", [_at(noise, 0.4)]))
    assert np.abs(light - (source + 0.4 * (whole - source))).max() <= 1.0
    nothing = _rgb(_job(harness, "none.png", [_at(noise, 0.0)]))
    assert np.abs(nothing - source).max() <= 1.0


def test_strength_keeps_16_bits(harness):
    rng = np.random.default_rng(3)
    base = np.linspace(8000, 50000, 120, dtype=np.float32)[None, :, None]
    pixels = base + rng.normal(0, 900, (100, 120, 3)).astype(np.float32)
    wide = np.clip(pixels, 0, 65535).astype(np.uint16)
    source = harness.tmp / "wide.png"
    cv2.imwrite(str(source), cv2.cvtColor(wide, cv2.COLOR_RGB2BGR))
    harness.image = str(source)
    noise = _fix(harness.noise)

    def run(name: str, stage: dict) -> np.ndarray:
        path = _job(harness, name, [stage], output_bit_depth=16)
        decoded = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        assert decoded.dtype == np.uint16
        return cv2.cvtColor(decoded, cv2.COLOR_BGR2RGB).astype(np.float32)

    whole, light = run("whole16.png", noise), run("light16.png", _at(noise, 0.4))
    expected = wide.astype(np.float32) + 0.4 * (whole - wide.astype(np.float32))
    assert np.abs(light - expected).max() <= 2.0
    # An 8-bit mix could not land between these: the levels here are 257 times finer.
    assert len(np.unique(light % 257)) > 8


def test_restores_own_model_takes_the_strength(harness):
    primary = {
        "kind": "restore",
        "role": "primary",
        "fix": "noise",
        "model_id": "test-noise",
        "model_path": harness.noise,
    }
    whole = _rgb(_job(harness, "p-whole.png", [primary], model_path=harness.noise))
    source = _rgb(Path(harness.image))
    light = _rgb(_job(harness, "p-light.png", [_at(primary, 0.4)], model_path=harness.noise))
    assert np.abs(whole - source).max() > 20
    assert np.abs(light - (source + 0.4 * (whole - source))).max() <= 1.0


def test_a_skipped_fix_is_not_mixed(harness, monkeypatch):
    def unstable(*_args, **_kwargs):
        raise UnstableRestorationError("synthetic instability")

    monkeypatch.setattr(harness.engine, "process_image", unstable)
    source = _rgb(Path(harness.image))
    for strength in (None, 0.4):
        output = _job(harness, f"skip-{strength}.png", [_at(_fix(harness.noise), strength)])
        assert np.array_equal(_rgb(output), source)
        assert any("was skipped" in str(m.to_json()) for m in harness.sent)


def _video_run(tmp: Path, name: str, **config) -> tuple[list[np.ndarray], list[np.ndarray]]:
    source = _make_clip(tmp / f"{name}.mp4", frames=3)
    clock = _FakeClock()
    clock_module = SimpleNamespace(perf_counter=clock.perf_counter)
    original = video_pipeline.time
    video_pipeline.time = clock_module
    try:
        enhanced: list[np.ndarray] = []
        run_video_job(
            VideoJobConfig(
                video_path=str(source),
                output_video_path=str(tmp / f"{name}.mkv"),
                device_str="cpu",
                precision_str="fp32",
                tile_size=64,
                halo=0,
                safe_memory=False,
                container="mkv",
                video_codec="ffv1",
                output_scale=1,
                **config,
            ),
            _DeterministicTraceEngine(clock),  # type: ignore[arg-type]
            threading.Event(),
            enhanced_frame_cb=lambda _i, _t, rgb: enhanced.append(rgb.copy()),
        )
    finally:
        video_pipeline.time = original
    return [f.rgb for f in decode_timed_frames(str(source))], enhanced


def _fix_config(strength: float | None) -> dict:
    params = {} if strength is None else {"strength": strength}
    return {
        "model_path": "",
        "model_info": None,
        "preprocess_stages": (
            VideoFixStage("fix_noise.pth", _make_info(1), fix="noise", params=params),
        ),
    }


def test_video_frames_are_mixed(tmp_path):
    source, whole = _video_run(tmp_path, "whole", **_fix_config(None))
    _, light = _video_run(tmp_path, "light", **_fix_config(0.4))
    _, none = _video_run(tmp_path, "none", **_fix_config(0.0))
    assert len(whole) == len(light) == len(none) == len(source) == 3
    for original, full, mixed, same in zip(source, whole, light, none, strict=True):
        original = original.astype(np.float32)
        assert np.abs(full.astype(np.float32) - original).max() >= 8
        expected = original + 0.4 * (full.astype(np.float32) - original)
        assert np.abs(mixed.astype(np.float32) - expected).max() <= 1.0
        assert np.abs(same.astype(np.float32) - original).max() <= 1.0


def test_the_video_restore_primary_is_mixed(tmp_path):
    def primary(strength: float | None) -> dict:
        config = {
            "model_path": "primary_noise.pth",
            "model_info": _make_info(1),
            "preprocess_stages": (),
        }
        if strength is not None:
            config["primary_strength"] = strength
        return config

    source, whole = _video_run(tmp_path, "pwhole", **primary(None))
    _, light = _video_run(tmp_path, "plight", **primary(0.4))
    for original, full, mixed in zip(source, whole, light, strict=True):
        original = original.astype(np.float32)
        assert np.abs(full.astype(np.float32) - original).max() >= 8
        expected = original + 0.4 * (full.astype(np.float32) - original)
        assert np.abs(mixed.astype(np.float32) - expected).max() <= 1.0


def test_a_skipped_video_fix_is_not_mixed(tmp_path):
    config = _fix_config(0.4)
    config["preprocess_stages"] = (
        VideoFixStage("unstable_noise.pth", _make_info(1), fix="noise", params={"strength": 0.4}),
    )
    source, result = _video_run(tmp_path, "unstable", **config)
    for original, out in zip(source, result, strict=True):
        assert np.array_equal(original, out)
