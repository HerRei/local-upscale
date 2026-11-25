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


def _reference_warp(image, flow):
    """Practical-RIFE's warp (FP32 grid, as vs-rife), with its (N, 2, H, W) grid."""
    import torch.nn.functional as F

    horizontal = (
        torch.linspace(-1.0, 1.0, flow.shape[3])
        .view(1, 1, 1, flow.shape[3])
        .expand(flow.shape[0], -1, flow.shape[2], -1)
    )
    vertical = (
        torch.linspace(-1.0, 1.0, flow.shape[2])
        .view(1, 1, flow.shape[2], 1)
        .expand(flow.shape[0], -1, -1, flow.shape[3])
    )
    grid = torch.cat([horizontal, vertical], 1)
    flow = torch.cat(
        [
            flow[:, 0:1] / ((image.shape[3] - 1.0) / 2.0),
            flow[:, 1:2] / ((image.shape[2] - 1.0) / 2.0),
        ],
        1,
    )
    return F.grid_sample(
        input=image,
        grid=(grid + flow).permute(0, 2, 3, 1),
        mode="bilinear",
        padding_mode="border",
        align_corners=True,
    )


def _reference_forward(model: IFNet, img0, img1, timestep, scale_list):
    """Practical-RIFE 4.26's IFNet.forward as published: every block input concatenated
    at full resolution before it is downsampled."""
    timestep = (img0[:, :1].clone() * 0 + 1) * timestep
    f0, f1 = model.encode(img0[:, :3]), model.encode(img1[:, :3])
    warped_img0, warped_img1 = img0, img1
    flow = mask = feat = None
    blocks = [model.block0, model.block1, model.block2, model.block3, model.block4]
    for i, block in enumerate(blocks):
        if flow is None:
            flow, mask, feat = block(
                torch.cat((img0[:, :3], img1[:, :3], f0, f1, timestep), 1),
                None,
                scale=scale_list[i],
            )
        else:
            wf0 = _reference_warp(f0, flow[:, :2])
            wf1 = _reference_warp(f1, flow[:, 2:4])
            fd, mask, feat = block(
                torch.cat(
                    (warped_img0[:, :3], warped_img1[:, :3], wf0, wf1, timestep, mask, feat), 1
                ),
                flow,
                scale=scale_list[i],
            )
            flow = flow + fd
        warped_img0 = _reference_warp(img0, flow[:, :2])
        warped_img1 = _reference_warp(img1, flow[:, 2:4])
    mask = torch.sigmoid(mask)
    return warped_img0 * mask + warped_img1 * (1 - mask)


@pytest.mark.parametrize(
    ("height", "width", "scale"), [(64, 128, 1.0), (128, 256, 0.5), (256, 256, 0.25)]
)
def test_the_lean_forward_gives_the_published_networks_frames(height, width, scale):
    torch.manual_seed(0)
    model = IFNet().eval()
    first, second = torch.rand(1, 3, height, width), torch.rand(1, 3, height, width)
    scales = [level / scale for level in (16, 8, 4, 2, 1)]
    with torch.inference_mode():
        expected = _reference_forward(model, first, second, 0.5, scales)
        actual = model(first, second, 0.5, scales)
    # Only float rounding differs (bilinear resizing vectorises by channel count).
    torch.testing.assert_close(actual, expected, atol=1e-4, rtol=0)
    as_bytes = [frame.clamp(0, 1).mul(255).round() for frame in (actual, expected)]
    assert (as_bytes[0] - as_bytes[1]).abs().max() <= 1


def test_the_forward_never_stacks_more_than_four_full_resolution_planes(monkeypatch):
    # The published network concatenates 15 to 32 full-resolution planes per block:
    # 1.4 to 3 GiB at 4320 × 5760, which ran a 16 GB Mac out of graphics memory.
    model = IFNet().eval()
    height, width = 128, 256
    widest = 0

    def recording(join):
        def joined(tensors, *args, **kwargs):
            nonlocal widest
            result = join(tensors, *args, **kwargs)
            sizes = tuple(result.shape)
            if any(sizes[k : k + 2] == (height, width) for k in range(len(sizes) - 1)):
                widest = max(widest, result.numel() // (height * width))
            return result

        return joined

    monkeypatch.setattr(torch, "cat", recording(torch.cat))
    monkeypatch.setattr(torch, "stack", recording(torch.stack))
    with torch.inference_mode():
        model(
            torch.rand(1, 3, height, width),
            torch.rand(1, 3, height, width),
            0.5,
            [level / 0.5 for level in (16, 8, 4, 2, 1)],
        )
    assert 0 < widest <= 4


class _OutOfMemoryOnce:
    def __init__(self, failures: int):
        self.failures = failures
        self.devices: list[str] = []

    def __call__(self, synthesizer, first, second, timesteps, cancel_event=None):
        self.devices.append(synthesizer.device.type)
        if self.failures:
            self.failures -= 1
            raise RuntimeError(
                "MPS backend out of memory (MPS allocated: 5.57 GiB, other allocations: "
                "552.25 MiB, max allowed: 7.34 GiB). Tried to allocate 1.44 GiB on shared pool. "
                "Use PYTORCH_MPS_HIGH_WATERMARK_RATIO=0.0 to disable upper limit for memory "
                "allocations (may cause system failure)."
            )
        return [first] * len(timesteps)


def _gpu_synthesizer(tmp_path, monkeypatch, fake):
    import localsr.video_models.rife.engine as engine_module

    statuses: list[str] = []
    notes: list[str] = []
    synthesizer = RifeSynthesizer(
        save_checkpoint(tmp_path / "flownet.pkl"),
        "cpu",
        status_callback=statuses.append,
        note_callback=notes.append,
    )
    # Pretend the model sits on a GPU; the fake never touches the device.
    synthesizer.device = torch.device("mps")
    emptied: list[str] = []
    monkeypatch.setattr(engine_module, "_empty_cache", lambda device: emptied.append(device.type))
    monkeypatch.setattr(
        RifeSynthesizer, "_synthesize", lambda self, *args, **kwargs: fake(self, *args, **kwargs)
    )
    return synthesizer, statuses, notes, emptied


def test_a_pair_that_ran_out_of_graphics_memory_once_is_retried_on_the_gpu(tmp_path, monkeypatch):
    fake = _OutOfMemoryOnce(failures=1)
    synthesizer, statuses, notes, emptied = _gpu_synthesizer(tmp_path, monkeypatch, fake)
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    assert len(synthesizer.synthesize(frame, frame, [0.5])) == 1
    assert fake.devices == ["mps", "mps"]
    assert emptied == ["mps"]
    assert synthesizer.device.type == "mps"
    assert notes == [] and statuses == []


def test_a_pair_that_still_does_not_fit_moves_interpolation_to_the_cpu_and_says_so(
    tmp_path, monkeypatch
):
    fake = _OutOfMemoryOnce(failures=2)
    synthesizer, statuses, notes, _ = _gpu_synthesizer(tmp_path, monkeypatch, fake)
    frame = np.zeros((6, 4, 3), dtype=np.uint8)
    assert len(synthesizer.synthesize(frame, frame, [0.25, 0.75])) == 2
    assert fake.devices == ["mps", "mps", "cpu"]
    assert synthesizer.device.type == "cpu"
    assert next(synthesizer.model.parameters()).dtype == torch.float32
    assert len(notes) == 1 and notes == statuses
    assert "Frame interpolation ran on the CPU" in notes[0] and "4 × 6" in notes[0]
    assert "PYTORCH" not in notes[0] and "—" not in notes[0]
    # Later pairs of the job stay on the CPU without failing first.
    synthesizer.synthesize(frame, frame, [0.5])
    assert fake.devices[-1] == "cpu" and len(notes) == 1


def test_other_errors_are_not_retried(tmp_path, monkeypatch):
    calls = []

    def broken(synthesizer, *args, **kwargs):
        calls.append(synthesizer.device.type)
        raise RuntimeError("Sizes of tensors must match")

    synthesizer, _, notes, _ = _gpu_synthesizer(tmp_path, monkeypatch, broken)
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    with pytest.raises(RuntimeError, match="Sizes of tensors"):
        synthesizer.synthesize(frame, frame, [0.5])
    assert calls == ["mps"] and notes == []


def test_a_pair_that_fits_once_the_models_are_parked_stays_on_the_gpu(tmp_path, monkeypatch):
    fake = _OutOfMemoryOnce(failures=2)
    synthesizer, statuses, notes, emptied = _gpu_synthesizer(tmp_path, monkeypatch, fake)
    parked: list[int] = []

    def relief():
        parked.append(len(fake.devices))
        return True

    synthesizer.memory_relief = relief
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    assert len(synthesizer.synthesize(frame, frame, [0.5])) == 1
    assert fake.devices == ["mps", "mps", "mps"]
    assert parked == [2]  # after the emptied cache was not enough
    assert synthesizer.device.type == "mps" and notes == [] and statuses == []
    # Later pairs park the models first and need no failed attempt.
    synthesizer.synthesize(frame, frame, [0.5])
    assert fake.devices == ["mps", "mps", "mps", "mps"] and parked == [2, 3]


def test_a_pair_that_does_not_fit_even_with_the_models_parked_moves_to_the_cpu(
    tmp_path, monkeypatch
):
    fake = _OutOfMemoryOnce(failures=3)
    synthesizer, _, notes, _ = _gpu_synthesizer(tmp_path, monkeypatch, fake)
    synthesizer.memory_relief = lambda: True
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    synthesizer.synthesize(frame, frame, [0.5])
    assert fake.devices == ["mps", "mps", "mps", "cpu"] and len(notes) == 1


def test_nothing_to_park_goes_straight_to_the_cpu(tmp_path, monkeypatch):
    fake = _OutOfMemoryOnce(failures=2)
    synthesizer, _, notes, _ = _gpu_synthesizer(tmp_path, monkeypatch, fake)
    synthesizer.memory_relief = lambda: False
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    synthesizer.synthesize(frame, frame, [0.5])
    assert fake.devices == ["mps", "mps", "cpu"] and len(notes) == 1


def test_a_run_without_running_out_neither_parks_nor_moves(tmp_path, monkeypatch):
    fake = _OutOfMemoryOnce(failures=0)
    synthesizer, statuses, notes, emptied = _gpu_synthesizer(tmp_path, monkeypatch, fake)
    calls = []
    synthesizer.memory_relief = lambda: calls.append(1) or True
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    for _ in range(3):
        synthesizer.synthesize(frame, frame, [0.5])
    assert fake.devices == ["mps"] * 3
    assert calls == [] and emptied == [] and notes == [] and statuses == []
