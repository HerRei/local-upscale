import threading

import pytest
import torch
from torch import nn

from localsr.core.inference import InferenceEngine
from localsr.core.model_adapter import NormalizedModelInfo


class DummyModel(nn.Module):
    def __init__(self, scale):
        super().__init__()
        self.scale = scale

    def forward(self, x):
        # Dummy super-resolution: just nearest neighbor upscale
        return torch.nn.functional.interpolate(x, scale_factor=self.scale, mode="nearest")


class DummyAdapter:
    def __init__(self, scale, in_c=3, out_c=3, min_req=1, mult_req=1):
        self.scale = scale
        self.model_info = NormalizedModelInfo(
            architecture="Dummy",
            scale=scale,
            in_channels=in_c,
            out_channels=out_c,
            tiling_supported=True,
            half_supported=False,
            size_requirements_min=min_req,
            size_requirements_mult=mult_req,
            filename="dummy.pth",
            warnings=[],
        )

    def load(self, path, device, precision):
        model = DummyModel(self.scale).to(device).to(precision)
        return model, None

    def release(self):
        pass


def test_inference_dimensions():
    # Test exactly 2x and 4x
    for scale in [2, 4]:
        adapter = DummyAdapter(scale)
        engine = InferenceEngine(adapter)

        img_tensor = torch.rand((3, 33, 47))
        img_data = {"tensor": img_tensor}

        cancel = threading.Event()

        def cb(c, t, s):
            pass

        writer = engine.process_image(
            img_data, adapter.model_info, "dummy", "cpu", "fp32", 32, 8, cancel, cb, False
        )

        arr = writer.get_array()
        assert arr.shape == (3, 33 * scale, 47 * scale)
        writer.cleanup()


def test_inference_small_image_large_halo():
    # Halo larger than image dimensions
    adapter = DummyAdapter(2)
    engine = InferenceEngine(adapter)

    img_tensor = torch.rand((3, 10, 10))
    img_data = {"tensor": img_tensor}

    cancel = threading.Event()

    def cb(c, t, s):
        pass

    writer = engine.process_image(
        img_data, adapter.model_info, "dummy", "cpu", "fp32", 256, 32, cancel, cb, False
    )

    arr = writer.get_array()
    assert arr.shape == (3, 20, 20)
    writer.cleanup()


def test_divisibility_padding():
    # Force divisibility by 8
    adapter = DummyAdapter(2, mult_req=8)
    engine = InferenceEngine(adapter)

    img_tensor = torch.rand((3, 15, 15))
    img_data = {"tensor": img_tensor}

    cancel = threading.Event()

    def cb(c, t, s):
        pass

    writer = engine.process_image(
        img_data, adapter.model_info, "dummy", "cpu", "fp32", 10, 4, cancel, cb, False
    )

    arr = writer.get_array()
    assert arr.shape == (3, 30, 30)
    writer.cleanup()


def test_square_size_requirement_padding():
    class SquareModel(DummyModel):
        def __init__(self, scale):
            super().__init__(scale)
            self.seen_shapes = []

        def forward(self, x):
            self.seen_shapes.append(x.shape[-2:])
            assert x.shape[-2] == x.shape[-1]
            return super().forward(x)

    class SquareAdapter(DummyAdapter):
        def __init__(self):
            super().__init__(2)
            self.model_info.size_requirements_square = True
            self.model = SquareModel(2)

        def load(self, path, device, precision):
            return self.model.to(device).to(precision), None

    adapter = SquareAdapter()
    engine = InferenceEngine(adapter)
    writer = engine.process_image(
        {"tensor": torch.rand((3, 13, 29))},
        adapter.model_info,
        "dummy",
        "cpu",
        "fp32",
        64,
        4,
        threading.Event(),
        lambda _completed, _total, _size: None,
        False,
    )

    assert adapter.model.seen_shapes
    assert all(height == width for height, width in adapter.model.seen_shapes)
    assert writer.get_array().shape == (3, 26, 58)
    writer.cleanup()


def test_nan_inf_detection():
    class PoisonModel(nn.Module):
        def forward(self, x):
            return x * float("inf")

    class PoisonAdapter(DummyAdapter):
        def load(self, path, device, precision):
            return PoisonModel(), None

    adapter = PoisonAdapter(2)
    engine = InferenceEngine(adapter)
    img_tensor = torch.rand((3, 10, 10))
    cancel = threading.Event()

    with pytest.raises(ValueError, match="NaN or Infinity"):
        engine.process_image(
            {"tensor": img_tensor},
            adapter.model_info,
            "dummy",
            "cpu",
            "fp32",
            32,
            8,
            cancel,
            lambda c, t, s: None,
            False,
        )
