"""DDColor checkpoint loading and OpenCV Lab colorization."""

from __future__ import annotations

import importlib
import math
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import torch
import torch.nn.functional as F

from .model import DDColor

DDCOLOR_INPUT_SIZE = (512, 512)
COLORFUL_INPUT_CHROMA_THRESHOLD = 4.0
ALREADY_COLORFUL_NOTE = "This photo already had colour. It was recoloured."


def _import_cv2():
    try:
        return importlib.import_module("cv2")
    except ImportError as error:
        raise RuntimeError("OpenCV (cv2) is required for DDColor photo colorization.") from error


@dataclass(frozen=True)
class DDColorConfig:
    encoder_name: str
    depths: tuple[int, ...]
    dims: tuple[int, ...]
    nf: int
    num_queries: int
    hidden_dim: int
    num_scales: int
    dec_layers: int
    dim_feedforward: int
    nheads: int
    num_output_channels: int
    input_size: tuple[int, int] = DDCOLOR_INPUT_SIZE
    last_norm: str = "Spectral"
    do_normalize: bool = False


@dataclass(frozen=True)
class DDColorArchitecture:
    name: str = "DDColor"


@dataclass(frozen=True)
class DDColorSizeRequirements:
    minimum: int = 0
    multiple_of: int = 1
    square: bool = False


class DDColorDescriptor:
    """Spandrel-compatible descriptor wrapper for DDColor."""

    def __init__(self, model: DDColor, config: DDColorConfig):
        self.model = model
        self.config = config
        self.architecture = DDColorArchitecture()
        self.scale = 1
        self.input_channels = 3
        self.output_channels = 3
        self.supports_half = False
        self.size_requirements = DDColorSizeRequirements()
        self.input_size = config.input_size

    def to(self, *args: Any, **kwargs: Any) -> DDColorDescriptor:
        self.model = self.model.to(*args, **kwargs)
        return self

    def eval(self) -> DDColorDescriptor:
        self.model.eval()
        return self

    def __call__(self, x: torch.Tensor) -> torch.Tensor:
        return self.model(x)


def unwrap_ddcolor_state_dict(checkpoint: object) -> dict[str, torch.Tensor]:
    if not isinstance(checkpoint, Mapping):
        raise ValueError("DDColor checkpoint must be a mapping or state dict.")
    params = checkpoint.get("params") if "params" in checkpoint else checkpoint
    if not isinstance(params, Mapping):
        raise ValueError("DDColor checkpoint params must be a state dict mapping.")
    return {str(k): v for k, v in params.items() if isinstance(v, torch.Tensor)}


def is_ddcolor_state_dict(checkpoint: object) -> bool:
    if not isinstance(checkpoint, Mapping):
        return False
    state = checkpoint.get("params") if "params" in checkpoint else checkpoint
    if not isinstance(state, Mapping):
        return False
    required = {
        "encoder.arch.downsample_layers.0.0.weight",
        "decoder.color_decoder.query_feat.weight",
        "decoder.color_decoder.level_embed.weight",
    }
    has_refine = "refine_net.0.0.weight_orig" in state or "refine_net.0.0.weight" in state
    return required <= set(state) and has_refine


def _tensor_shape_first(state_dict: Mapping[str, torch.Tensor], *keys: str) -> tuple[int, ...]:
    for key in keys:
        tensor = state_dict.get(key)
        if tensor is not None:
            return tuple(int(dim) for dim in tensor.shape)
    raise ValueError(f"Missing expected DDColor tensor: {', '.join(keys)}")


def infer_ddcolor_config(
    state_dict: Mapping[str, torch.Tensor],
    *,
    input_size: tuple[int, int] = DDCOLOR_INPUT_SIZE,
) -> DDColorConfig:
    if not is_ddcolor_state_dict(state_dict):
        raise ValueError("State dict does not match the DDColor architecture.")

    dims = tuple(
        _tensor_shape_first(
            state_dict,
            f"encoder.arch.downsample_layers.{i}.{0 if i == 0 else 1}.weight",
        )[0]
        for i in range(4)
    )
    depths = tuple(
        sum(
            1
            for key in state_dict
            if key.startswith(f"encoder.arch.stages.{i}.") and key.endswith(".gamma")
        )
        for i in range(4)
    )
    if any(depth <= 0 for depth in depths):
        raise ValueError("Invalid ConvNeXt stage depths in DDColor checkpoint.")

    if dims == (96, 192, 384, 768) and depths == (3, 3, 9, 3):
        encoder_name = "convnext-t"
    elif dims == (192, 384, 768, 1536) and depths == (3, 3, 27, 3):
        encoder_name = "convnext-l"
    else:
        encoder_name = "custom"

    query_shape = _tensor_shape_first(state_dict, "decoder.color_decoder.query_feat.weight")
    num_queries, hidden_dim = query_shape[0], query_shape[1]
    num_scales = _tensor_shape_first(state_dict, "decoder.color_decoder.level_embed.weight")[0]
    layer_indices = [
        int(key.split(".")[3])
        for key in state_dict
        if key.startswith("decoder.color_decoder.transformer_self_attention_layers.")
    ]
    if not layer_indices:
        raise ValueError("Missing transformer decoder layers in DDColor checkpoint.")
    dec_layers = max(layer_indices) + 1
    dim_feedforward = _tensor_shape_first(
        state_dict, "decoder.color_decoder.transformer_ffn_layers.0.linear1.weight"
    )[0]
    nf = _tensor_shape_first(
        state_dict, "decoder.layers.0.conv.0.weight_orig", "decoder.layers.0.conv.0.weight"
    )[0]
    num_output_channels = _tensor_shape_first(
        state_dict, "refine_net.0.0.weight_orig", "refine_net.0.0.weight"
    )[0]
    nheads = 8 if hidden_dim >= 256 and hidden_dim % 8 == 0 else max(1, math.gcd(hidden_dim, 8))

    return DDColorConfig(
        encoder_name=encoder_name,
        depths=depths,
        dims=dims,
        nf=nf,
        num_queries=num_queries,
        hidden_dim=hidden_dim,
        num_scales=num_scales,
        dec_layers=dec_layers,
        dim_feedforward=dim_feedforward,
        nheads=nheads,
        num_output_channels=num_output_channels,
        input_size=input_size,
    )


def load_ddcolor_descriptor(
    checkpoint_or_state: Mapping[str, Any],
    *,
    input_size: tuple[int, int] = DDCOLOR_INPUT_SIZE,
) -> DDColorDescriptor:
    state_dict = unwrap_ddcolor_state_dict(checkpoint_or_state)
    config = infer_ddcolor_config(state_dict, input_size=input_size)
    model = DDColor(
        encoder_name=config.encoder_name if config.encoder_name != "custom" else "convnext-t",
        decoder_name="MultiScaleColorDecoder",
        num_input_channels=3,
        input_size=config.input_size,
        nf=config.nf,
        num_output_channels=config.num_output_channels,
        last_norm=config.last_norm,
        do_normalize=config.do_normalize,
        num_queries=config.num_queries,
        num_scales=config.num_scales,
        dec_layers=config.dec_layers,
        depths=config.depths,
        dims=config.dims,
        hidden_dim=config.hidden_dim,
        nheads=config.nheads,
        dim_feedforward=config.dim_feedforward,
    )
    model.load_state_dict(state_dict, strict=True)
    model.eval()
    return DDColorDescriptor(model, config)


def load_ddcolor_checkpoint(
    path: str | Path,
    *,
    input_size: tuple[int, int] = DDCOLOR_INPUT_SIZE,
) -> DDColorDescriptor:
    checkpoint = torch.load(Path(path), map_location="cpu", weights_only=True)
    return load_ddcolor_descriptor(checkpoint, input_size=input_size)


@dataclass(frozen=True)
class ColorizeResult:
    rgb: np.ndarray
    input_mean_chroma: float
    output_mean_chroma: float
    already_colorful: bool
    note: str | None = None


def mean_lab_chroma(ab: np.ndarray) -> float:
    """Mean chroma magnitude sqrt(a^2 + b^2) for a (..., 2) Lab ab array."""
    return float(np.mean(np.hypot(ab[..., 0], ab[..., 1])))


def rgb_to_lab(rgb_float: np.ndarray) -> np.ndarray:
    """Convert float32 RGB in [0, 1] (H, W, 3) to OpenCV float32 Lab."""
    cv2 = _import_cv2()
    clipped = np.clip(np.asarray(rgb_float, dtype=np.float32), 0.0, 1.0)
    return cv2.cvtColor(clipped, cv2.COLOR_RGB2Lab)


def lab_to_rgb(lab_float: np.ndarray) -> np.ndarray:
    """Convert OpenCV float32 Lab (H, W, 3) to clipped float32 RGB in [0, 1]."""
    cv2 = _import_cv2()
    rgb = cv2.cvtColor(np.asarray(lab_float, dtype=np.float32), cv2.COLOR_Lab2RGB)
    return np.clip(rgb, 0.0, 1.0)


def predict_ab(
    model: DDColor | DDColorDescriptor,
    rgb_float: np.ndarray,
    *,
    input_size: tuple[int, int] | None = None,
    device: torch.device | None = None,
    cancel_event: threading.Event | None = None,
) -> torch.Tensor:
    """The model's colour planes for a float32 [0, 1] RGB picture: a (1, 2, h, w) CPU tensor
    at the model's input size, whatever the picture's own size."""
    cv2 = _import_cv2()
    raw_model = model.model if isinstance(model, DDColorDescriptor) else model
    target_size = input_size or (
        model.input_size if isinstance(model, DDColorDescriptor) else DDCOLOR_INPUT_SIZE
    )
    target_h, target_w = int(target_size[0]), int(target_size[1])
    if device is None:
        try:
            device = next(raw_model.parameters()).device
        except StopIteration:
            device = torch.device("cpu")

    resized_rgb = cv2.resize(rgb_float, (target_w, target_h), interpolation=cv2.INTER_LINEAR)
    l_resized = rgb_to_lab(resized_rgb)[:, :, :1]
    gray_lab = np.concatenate(
        (l_resized, np.zeros_like(l_resized), np.zeros_like(l_resized)), axis=-1
    )
    gray_rgb = lab_to_rgb(gray_lab)

    tensor_in = torch.from_numpy(gray_rgb.transpose(2, 0, 1)).float().unsqueeze(0).to(device)
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("Cancelled")

    raw_model.eval()
    with torch.no_grad():
        output_ab = raw_model(tensor_in)
    if not torch.isfinite(output_ab).all():
        raise ValueError("DDColor produced NaN or Infinity values.")
    return output_ab.detach().to(torch.float32).cpu()


def colorize_rgb_array(
    model: DDColor | DDColorDescriptor,
    rgb: np.ndarray,
    *,
    input_size: tuple[int, int] | None = None,
    device: torch.device | None = None,
    cancel_event: threading.Event | None = None,
) -> ColorizeResult:
    """Colorize an RGB image (uint8, uint16, or float32 [0, 1]) via DDColor Lab inference."""
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError("DDColor expects an HxWx3 RGB image.")
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("Cancelled")

    orig_dtype = rgb.dtype
    if orig_dtype == np.uint8:
        rgb_float = rgb.astype(np.float32) / 255.0
    elif orig_dtype == np.uint16:
        rgb_float = rgb.astype(np.float32) / 65535.0
    elif np.issubdtype(orig_dtype, np.floating):
        rgb_float = np.clip(rgb.astype(np.float32), 0.0, 1.0)
    else:
        raise ValueError(f"Unsupported RGB dtype for DDColor: {orig_dtype}")

    height, width = rgb_float.shape[:2]
    orig_lab = rgb_to_lab(rgb_float)
    orig_l = orig_lab[:, :, :1]
    input_chroma = mean_lab_chroma(orig_lab[:, :, 1:])
    already_colorful = input_chroma > COLORFUL_INPUT_CHROMA_THRESHOLD
    note = ALREADY_COLORFUL_NOTE if already_colorful else None

    output_ab_cpu = predict_ab(
        model, rgb_float, input_size=input_size, device=device, cancel_event=cancel_event
    )
    if tuple(output_ab_cpu.shape[-2:]) != (height, width):
        ab_resized = F.interpolate(output_ab_cpu, size=(height, width), mode="nearest")
    else:
        ab_resized = output_ab_cpu
    ab_hw2 = ab_resized[0].numpy().transpose(1, 2, 0)

    output_lab = np.concatenate((orig_l, ab_hw2), axis=-1)
    output_rgb_float = lab_to_rgb(output_lab)
    output_chroma = mean_lab_chroma(rgb_to_lab(output_rgb_float)[:, :, 1:])

    if orig_dtype == np.uint8:
        output_rgb = np.rint(output_rgb_float * 255.0).clip(0, 255).astype(np.uint8)
    elif orig_dtype == np.uint16:
        output_rgb = np.rint(output_rgb_float * 65535.0).clip(0, 65535).astype(np.uint16)
    else:
        output_rgb = output_rgb_float.astype(orig_dtype, copy=False)

    return ColorizeResult(
        rgb=output_rgb,
        input_mean_chroma=input_chroma,
        output_mean_chroma=output_chroma,
        already_colorful=already_colorful,
        note=note,
    )


def colorize_tensor(
    model: DDColor | DDColorDescriptor,
    img_tensor: torch.Tensor,
    *,
    device: torch.device | None = None,
    cancel_event: threading.Event | None = None,
    bit_depth: int = 8,
) -> ColorizeResult:
    """Colorize a (3, H, W) float32 [0, 1] RGB tensor and return uint8 or uint16 (3, H, W)."""
    rgb_hwc = img_tensor.detach().cpu().clamp(0.0, 1.0).permute(1, 2, 0).numpy().astype(np.float32)
    result = colorize_rgb_array(model, rgb_hwc, device=device, cancel_event=cancel_event)
    if bit_depth == 16:
        quantized = np.rint(result.rgb * 65535.0).clip(0, 65535).astype(np.uint16)
    else:
        quantized = np.rint(result.rgb * 255.0).clip(0, 255).astype(np.uint8)
    chw = np.ascontiguousarray(quantized.transpose(2, 0, 1))
    return ColorizeResult(
        rgb=chw,
        input_mean_chroma=result.input_mean_chroma,
        output_mean_chroma=result.output_mean_chroma,
        already_colorful=result.already_colorful,
        note=result.note,
    )
