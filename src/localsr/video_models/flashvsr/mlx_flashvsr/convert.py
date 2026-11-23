# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Hermes Reisner. Part of the MLX port of FlashVSR (https://github.com/OpenImagingLab/FlashVSR).

import gc
import os
import re
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence, Set, Tuple, Union

import mlx.core as mx
import mlx.nn as nn
import numpy as np
from mlx.utils import tree_flatten, tree_unflatten

_DTYPE_MAP = {
    "float32": mx.float32,
    "fp32": mx.float32,
    "float16": mx.float16,
    "fp16": mx.float16,
    "bfloat16": mx.bfloat16,
    "bf16": mx.bfloat16,
}

# PyTorch nn.Sequential modules store indexed children as `<attr>.<idx>.*`,
# whereas mlx.nn.Sequential nests them under `<attr>.layers.<idx>.*`.
_SEQUENTIAL_RE = re.compile(
    r"(?:(?<=^)|(?<=\.))(ffn|proj|text_embedding|time_embedding|time_projection|decoder|conv)\.(\d+)\."
)


def resolve_dtype(dtype: Union[str, mx.Dtype]) -> mx.Dtype:
    if isinstance(dtype, mx.Dtype):
        return dtype
    if isinstance(dtype, str):
        key = dtype.lower()
        if key in _DTYPE_MAP:
            return _DTYPE_MAP[key]
    raise ValueError(f"Unsupported dtype {dtype!r}; expected float32, float16, or bfloat16.")


def torch_to_mlx(tensor: Any, dtype: Union[str, mx.Dtype] = mx.float32) -> mx.array:
    target = resolve_dtype(dtype)
    if isinstance(tensor, mx.array):
        return tensor if tensor.dtype == target else tensor.astype(target)
    if isinstance(tensor, np.ndarray):
        arr = mx.array(tensor)
        return arr if arr.dtype == target else arr.astype(target)

    # torch.Tensor path (avoid importing torch at module import time)
    t = tensor.detach().cpu()
    if str(t.dtype) == "torch.bfloat16":
        import torch

        raw = t.view(torch.uint16).numpy()
        arr = mx.array(raw, dtype=mx.uint16).view(mx.bfloat16)
        return arr if target == mx.bfloat16 else arr.astype(target)
    arr = mx.array(t.numpy())
    return arr if arr.dtype == target else arr.astype(target)


def map_pytorch_key(key: str) -> str:
    return _SEQUENTIAL_RE.sub(r"\1.layers.\2.", key)


def map_pytorch_shape(key: str, shape: Sequence[int]) -> Tuple[int, ...]:
    """Return the expected MLX parameter shape for a PyTorch checkpoint tensor."""
    shape_t = tuple(int(s) for s in shape)
    if len(shape_t) == 5:
        # PyTorch Conv3d: (out, in, kT, kH, kW) -> MLX Conv3d: (out, kT, kH, kW, in)
        return (shape_t[0], shape_t[2], shape_t[3], shape_t[4], shape_t[1])
    if len(shape_t) == 4 and key.endswith(".weight"):
        # PyTorch Conv2d: (out, in, kH, kW) -> MLX Conv2d: (out, kH, kW, in)
        return (shape_t[0], shape_t[2], shape_t[3], shape_t[1])
    return shape_t


def _convert_tensor(key: str, value: Any, target: mx.Dtype) -> mx.array:
    arr = torch_to_mlx(value, dtype=target)
    if arr.ndim == 5:
        arr = arr.transpose(0, 2, 3, 4, 1)
    elif arr.ndim == 4 and key.endswith(".weight"):
        arr = arr.transpose(0, 2, 3, 1)
    return arr


def convert_state_dict(
    state_dict: Mapping[str, Any],
    dtype: Union[str, mx.Dtype] = "float32",
) -> Dict[str, mx.array]:
    target = resolve_dtype(dtype)
    converted: Dict[str, mx.array] = {}
    for key, value in state_dict.items():
        converted[map_pytorch_key(key)] = _convert_tensor(key, value, target)
    return converted


def load_checkpoint_state_dict(path: Union[str, os.PathLike]) -> Dict[str, Any]:
    """Load a safetensors or PyTorch (.ckpt/.pth/.pt) state dict from disk."""
    p = Path(path)
    if p.suffix == ".safetensors":
        return dict(mx.load(str(p)))

    import torch

    loaded = torch.load(str(p), map_location="cpu", weights_only=True)
    if (
        isinstance(loaded, Mapping)
        and "state_dict" in loaded
        and isinstance(loaded["state_dict"], Mapping)
    ):
        return dict(loaded["state_dict"])
    if isinstance(loaded, Mapping):
        return dict(loaded)
    raise TypeError(f"Unsupported checkpoint payload type {type(loaded)!r} in {p}")


def _validate_keys_and_shapes(
    expected: Mapping[str, mx.array],
    provided_shapes: Mapping[str, Tuple[str, Tuple[int, ...]]],
    strict: bool,
) -> Tuple[Set[str], list]:
    expected_keys = set(expected.keys())
    provided_keys = set(provided_shapes.keys())
    if strict:
        missing = sorted(expected_keys - provided_keys)
        unexpected = sorted(provided_keys - expected_keys)
        if missing or unexpected:
            raise KeyError(f"State dict mismatch: missing={missing}, unexpected={unexpected}")
    common_keys = sorted(expected_keys & provided_keys)
    mismatched = []
    for k in common_keys:
        pt_key, pt_shape = provided_shapes[k]
        exp_shape = tuple(expected[k].shape)
        got_shape = map_pytorch_shape(pt_key, pt_shape)
        if exp_shape != got_shape:
            mismatched.append((k, exp_shape, got_shape))
    if mismatched:
        raise ValueError(f"State dict shape mismatch: {mismatched}")
    return expected_keys & provided_keys, common_keys


def _stream_load_safetensors(
    path: Path,
    expected: Mapping[str, mx.array],
    target: mx.Dtype,
    strict: bool,
) -> Tuple[Dict[str, mx.array], list]:
    from safetensors import safe_open

    converted: Dict[str, mx.array] = {}
    with safe_open(str(path), framework="mlx") as f:
        pt_keys = list(f.keys())
        provided_shapes = {
            map_pytorch_key(k): (k, tuple(f.get_slice(k).get_shape())) for k in pt_keys
        }
        _, common_keys = _validate_keys_and_shapes(expected, provided_shapes, strict)
        for idx, mlx_key in enumerate(common_keys):
            pt_key, _ = provided_shapes[mlx_key]
            raw = f.get_tensor(pt_key)
            arr = _convert_tensor(pt_key, raw, target)
            mx.eval(arr)
            converted[mlx_key] = arr
            del raw
            if (idx + 1) % 32 == 0:
                mx.clear_cache()
    gc.collect()
    mx.clear_cache()
    return converted, common_keys


def _stream_load_torch_ckpt(
    path: Path,
    expected: Mapping[str, mx.array],
    target: mx.Dtype,
    strict: bool,
) -> Tuple[Dict[str, mx.array], list]:
    raw_sd = load_checkpoint_state_dict(path)
    provided_shapes = {map_pytorch_key(k): (k, tuple(v.shape)) for k, v in raw_sd.items()}
    _, common_keys = _validate_keys_and_shapes(expected, provided_shapes, strict)
    converted: Dict[str, mx.array] = {}
    for idx, mlx_key in enumerate(common_keys):
        pt_key, _ = provided_shapes[mlx_key]
        raw = raw_sd.pop(pt_key)
        arr = _convert_tensor(pt_key, raw, target)
        mx.eval(arr)
        converted[mlx_key] = arr
        del raw
        if (idx + 1) % 32 == 0:
            mx.clear_cache()
    raw_sd.clear()
    del raw_sd
    gc.collect()
    mx.clear_cache()
    return converted, common_keys


def load_torch_state_dict(
    module: nn.Module,
    state_dict: Union[Mapping[str, Any], str, os.PathLike],
    dtype: Union[str, mx.Dtype] = "float32",
    strict: bool = True,
) -> nn.Module:
    target = resolve_dtype(dtype)
    expected = dict(tree_flatten(module.parameters()))

    if isinstance(state_dict, (str, os.PathLike)):
        p = Path(state_dict)
        if p.suffix == ".safetensors":
            converted, common_keys = _stream_load_safetensors(p, expected, target, strict)
        else:
            converted, common_keys = _stream_load_torch_ckpt(p, expected, target, strict)
    else:
        converted = convert_state_dict(state_dict, dtype=target)
        expected_keys = set(expected.keys())
        provided_keys = set(converted.keys())
        if strict:
            missing = sorted(expected_keys - provided_keys)
            unexpected = sorted(provided_keys - expected_keys)
            if missing or unexpected:
                raise KeyError(f"State dict mismatch: missing={missing}, unexpected={unexpected}")
        common_keys = sorted(expected_keys & provided_keys)
        mismatched = [
            (k, tuple(expected[k].shape), tuple(converted[k].shape))
            for k in common_keys
            if tuple(expected[k].shape) != tuple(converted[k].shape)
        ]
        if mismatched:
            raise ValueError(f"State dict shape mismatch: {mismatched}")

    module.update(tree_unflatten([(k, converted[k]) for k in common_keys]))
    return module
