"""Own NERVE and HEART checkpoints (#154) load through the vendored architectures."""

import os
import threading

import numpy as np
import pytest
import spandrel
import torch
from PIL import Image
from safetensors.torch import save_file

from localsr.core.coreml_runtime import COMPUTE_UNITS
from localsr.core.image_io import ImageManager
from localsr.core.inference import InferenceEngine
from localsr.core.model_adapter import MODEL_REGISTRY, CheckpointSecurityError, ModelAdapter
from localsr.core.onnx_runtime import NPU_ARCHITECTURES
from localsr.image_models.heart import HEART
from localsr.image_models.nerve import NERVE


def _randomize_weights(network: torch.nn.Module) -> torch.nn.Module:
    torch.manual_seed(0)
    with torch.no_grad():
        for parameter in network.parameters():
            parameter.normal_(0, 0.05)
    return network.eval()


def tiny_nerve(**overrides) -> NERVE:
    config = dict(
        in_ch=3,
        out_ch=3,
        dim=8,
        n_blocks=2,
        upscale=2,
    )
    config.update(overrides)
    return _randomize_weights(NERVE(**config))  # type: ignore[return-value]


def tiny_heart(**overrides) -> HEART:
    config = dict(
        img_size=64,
        embed_dim=60,
        depths=(2, 2),
        num_heads=(6, 6),
        window_size=32,
        num_feat=16,
        upscale=2,
    )
    config.update(overrides)
    return _randomize_weights(HEART(**config))  # type: ignore[return-value]


@pytest.fixture(scope="module")
def checkpoints(tmp_path_factory):
    folder = tmp_path_factory.mktemp("nerve_heart")
    built = {}
    for name, factory in (("nerve", tiny_nerve), ("heart", tiny_heart)):
        network = factory()
        state_dict = network.state_dict()
        pth = folder / f"own-{name}.pth"
        torch.save(state_dict, pth)
        safetensors = folder / f"own-{name}.safetensors"
        save_file({key: value.contiguous() for key, value in state_dict.items()}, str(safetensors))
        built[name] = (network, pth, safetensors)
    return built


def test_spandrel_alone_does_not_know_nerve_or_heart_and_registry_stays_unchanged(checkpoints):
    for arch_id, (network, _, _) in (
        ("NERVE", checkpoints["nerve"]),
        ("HEART", checkpoints["heart"]),
    ):
        with pytest.raises(spandrel.UnsupportedModelError):
            spandrel.ModelLoader().load_from_state_dict(network.state_dict())
        assert arch_id not in spandrel.MAIN_REGISTRY
        assert arch_id in MODEL_REGISTRY

    own = [support.architecture.id for support in spandrel.MAIN_REGISTRY.architectures("detection")]
    ours = [support.architecture.id for support in MODEL_REGISTRY.architectures("detection")]
    assert ours == [*own, "NERVE", "HEART"]


@pytest.mark.parametrize(
    ("overrides", "scale", "purpose"),
    [
        ({}, 2, "SR"),
        ({"in_ch": 1, "out_ch": 1, "upscale": 2}, 2, "SR"),
        ({"dim": 16, "n_blocks": 3, "upscale": 3}, 3, "SR"),
        ({"upscale": 4}, 4, "SR"),
        ({"n_blocks": 1, "upscale": 1}, 1, "Restoration"),
    ],
)
def test_detection_rebuilds_each_nerve_variant(overrides, scale, purpose):
    network = tiny_nerve(**overrides)
    descriptor = spandrel.ModelLoader(registry=MODEL_REGISTRY).load_from_state_dict(
        network.state_dict()
    )
    assert descriptor.architecture.id == "NERVE"
    assert descriptor.scale == scale
    assert descriptor.purpose == purpose
    assert descriptor.supports_half is True
    assert descriptor.input_channels == overrides.get("in_ch", 3)
    assert descriptor.output_channels == overrides.get("out_ch", 3)

    in_ch = overrides.get("in_ch", 3)
    image = torch.rand(1, in_ch, 17, 23)
    with torch.no_grad():
        expected = network(image)
    np.testing.assert_array_equal(descriptor.model.eval()(image).detach().numpy(), expected.numpy())


@pytest.mark.parametrize(
    ("overrides", "scale", "purpose"),
    [
        ({}, 2, "SR"),
        ({"in_chans": 1, "upscale": 2}, 2, "SR"),
        ({"upscale": 3}, 3, "SR"),
        ({"upscale": 4}, 4, "SR"),
        ({"upscale": 1}, 1, "Restoration"),
        (
            {
                "depths": (2, 2, 2),
                "num_heads": (6, 6, 6),
                "rib_hidden_dim": 16,
                "mlp_ratio": 4.0,
                "ape": True,
                "patch_norm": False,
                "resi_connection": "identity",
                "compress_ratio": 6,
                "squeeze_factor": 15,
                "upscale": 4,
            },
            4,
            "SR",
        ),
    ],
)
def test_detection_rebuilds_each_heart_variant(overrides, scale, purpose):
    network = tiny_heart(**overrides)
    descriptor = spandrel.ModelLoader(registry=MODEL_REGISTRY).load_from_state_dict(
        network.state_dict()
    )
    assert descriptor.architecture.id == "HEART"
    assert descriptor.scale == scale
    assert descriptor.purpose == purpose
    assert descriptor.supports_half is True
    assert descriptor.size_requirements.multiple_of == 32
    in_ch = overrides.get("in_chans", 3)
    assert descriptor.input_channels == in_ch
    assert descriptor.output_channels == in_ch

    image = torch.rand(1, in_ch, 64, 64) if overrides.get("ape") else torch.rand(1, in_ch, 21, 30)
    with torch.no_grad():
        expected = network(image)
    np.testing.assert_allclose(
        descriptor.model.eval()(image).detach().numpy(), expected.numpy(), rtol=1e-5, atol=1e-6
    )


@pytest.mark.parametrize(("key", "arch"), [("nerve", "NERVE"), ("heart", "HEART")])
def test_pth_needs_the_same_approval_as_other_pickle_checkpoints(checkpoints, key, arch):
    _, pth, _ = checkpoints[key]
    with pytest.raises(CheckpointSecurityError, match="blocks unverified"):
        ModelAdapter(allow_unverified_checkpoints=False).inspect(str(pth))
    info = ModelAdapter(allow_unverified_checkpoints=True).inspect(str(pth))
    assert info.architecture == arch
    assert info.scale == 2
    assert info.half_supported is True
    assert any("Security warning" in warning for warning in info.warnings)


@pytest.mark.parametrize(("key", "arch", "mult"), [("nerve", "NERVE", 1), ("heart", "HEART", 32)])
def test_safetensors_loads_without_approval_and_reports_routing_contract(
    checkpoints, key, arch, mult
):
    _, _, safetensors = checkpoints[key]
    info = ModelAdapter(allow_unverified_checkpoints=False).inspect(str(safetensors))
    assert info.architecture == arch
    assert info.scale == 2
    assert info.half_supported is True
    assert info.tiling_supported is True
    assert info.size_requirements_mult % mult == 0
    assert info.warnings == []
    assert arch not in COMPUTE_UNITS
    assert arch not in NPU_ARCHITECTURES


def run_one_tile(path: str, source: np.ndarray, tmp_path, halo: int) -> np.ndarray:
    adapter = ModelAdapter(allow_unverified_checkpoints=True)
    info = adapter.inspect(path)
    input_path = str(tmp_path / f"small-{source.shape[0]}x{source.shape[1]}.png")
    Image.fromarray(source).save(input_path)
    writer = InferenceEngine(adapter).process_image(
        img_data=ImageManager().load(input_path),
        model_info=info,
        model_path=path,
        device_str="cpu",
        precision_str="fp32",
        tile_size=128,
        halo=halo,
        cancel_event=threading.Event(),
        progress_callback=lambda *_: None,
        safe_memory=False,
    )
    try:
        # A copy: the writer's array is a view of storage that cleanup() releases.
        return np.array(writer.get_array())
    finally:
        writer.cleanup()


@pytest.mark.parametrize("key", ["nerve", "heart"])
@pytest.mark.parametrize("which", ["pth", "safetensors"])
def test_one_cpu_tile_through_the_engine_matches_the_network(checkpoints, tmp_path, key, which):
    network, pth, safetensors = checkpoints[key]
    path = str(pth if which == "pth" else safetensors)
    rng = np.random.default_rng(1)

    odd = rng.integers(0, 256, (29, 37, 3), dtype=np.uint8)
    actual_odd = run_one_tile(path, odd, tmp_path, halo=8)
    assert actual_odd.shape == (3, 58, 74)
    assert len(np.unique(actual_odd)) > 16

    # Multiple of HEART's 32px window and valid for NERVE: reaches the network unpadded.
    aligned = rng.integers(0, 256, (32, 32, 3), dtype=np.uint8)
    actual = run_one_tile(path, aligned, tmp_path, halo=0)
    assert actual.shape == (3, 64, 64)
    with torch.no_grad():
        image = torch.from_numpy(aligned).permute(2, 0, 1).unsqueeze(0).float() / 255
        expected = network(image).clamp(0, 1)[0].mul(255).round().to(torch.uint8).numpy()
    assert np.abs(actual.astype(int) - expected.astype(int)).max() <= 1


@pytest.mark.parametrize("key", ["nerve", "heart"])
def test_graph_exports_for_the_onnx_gpu_routes(checkpoints, key):
    if not os.environ.get("CI"):
        pytest.importorskip("onnx")
        pytest.importorskip("onnxruntime")
    from localsr.core.onnx_runtime import OnnxImageDescriptor

    network, _, safetensors = checkpoints[key]
    descriptor = spandrel.ModelLoader(registry=MODEL_REGISTRY).load_from_file(str(safetensors))
    descriptor.eval()
    converted = OnnxImageDescriptor(descriptor, provider="CPUExecutionProvider")
    image = torch.rand(1, 3, 32, 32)
    with torch.no_grad():
        expected = network(image).clamp(0, 1)
    np.testing.assert_allclose(converted(image).numpy(), expected.numpy(), atol=1e-5)
    assert converted.execution_verified
