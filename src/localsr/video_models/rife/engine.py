"""RIFE frame synthesis for LocalSR's frame interpolation stage.

Loads a catalog-verified Practical-RIFE ``flownet.pkl`` as tensors only and
makes in-between frames for :func:`localsr.core.frame_interpolation.interpolate_frames`.
RIFE 4.x takes the timestep as an input, so 23.976 → 60 fps needs no recursive
halving. Backends without a PyTorch route (DirectML and OpenVINO, which LocalSR
reaches through ONNX Runtime) interpolate on the CPU.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Callable, Sequence
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

from .ifnet import IFNet

# RIFE 4.25/4.26 pad their input to a multiple of 64 / scale (HolyWu/vs-rife).
_MODULO = 64
# Practical-RIFE's Model.inference scale_list for 4.25 and 4.26.
_SCALE_LIST = (16, 8, 4, 2, 1)


def flow_scale_for(height: int, width: int) -> float:
    """Practical-RIFE recommends scale 0.5 for 4K: flow is estimated at a lower resolution."""
    longest = max(height, width)
    if longest > 5120:
        return 0.25
    if longest > 2560:
        return 0.5
    return 1.0


def torch_device_for(device_str: str) -> torch.device:
    kind = device_str.split(":", 1)[0]
    if kind in {"directml", "openvino", "qnn"}:
        return torch.device("cpu")
    return torch.device(device_str)


def _load_state(path: Path) -> dict[str, torch.Tensor]:
    state = torch.load(path, map_location="cpu", weights_only=True)
    if not isinstance(state, dict):
        raise ValueError("The RIFE checkpoint is not a state dict.")
    return {name.removeprefix("module."): value for name, value in state.items()}


class RifeSynthesizer:
    def __init__(
        self,
        checkpoint: Path,
        device_str: str,
        *,
        status_callback: Callable[[str], None] | None = None,
    ):
        self.device = torch_device_for(device_str)
        if self.device.type == "cpu" and not device_str.startswith("cpu") and status_callback:
            status_callback("Frame interpolation runs on the CPU with this hardware choice.")
        model = IFNet()
        state = _load_state(Path(checkpoint))
        expected = model.state_dict()
        missing = [name for name in expected if name not in state]
        mismatched = [
            name
            for name in expected
            if name in state and tuple(state[name].shape) != tuple(expected[name].shape)
        ]
        if missing or mismatched:
            raise ValueError(
                "This RIFE checkpoint does not match the network "
                f"({len(missing)} missing, {len(mismatched)} mismatched tensors)."
            )
        # Practical-RIFE checkpoints also carry the training-only teacher block.
        model.load_state_dict({name: state[name] for name in expected})
        # FP16 only where the community runs RIFE that way (NVIDIA CUDA); FP32 elsewhere.
        self.half = self.device.type == "cuda" and torch.version.hip is None
        self.model = model.eval().to(self.device, torch.float16 if self.half else torch.float32)

    def _tensor(self, rgb: np.ndarray, padded: tuple[int, int]) -> torch.Tensor:
        tensor = torch.from_numpy(np.ascontiguousarray(rgb[..., :3])).to(self.device)
        tensor = tensor.permute(2, 0, 1).unsqueeze(0)
        tensor = tensor.to(torch.float16 if self.half else torch.float32) / 255.0
        height, width = rgb.shape[:2]
        return F.pad(tensor, (0, padded[1] - width, 0, padded[0] - height))

    @torch.inference_mode()
    def synthesize(
        self,
        first: np.ndarray,
        second: np.ndarray,
        timesteps: Sequence[float],
        cancel_event: threading.Event | None = None,
    ) -> list[np.ndarray]:
        height, width = first.shape[:2]
        scale = flow_scale_for(height, width)
        multiple = max(_MODULO, int(_MODULO / scale))
        padded = (math.ceil(height / multiple) * multiple, math.ceil(width / multiple) * multiple)
        img0, img1 = self._tensor(first, padded), self._tensor(second, padded)
        f0, f1 = self.model.encode(img0), self.model.encode(img1)
        scales = [level / scale for level in _SCALE_LIST]
        frames = []
        for step in timesteps:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("video job cancelled")
            merged = self.model(img0, img1, float(step), scales, f0, f1)
            pixels = merged[0, :, :height, :width].clamp(0, 1).mul(255).round().byte()
            frames.append(np.ascontiguousarray(pixels.permute(1, 2, 0).cpu().numpy()))
        return frames


__all__ = ["RifeSynthesizer", "flow_scale_for", "torch_device_for"]
