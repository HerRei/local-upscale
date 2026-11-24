"""Own PULSE checkpoints load through the vendored architecture."""

from __future__ import annotations

import importlib.util
import threading
from pathlib import Path

import numpy as np
import pytest
import spandrel
import torch
from PIL import Image
from safetensors.torch import load_file, save_file

from localsr.core.image_io import ImageManager
from localsr.core.inference import InferenceEngine
from localsr.core.model_adapter import MODEL_REGISTRY, CheckpointSecurityError, ModelAdapter
from localsr.image_models.pulse import PULSE

ROOT = Path(__file__).resolve().parents[1]
OWN_DIR = ROOT / ".own"


def _randomize_weights(network: torch.nn.Module) -> torch.nn.Module:
    torch.manual_seed(0)
    with torch.no_grad():
        for parameter in network.parameters():
            parameter.normal_(0, 0.05)
    return network.eval()


def tiny_pulse(**overrides) -> PULSE:
    config = dict(dim=16, scale=4, n_blocks=2)
    config.update(overrides)
    return _randomize_weights(PULSE(**config))  # type: ignore[return-value]


@pytest.fixture(scope="module")
def checkpoints(tmp_path_factory):
    folder = tmp_path_factory.mktemp("pulse")
    network = tiny_pulse()
    state_dict = network.state_dict()
    pth = folder / "own-pulse.pth"
    torch.save(state_dict, pth)
    safetensors = folder / "own-pulse.safetensors"
    save_file({key: value.contiguous() for key, value in state_dict.items()}, str(safetensors))
    return network, pth, safetensors


def test_spandrel_alone_does_not_know_pulse_and_registry_stays_unchanged(checkpoints):
    network, _, _ = checkpoints
    with pytest.raises(spandrel.UnsupportedModelError):
        spandrel.ModelLoader().load_from_state_dict(network.state_dict())
    assert "PULSE" not in spandrel.MAIN_REGISTRY
    assert "PULSE" in MODEL_REGISTRY


@pytest.mark.parametrize(
    ("overrides", "scale", "purpose"),
    [
        ({}, 4, "SR"),
        ({"dim": 12, "n_blocks": 3, "scale": 2}, 2, "SR"),
        ({"dim": 8, "n_blocks": 1, "scale": 3}, 3, "SR"),
        ({"dim": 8, "n_blocks": 1, "scale": 1}, 1, "Restoration"),
    ],
)
def test_detection_rebuilds_each_pulse_variant(overrides, scale, purpose):
    network = tiny_pulse(**overrides)
    descriptor = spandrel.ModelLoader(registry=MODEL_REGISTRY).load_from_state_dict(
        network.state_dict()
    )
    assert descriptor.architecture.id == "PULSE"
    assert descriptor.scale == scale
    assert descriptor.purpose == purpose
    assert descriptor.supports_half is True
    assert descriptor.input_channels == 3
    assert descriptor.output_channels == 3

    image = torch.rand(1, 3, 17, 23)
    with torch.no_grad():
        expected = network(image)
    np.testing.assert_allclose(
        descriptor.model.eval()(image).detach().numpy(), expected.numpy(), rtol=1e-5, atol=1e-6
    )


def test_pth_needs_the_same_approval_as_other_pickle_checkpoints(checkpoints):
    _, pth, _ = checkpoints
    with pytest.raises(CheckpointSecurityError, match="blocks unverified"):
        ModelAdapter(allow_unverified_checkpoints=False).inspect(str(pth))
    info = ModelAdapter(allow_unverified_checkpoints=True).inspect(str(pth))
    assert info.architecture == "PULSE"
    assert info.scale == 4
    assert info.half_supported is True
    assert any("Security warning" in warning for warning in info.warnings)


def test_safetensors_loads_without_approval(checkpoints):
    _, _, safetensors = checkpoints
    info = ModelAdapter(allow_unverified_checkpoints=False).inspect(str(safetensors))
    assert info.architecture == "PULSE"
    assert info.scale == 4
    assert info.half_supported is True
    assert info.tiling_supported is True
    assert info.warnings == []


def test_one_cpu_tile_through_the_engine_matches_the_network(checkpoints, tmp_path):
    network, _, safetensors = checkpoints
    adapter = ModelAdapter(allow_unverified_checkpoints=False)
    info = adapter.inspect(str(safetensors))
    rng = np.random.default_rng(1)
    source = rng.integers(0, 256, (32, 32, 3), dtype=np.uint8)
    input_path = str(tmp_path / "small.png")
    Image.fromarray(source).save(input_path)
    writer = InferenceEngine(adapter).process_image(
        img_data=ImageManager().load(input_path),
        model_info=info,
        model_path=str(safetensors),
        device_str="cpu",
        precision_str="fp32",
        tile_size=128,
        halo=0,
        cancel_event=threading.Event(),
        progress_callback=lambda *_: None,
        safe_memory=False,
    )
    try:
        actual = np.array(writer.get_array())
    finally:
        writer.cleanup()
    assert actual.shape == (3, 128, 128)
    with torch.no_grad():
        image = torch.from_numpy(source).permute(2, 0, 1).unsqueeze(0).float() / 255
        expected = network(image).clamp(0, 1)[0].mul(255).round().to(torch.uint8).numpy()
    assert np.abs(actual.astype(int) - expected.astype(int)).max() <= 1


@pytest.mark.skipif(
    not (OWN_DIR / "pulse_arch.py").exists()
    or not (OWN_DIR / "localsr-quick-x4-v1.safetensors").exists()
    or not (OWN_DIR / "localsr-best-x4-v1.safetensors").exists(),
    reason="local .own/ reference files are not present",
)
@pytest.mark.parametrize(
    ("filename", "dim", "n_blocks"),
    [
        ("localsr-quick-x4-v1.safetensors", 64, 8),
        ("localsr-best-x4-v1.safetensors", 96, 12),
    ],
)
def test_vendored_pulse_loads_real_files_and_matches_reference(filename, dim, n_blocks):
    spec = importlib.util.spec_from_file_location("own_pulse_arch", OWN_DIR / "pulse_arch.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    path = OWN_DIR / filename
    descriptor = spandrel.ModelLoader(registry=MODEL_REGISTRY).load_from_file(str(path))
    assert descriptor.architecture.id == "PULSE"
    assert descriptor.scale == 4

    reference = module.PULSE(dim=dim, scale=4, n_blocks=n_blocks).eval()
    reference.load_state_dict(load_file(str(path)), strict=True)

    sample = torch.rand(1, 3, 24, 24)
    with torch.no_grad():
        actual = descriptor.model.eval()(sample)
        expected = reference(sample)
    torch.testing.assert_close(actual, expected, rtol=0, atol=0)
