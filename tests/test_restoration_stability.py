"""A descriptor's range clipping must not conceal an unstable restoration."""

import threading
from dataclasses import replace

import numpy as np
import pytest
import torch
from torch import nn

from localsr.core.inference import (
    InferenceEngine,
    UnstableRestorationError,
    _introduced_periodic_signal,
    _span_output_is_unstable,
)
from localsr.core.model_adapter import NormalizedModelInfo
from localsr.core.tiling import generate_tiles


class CropSensitiveNetwork(nn.Module):
    def __init__(self, mode="recover", cancel=None):
        super().__init__()
        self.shapes = []
        self.mode, self.cancel = mode, cancel

    def forward(self, value):
        self.shapes.append(tuple(value.shape[-2:]))
        if self.cancel is not None:
            self.cancel.set()
        if value.shape[-1] < 300 or self.mode == "fail":
            if self.mode == "colour":
                output = value * 0 + 0.4
                output[:, 0] = 0.9
                return output  # inside [0, 1], but invents colour on a neutral scan
            if self.mode == "correction":
                return value * 0 + 0.05
            if self.mode == "checker":
                output = value.clone()
                output[..., ::2, ::2] += 0.04
                output[..., 1::2, 1::2] += 0.04
                output[..., ::2, 1::2] -= 0.04
                output[..., 1::2, ::2] -= 0.04
                return output
            return value * 0 + 30  # finite; a descriptor would silently clip to white
        if self.mode == "oom":
            raise RuntimeError("out of memory")
        return value


class ClippingDescriptor:
    def __init__(self, model):
        self.model = model

    def __call__(self, value):
        return self.model(value).clamp(0, 1)


INFO = NormalizedModelInfo(
    architecture="NAFNet",
    scale=1,
    in_channels=3,
    out_channels=3,
    tiling_supported=True,
    half_supported=False,
    size_requirements_min=0,
    size_requirements_mult=1,
    filename="denoiser.pth",
    warnings=[],
)


def render(network, *, width=1982, height=1361, cancel=None):
    # Regression: final narrow column, same position and shape as the real scan.
    tile = list(generate_tiles(width, height, 256, 16, 1))[-9]
    source = torch.linspace(0, 1, width * height).reshape(1, height, width).repeat(3, 1, 1)
    output = InferenceEngine(None).run_tile(
        tile,
        source,
        INFO,
        torch.device("cpu"),
        torch.float32,
        16,
        ClippingDescriptor(network),
        cancel_event=cancel,
        float_output=True,
    )
    expected = source[
        :, tile.core_y : tile.core_y + tile.core_h, tile.core_x : tile.core_x + tile.core_w
    ]
    np.testing.assert_allclose(output, expected.numpy(), atol=1e-7)
    return tile


def test_unstable_edge_is_recomputed_with_aligned_context_and_exact_output_coordinates():
    network = CropSensitiveNetwork()
    tile = render(network)
    assert (tile.core_x, tile.core_y) == (1792, 1024)
    assert network.shapes == [(288, 222), (369, 478)]
    assert not network._forward_hooks


@pytest.mark.parametrize("mode", ["colour", "correction", "checker"])
def test_in_range_colour_blocks_also_require_stable_context(mode):
    network = CropSensitiveNetwork(mode)
    render(network)
    assert len(network.shapes) == 2
    assert not network._forward_hooks


def test_existing_periodic_texture_and_normal_noise_reduction_are_not_rejected():
    source = torch.full((1, 3, 67, 93), 0.5)
    source[..., ::2, ::2] += 0.1
    source[..., 1::2, 1::2] += 0.1
    assert _introduced_periodic_signal(source, source) == 0
    denoised = source * 0.5 + 0.25
    assert _introduced_periodic_signal(denoised, source) <= 1e-6
    assert _introduced_periodic_signal(source[..., :7, :9], source[..., :7, :9]) == 0


@pytest.mark.parametrize("mode", ["fail", "oom"])
def test_unrecoverable_region_fails_instead_of_saving_clipped_garbage(mode):
    network = CropSensitiveNetwork(mode)
    with pytest.raises(UnstableRestorationError, match="No damaged output was saved"):
        render(network)
    assert 1 < len(network.shapes) <= 5
    assert not network._forward_hooks


def test_cancellation_stops_before_expanding_the_context():
    cancel = threading.Event()
    network = CropSensitiveNetwork(cancel=cancel)
    with pytest.raises(InterruptedError):
        render(network, cancel=cancel)
    assert len(network.shapes) == 1
    assert not network._forward_hooks


def test_other_architectures_keep_their_existing_range_handling():
    network = CropSensitiveNetwork("fail")
    tile = next(generate_tiles(17, 19, 256, 16, 1))
    output = InferenceEngine(None).run_tile(
        tile,
        torch.zeros(3, 19, 17),
        replace(INFO, architecture="Other"),
        torch.device("cpu"),
        torch.float32,
        16,
        ClippingDescriptor(network),
    )
    assert output.shape == (3, 19, 17)
    assert (output == 255).all()
    assert len(network.shapes) == 1


def test_span_rejects_extreme_raw_values_before_a_descriptor_can_hide_them():
    network = CropSensitiveNetwork("fail")
    tile = next(generate_tiles(33, 35, 256, 16, 1))
    with pytest.raises(UnstableRestorationError, match="SPAN.*No damaged output was saved"):
        InferenceEngine(None).run_tile(
            tile,
            torch.zeros(3, 35, 33),
            replace(INFO, architecture="SPAN"),
            torch.device("cpu"),
            torch.float32,
            16,
            ClippingDescriptor(network),
        )
    assert not network._forward_hooks
    assert not _span_output_is_unstable(torch.tensor([-0.2, 0.5, 1.2]))
    assert _span_output_is_unstable(torch.tensor([float("nan")]))
    assert _span_output_is_unstable(torch.tensor([-1356.4, 1139.1]))
