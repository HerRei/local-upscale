"""RIFE frame interpolation engine: checkpoint loading, padding, scale and devices.

The real-weights checks run only when LOCALSR_RIFE_CHECKPOINT names a verified
``flownet.pkl`` (RIFE 4.26); the rest use randomly initialised weights.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest
import torch

from localsr.video_models.rife.engine import RifeSynthesizer, flow_scale_for, torch_device_for
from localsr.video_models.rife.ifnet import IFNet


def save_checkpoint(path: Path, model: IFNet | None = None, *, extra: bool = True) -> Path:
    """Write a state dict the way Practical-RIFE does: a ``module.`` prefix and teacher weights."""
    model = model or IFNet()
    state = {f"module.{name}": value for name, value in model.state_dict().items()}
    if extra:
        state["module.teacher.conv0.0.0.weight"] = torch.zeros(1)
    torch.save(state, path)
    return path


def test_checkpoints_load_without_their_training_only_weights(tmp_path):
    reference = IFNet()
    synthesizer = RifeSynthesizer(save_checkpoint(tmp_path / "flownet.pkl", reference), "cpu")
    for name, value in reference.state_dict().items():
        torch.testing.assert_close(synthesizer.model.state_dict()[name], value)


def test_a_checkpoint_missing_weights_is_refused(tmp_path):
    state = {f"module.{name}": value for name, value in IFNet().state_dict().items()}
    state.pop("module.block4.lastconv.0.weight")
    path = tmp_path / "broken.pkl"
    torch.save(state, path)
    with pytest.raises(ValueError, match="RIFE checkpoint"):
        RifeSynthesizer(path, "cpu")


def test_checkpoints_are_read_as_tensors_only(tmp_path):
    path = tmp_path / "pickle.pkl"
    torch.save({"module.block0": object()}, path)
    with pytest.raises(Exception):  # noqa: B017 - weights_only refuses arbitrary objects
        RifeSynthesizer(path, "cpu")


@pytest.mark.parametrize(("height", "width"), [(50, 70), (64, 128), (33, 97)])
def test_synthesized_frames_match_the_input_size_for_every_timestep(tmp_path, height, width):
    synthesizer = RifeSynthesizer(save_checkpoint(tmp_path / "flownet.pkl"), "cpu")
    generator = np.random.default_rng(0)
    first = generator.integers(0, 256, (height, width, 3), dtype=np.uint8)
    second = generator.integers(0, 256, (height, width, 3), dtype=np.uint8)
    frames = synthesizer.synthesize(first, second, [0.25, 0.5, 0.75])
    assert len(frames) == 3
    for frame in frames:
        assert frame.shape == (height, width, 3)
        assert frame.dtype == np.uint8
        assert frame.flags["C_CONTIGUOUS"]


def test_cancellation_is_checked_between_timesteps(tmp_path):
    import threading

    synthesizer = RifeSynthesizer(save_checkpoint(tmp_path / "flownet.pkl"), "cpu")
    cancel = threading.Event()
    cancel.set()
    frame = np.zeros((32, 32, 3), dtype=np.uint8)
    with pytest.raises(InterruptedError):
        synthesizer.synthesize(frame, frame, [0.5], cancel)


@pytest.mark.parametrize(
    ("height", "width", "scale"),
    [
        (1080, 1920, 1.0),
        (1440, 2560, 1.0),
        (2160, 3840, 0.5),
        (3840, 2160, 0.5),
        (4320, 7680, 0.25),
    ],
)
def test_flow_runs_at_a_lower_scale_for_4k_and_8k(height, width, scale):
    # Practical-RIFE recommends --scale=0.5 for 4K.
    assert flow_scale_for(height, width) == scale


@pytest.mark.parametrize(
    ("device", "expected"),
    [
        ("cpu", "cpu"),
        ("mps", "mps"),
        ("cuda:0", "cuda:0"),
        ("directml:0", "cpu"),
        ("openvino:GPU.0", "cpu"),
        ("openvino:NPU", "cpu"),
    ],
)
def test_backends_without_a_pytorch_route_interpolate_on_the_cpu(device, expected):
    assert str(torch_device_for(device)) == expected


REAL_CHECKPOINT = os.environ.get("LOCALSR_RIFE_CHECKPOINT")


def moving_square(position: int, size: int = 160) -> np.ndarray:
    frame = np.full((size, size, 3), 40, dtype=np.uint8)
    frame[60:100, position : position + 40] = (230, 200, 60)
    return frame


def centroid_x(frame: np.ndarray) -> float:
    mask = frame[..., 0].astype(int) > 135
    return float(np.nonzero(mask)[1].mean())


@pytest.mark.skipif(not REAL_CHECKPOINT, reason="needs LOCALSR_RIFE_CHECKPOINT")
def test_real_weights_place_a_moving_object_along_its_path():
    synthesizer = RifeSynthesizer(Path(str(REAL_CHECKPOINT)), "cpu")
    first, second = moving_square(20), moving_square(84)
    quarter, half = synthesizer.synthesize(first, second, [0.25, 0.5])
    assert centroid_x(half) == pytest.approx(centroid_x(first) + 32, abs=2)
    assert centroid_x(quarter) == pytest.approx(centroid_x(first) + 16, abs=3)
