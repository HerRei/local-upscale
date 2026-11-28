"""Background removal is a developer-only model step."""

from pathlib import Path

import pytest

from localsr.core.model_catalog import CATALOG_BY_ID, ModelPurpose
from localsr.core.presets import PresetMode, rank_models_for_preset
from localsr.core.steps import STEPS_BY_ID
from localsr.worker.server import _engine_features

REAL_CHECKPOINT = (
    Path(__file__).resolve().parents[1] / ".cache/birefnet/birefnet_general.safetensors"
)


def test_background_model_is_not_a_restoration_preset():
    model = CATALOG_BY_ID["birefnet_general"]
    assert model.purposes == (ModelPurpose.BACKGROUND,)
    assert not model.fixes
    for purpose in ("photo", "denoise", "deblur", "restoration"):
        assert model not in rank_models_for_preset(
            [model], PresetMode.BEST_DENOISE, purpose=purpose
        )


def test_background_step_is_listed_without_the_dev_switch(monkeypatch):
    step = STEPS_BY_ID["remove_background"]
    assert step.model_task == "background" and step.output_alpha and not step.dev_only
    monkeypatch.delenv("LOCALSR_DEV_STEPS", raising=False)
    assert "image_background_removal" in _engine_features([])


@pytest.mark.parametrize("bit_depth", [8, 16])
def test_small_birefnet_step_runs_through_image_job(tmp_path, monkeypatch, bit_depth):
    import hashlib
    from types import SimpleNamespace

    import numpy as np
    import torch
    from PIL import Image
    from safetensors.torch import save_file
    from test_model_steps import _job, _real_server, _source

    from localsr.core.image_io_16bit import read_png_16, write_png_16
    from localsr.core.model_adapter import ModelAdapter
    from localsr.core.model_catalog import CATALOG_BY_FILENAME, CATALOG_BY_ID
    from localsr.image_models.birefnet import BiRefNet
    from localsr.worker import image_job

    monkeypatch.setattr(image_job, "BIREFNET_INPUT_SIZE", 64)
    torch.manual_seed(27)
    network = BiRefNet(embed_dim=8, depths=(1, 1, 1, 1), heads=(1, 2, 4, 8), window_size=4).eval()
    path = tmp_path / "small-birefnet.safetensors"
    save_file(network.state_dict(), str(path))
    entry = SimpleNamespace(
        model_id="small_background",
        name="Small BiRefNet",
        display_name="Small BiRefNet",
        filename=path.name,
        size_bytes=path.stat().st_size,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        recommended_halo=0,
        time_factor=1.0,
        architecture="BiRefNet",
    )
    monkeypatch.setitem(CATALOG_BY_FILENAME, path.name, entry)
    monkeypatch.setitem(CATALOG_BY_ID, entry.model_id, entry)
    info = ModelAdapter().inspect(str(path))
    assert (info.architecture, info.scale, info.out_channels, info.tiling_supported) == (
        "BiRefNet",
        1,
        1,
        False,
    )
    source_path, rgba = _source(tmp_path)
    peak = 255 if bit_depth == 8 else 65535
    if bit_depth == 16:
        rgba = rgba.astype(np.uint16) * 257
        rgba[..., :3] += 23
        write_png_16(source_path, rgba)
    tensor = torch.from_numpy(rgba[..., :3].copy().astype(np.float32)).permute(2, 0, 1) / peak
    model = image_job.StepModel(str(path), info, network, torch.device("cpu"), torch.float32)
    expected_matte = image_job._run_background_stage(
        SimpleNamespace(_check_cancelled=lambda: None),
        {"tensor": tensor},
        None,
        0,
        {},
        None,
        [],
        model,
    ).numpy()
    data = _job(
        tmp_path,
        [
            {
                "kind": "remove_background",
                "step": "remove_background",
                "model_id": entry.model_id,
                "model_path": str(path),
            }
        ],
        output_scale=1,
        bit_depth=bit_depth,
    )
    server, _engine = _real_server()
    server._run_job("step-job", data)
    if bit_depth == 8:
        with Image.open(data["output_path"]) as output:
            assert output.mode == "RGBA"
            result = np.array(output)
    else:
        result, colour = read_png_16(Path(data["output_path"]))
        assert colour == 6
    np.testing.assert_array_equal(result[..., :3], rgba[..., :3])
    expected = np.rint(expected_matte * rgba[..., 3])
    assert np.max(np.abs(result[..., 3].astype(np.int32) - expected)) <= 1


@pytest.mark.skipif(not REAL_CHECKPOINT.is_file(), reason="BiRefNet checkpoint not cached locally")
def test_real_birefnet_matte_if_cached():
    from types import SimpleNamespace

    import torch
    from safetensors.torch import load_file

    from localsr.image_models.birefnet import load_birefnet_descriptor
    from localsr.worker.image_job import StepModel, _run_background_stage

    path = REAL_CHECKPOINT
    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    model = load_birefnet_descriptor(load_file(str(path))).eval().to(device)
    source = torch.ones((3, 1024, 1024))
    yy, xx = torch.meshgrid(torch.arange(1024), torch.arange(1024), indexing="ij")
    circle = (xx - 512).square() + (yy - 512).square() <= 262**2
    source[1:, circle] = 0
    matte = _run_background_stage(
        SimpleNamespace(_check_cancelled=lambda: None),
        {"tensor": source},
        None,
        0,
        {},
        None,
        [],
        StepModel(str(path), None, model, device, torch.float32),
    ).cpu()
    foreground = matte[400:624, 400:624].mean().item()
    background = matte[:150, :150].mean().item()
    print(f"BiRefNet object={foreground:.6f} background={background:.6f}")
    assert foreground > 0.9 and background < 0.1


@pytest.mark.parametrize("device_name", ["directml:0", "openvino:gpu", "openvino:npu"])
def test_onnx_devices_run_birefnet_in_pytorch_on_cpu(monkeypatch, device_name):
    from types import SimpleNamespace

    import torch

    from localsr.core import inference
    from localsr.core.inference import InferenceEngine

    calls = []

    class Adapter:
        def load(self, path, device, precision, **kwargs):
            calls.append((device.type, precision, kwargs))
            return SimpleNamespace(), None

    route = SimpleNamespace(kind="NPU" if device_name.endswith("npu") else "GPU", label=device_name)
    monkeypatch.setattr(inference, "_onnx_route", lambda _device: route)
    engine = InferenceEngine(Adapter())
    device, precision = engine.load_model(
        "background.safetensors",
        device_name,
        "fp16",
        SimpleNamespace(architecture="BiRefNet", half_supported=False),
    )
    assert device.type == "cpu" and precision == torch.float32
    assert calls == [("cpu", torch.float32, {})]


def test_streamed_deformable_projection_matches_upstream_concatenation():
    import torch
    import torch.nn.functional as F

    from localsr.image_models.birefnet.components import ASPPDeformable

    torch.manual_seed(7)
    module = ASPPDeformable(4).eval()
    image = torch.randn(1, 4, 8, 8)
    with torch.inference_mode():
        actual = module(image)
        branches = [module.aspp1(image), *(branch(image) for branch in module.aspp_deforms)]
        pooled = F.interpolate(
            module.global_avg_pool(image),
            size=image.shape[-2:],
            mode="bilinear",
            align_corners=True,
        )
        expected = module.dropout(
            module.relu(module.bn1(module.conv1(torch.cat((*branches, pooled), 1))))
        )
    torch.testing.assert_close(actual, expected, atol=1e-6, rtol=1e-5)


def test_background_handler_checks_cancellation_before_and_after_inference(monkeypatch):
    from types import SimpleNamespace

    import torch

    from localsr.worker import image_job

    monkeypatch.setattr(image_job, "BIREFNET_INPUT_SIZE", 32)
    checks = []
    runner = SimpleNamespace(_check_cancelled=lambda: checks.append("check"))
    model = image_job.StepModel("", None, lambda x: x[:, :1], torch.device("cpu"), torch.float32)
    result = image_job._run_background_stage(
        runner, {"tensor": torch.ones((3, 5, 7))}, None, 0, {}, None, [], model
    )
    assert checks == ["check", "check"]
    assert result.shape == (5, 7)
    assert bool(torch.all((result >= 0) & (result <= 1)))
