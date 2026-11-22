"""DDColor loader, Lab colorization stage, device routing, and catalog tests."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import torch
from PIL import Image

from localsr.core.image_io import ImageManager
from localsr.core.inference import InferenceEngine
from localsr.core.model_adapter import CheckpointSecurityError, ModelAdapter
from localsr.core.model_catalog import MODEL_CATALOG, ModelPurpose
from localsr.core.pipeline import parse_pipeline_stages, resolve_image_pipeline
from localsr.image_models.ddcolor import (
    ALREADY_COLORFUL_NOTE,
    DDColor,
    colorize_rgb_array,
    infer_ddcolor_config,
    is_ddcolor_state_dict,
    lab_to_rgb,
    load_ddcolor_checkpoint,
    load_ddcolor_descriptor,
    mean_lab_chroma,
    rgb_to_lab,
)
from localsr.worker.image_job import ImageJobRunner
from localsr.worker.server import WorkerServer


def _small_ddcolor() -> DDColor:
    return DDColor(
        encoder_name="convnext-t",
        decoder_name="MultiScaleColorDecoder",
        num_input_channels=3,
        input_size=(64, 64),
        nf=32,
        num_output_channels=2,
        last_norm="Spectral",
        do_normalize=False,
        num_queries=8,
        num_scales=3,
        dec_layers=2,
        depths=(1, 1, 1, 1),
        dims=(16, 32, 64, 128),
        hidden_dim=16,
        nheads=4,
        dim_feedforward=32,
    )


def _save_small_checkpoint(path: Path, *, wrapped: bool = True) -> Path:
    model = _small_ddcolor().eval()
    state = model.state_dict()
    payload = {"params": state} if wrapped else state
    torch.save(payload, path)
    return path


def test_small_ddcolor_loads_weights_only_and_runs(tmp_path):
    ckpt_path = _save_small_checkpoint(tmp_path / "small_ddcolor.pth", wrapped=True)
    descriptor = load_ddcolor_checkpoint(ckpt_path, input_size=(64, 64))
    assert descriptor.architecture.name == "DDColor"
    assert descriptor.scale == 1
    assert descriptor.supports_half is False

    gray = np.full((77, 113, 3), 128, dtype=np.uint8)
    result = colorize_rgb_array(descriptor, gray, input_size=(64, 64))
    assert result.rgb.shape == (77, 113, 3)
    assert result.rgb.dtype == np.uint8
    assert result.already_colorful is False
    assert result.note is None


def test_refine_layer_matches_upstream_reference():
    from localsr.image_models.ddcolor.unet import NormType, custom_conv_layer

    # Upstream 2adb63f, pinned tiny weights, and a 64x64 lightness gradient.
    with np.load(Path(__file__).parent / "ddcolor_refine_reference.npz") as reference:
        layer = custom_conv_layer(103, 2, ks=1, use_activ=False, norm_type=NormType.Spectral)
        layer.load_state_dict(
            {
                f"0.{name}": torch.from_numpy(reference[name])
                for name in ("weight_orig", "weight_u", "weight_v", "bias")
            }
        )
        layer.eval()
        with torch.no_grad():
            output = layer(torch.from_numpy(reference["features"]))
        np.testing.assert_allclose(output.numpy(), reference["ab"], atol=1e-5, rtol=1e-5)


def test_best_ddcolor_config_matches_convnext_l_parameters():
    model = DDColor(
        encoder_name="convnext-l",
        decoder_name="MultiScaleColorDecoder",
        num_input_channels=3,
        input_size=(512, 512),
        nf=512,
        num_output_channels=2,
        last_norm="Spectral",
        do_normalize=False,
        num_queries=100,
        num_scales=3,
        dec_layers=9,
    )
    config = infer_ddcolor_config(model.state_dict())
    assert config.encoder_name == "convnext-l"
    assert config.dims == (192, 384, 768, 1536)
    assert config.depths == (3, 3, 27, 3)
    assert config.num_queries == 100
    assert config.num_scales == 3
    assert config.dec_layers == 9
    assert config.num_output_channels == 2


def test_ddcolor_checkpoint_security_follows_existing_policy(tmp_path):
    ckpt_path = _save_small_checkpoint(tmp_path / "unverified_ddcolor.pth")
    adapter = ModelAdapter(allow_unverified_checkpoints=False)
    with pytest.raises(CheckpointSecurityError, match="unverified"):
        adapter.inspect(str(ckpt_path))

    trusted_adapter = ModelAdapter(allow_unverified_checkpoints=True)
    info = trusted_adapter.inspect(str(ckpt_path))
    assert info.architecture == "DDColor"
    assert info.scale == 1
    assert info.in_channels == 3
    assert info.out_channels == 3
    assert info.tiling_supported is False
    assert info.half_supported is False

    pickle_path = tmp_path / "bad_pickle.pth"
    torch.save({"params": {"bad": object()}}, pickle_path)
    with pytest.raises(Exception):  # noqa: B017 - weights_only rejects arbitrary objects
        load_ddcolor_checkpoint(pickle_path)


def test_lab_round_trip_preserves_l_and_supports_8bit_and_16bit(monkeypatch):
    from localsr.image_models.ddcolor import engine as ddcolor_engine

    rng = np.random.default_rng(7)
    luma_8 = rng.integers(30, 225, size=(53, 89, 1), dtype=np.uint8)
    rgb_8 = np.repeat(luma_8, 3, axis=2)
    lab_in = rgb_to_lab(rgb_8.astype(np.float32) / 255.0)
    rgb_rt = lab_to_rgb(lab_in)
    lab_out = rgb_to_lab(rgb_rt)
    np.testing.assert_allclose(lab_out[:, :, :1], lab_in[:, :, :1], atol=0.05)

    captured_lab: list[np.ndarray] = []
    real_lab_to_rgb = ddcolor_engine.lab_to_rgb

    def spy_lab_to_rgb(lab: np.ndarray) -> np.ndarray:
        captured_lab.append(lab.copy())
        return real_lab_to_rgb(lab)

    monkeypatch.setattr(ddcolor_engine, "lab_to_rgb", spy_lab_to_rgb)

    descriptor = load_ddcolor_descriptor(_small_ddcolor().state_dict(), input_size=(64, 64))

    result_8 = colorize_rgb_array(descriptor, rgb_8, input_size=(64, 64))
    assert result_8.rgb.shape == rgb_8.shape
    assert result_8.rgb.dtype == np.uint8
    np.testing.assert_array_equal(captured_lab[-1][:, :, :1], lab_in[:, :, :1])

    rgb_16 = (rgb_8.astype(np.uint16)) * 257
    result_16 = colorize_rgb_array(descriptor, rgb_16, input_size=(64, 64))
    assert result_16.rgb.shape == rgb_16.shape
    assert result_16.rgb.dtype == np.uint16


def test_colorize_uses_upstream_resize_order_and_nearest_ab():
    import cv2
    import torch.nn.functional as F

    rng = np.random.default_rng(42)
    rgb = rng.integers(0, 256, (79, 103, 3), dtype=np.uint8)
    model = _small_ddcolor().eval()
    captured: list[tuple[torch.Tensor, torch.Tensor]] = []

    def capture(_module, inputs, output):
        captured.append((inputs[0].detach().clone(), output.detach().clone()))

    model.register_forward_hook(capture)
    result = colorize_rgb_array(model, rgb, input_size=(64, 64))
    rgb_float = rgb.astype(np.float32) / 255.0
    resized = cv2.resize(rgb_float, (64, 64), interpolation=cv2.INTER_LINEAR)
    lightness = cv2.cvtColor(resized, cv2.COLOR_RGB2Lab)[:, :, :1]
    gray_lab = np.concatenate(
        (lightness, np.zeros_like(lightness), np.zeros_like(lightness)), axis=-1
    )
    gray = cv2.cvtColor(gray_lab, cv2.COLOR_Lab2RGB)
    np.testing.assert_allclose(captured[0][0][0].permute(1, 2, 0).numpy(), gray, atol=1e-6)

    ab = F.interpolate(captured[0][1], size=rgb.shape[:2], mode="nearest")
    original_l = cv2.cvtColor(rgb_float, cv2.COLOR_RGB2Lab)[:, :, :1]
    lab = np.concatenate((original_l, ab[0].permute(1, 2, 0).numpy()), axis=-1)
    expected = np.rint(cv2.cvtColor(lab, cv2.COLOR_Lab2RGB) * 255).clip(0, 255).astype(np.uint8)
    np.testing.assert_array_equal(result.rgb, expected)


def test_already_colorful_photo_runs_with_note_and_no_refusal(tmp_path):
    ckpt_path = _save_small_checkpoint(tmp_path / "small_ddcolor.pth")
    descriptor = load_ddcolor_checkpoint(ckpt_path, input_size=(64, 64))

    colorful = np.zeros((64, 80, 3), dtype=np.uint8)
    colorful[:, :40] = (230, 30, 30)
    colorful[:, 40:] = (20, 180, 240)
    result = colorize_rgb_array(descriptor, colorful, input_size=(64, 64))
    assert result.rgb.shape == colorful.shape
    assert result.already_colorful is True
    assert result.note == ALREADY_COLORFUL_NOTE
    assert result.input_mean_chroma > 10.0

    in_path = tmp_path / "colorful.png"
    out_path = tmp_path / "out.png"
    Image.fromarray(colorful, mode="RGB").save(in_path)

    adapter = ModelAdapter(allow_unverified_checkpoints=True)
    engine = InferenceEngine(adapter)
    messages = []
    runner = ImageJobRunner(
        engine=engine,
        model_adapter=adapter,
        cancel_event=threading.Event(),
        emit=messages.append,
        emit_preview=lambda _pkt: None,
    )
    runner.run(
        "colorize-colorful",
        {
            "job_id": "colorize-colorful",
            "image_path": str(in_path),
            "model_path": str(ckpt_path),
            "output_path": str(out_path),
            "scratch_directory": str(tmp_path / "scratch"),
            "output_format": "png",
            "device": "cpu",
            "precision": "fp32",
            "tile_size": 128,
            "halo": 0,
            "jpeg_quality": 95,
            "preserve_metadata": True,
            "safe_memory": True,
            "output_scale": 1,
            "preview_enabled": False,
            "stages": [
                {
                    "kind": "restore",
                    "role": "primary",
                    "fix": "colour",
                    "model_id": "ddcolor_modelscope",
                    "model_path": str(ckpt_path),
                }
            ],
        },
    )
    completed = [
        json.loads(msg.to_json()) for msg in messages if msg.to_json().find("job_completed") >= 0
    ]
    assert len(completed) == 1
    assert ALREADY_COLORFUL_NOTE in completed[0]["data"]["notes"]
    assert out_path.is_file()


def test_grayscale_16bit_job_keeps_colorized_output_and_preserves_dimensions(tmp_path, monkeypatch):
    ckpt_path = _save_small_checkpoint(tmp_path / "small_ddcolor.pth")

    # Replace DDColor model output with deterministic non-zero ab chroma so
    # ImageManager.grayscale clearing is verified end-to-end.
    class _FixedAbModel(DDColor):
        def forward(self, x: torch.Tensor) -> torch.Tensor:
            out = torch.zeros((x.shape[0], 2, x.shape[2], x.shape[3]), device=x.device)
            out[:, 0] = 35.0
            out[:, 1] = 25.0
            return out

    from localsr.image_models.ddcolor import engine as dd_engine

    monkeypatch.setattr(
        dd_engine,
        "load_ddcolor_descriptor",
        lambda state_dict, input_size=(64, 64): dd_engine.DDColorDescriptor(
            _FixedAbModel(
                encoder_name="convnext-t",
                input_size=(64, 64),
                nf=32,
                num_output_channels=2,
                num_queries=8,
                dec_layers=2,
                depths=(1, 1, 1, 1),
                dims=(16, 32, 64, 128),
                hidden_dim=16,
                nheads=4,
                dim_feedforward=32,
            ),
            infer_ddcolor_config(state_dict, input_size=(64, 64)),
        ),
    )

    in_path = tmp_path / "gray16.png"
    out_path = tmp_path / "color_out.png"
    gray16 = np.full((61, 79), 32768, dtype=np.uint16)
    Image.fromarray(gray16).save(in_path)
    assert ImageManager().load(str(in_path))["grayscale"] is True

    adapter = ModelAdapter(allow_unverified_checkpoints=True)
    engine = InferenceEngine(adapter)
    messages = []
    runner = ImageJobRunner(
        engine=engine,
        model_adapter=adapter,
        cancel_event=threading.Event(),
        emit=messages.append,
        emit_preview=lambda _pkt: None,
    )
    runner.run(
        "colorize-gray16",
        {
            "job_id": "colorize-gray16",
            "image_path": str(in_path),
            "model_path": str(ckpt_path),
            "output_path": str(out_path),
            "scratch_directory": str(tmp_path / "scratch"),
            "output_format": "png",
            "device": "cpu",
            "precision": "fp32",
            "tile_size": 128,
            "halo": 0,
            "jpeg_quality": 95,
            "preserve_metadata": True,
            "safe_memory": True,
            "output_scale": 1,
            "preview_enabled": False,
            "stages": [
                {
                    "kind": "restore",
                    "role": "primary",
                    "fix": "colour",
                    "model_id": "ddcolor_modelscope",
                    "model_path": str(ckpt_path),
                }
            ],
        },
    )
    with Image.open(out_path) as saved:
        assert saved.size == (79, 61)
        arr = np.asarray(saved, dtype=np.float32) / 255.0
    out_chroma = mean_lab_chroma(rgb_to_lab(arr)[:, :, 1:])
    assert out_chroma > 10.0


def test_pipeline_accepts_colour_as_preprocess_and_1x_restore():
    stages = parse_pipeline_stages(
        [
            {
                "kind": "restore",
                "role": "fix",
                "fix": "colour",
                "model_id": "ddcolor_modelscope",
                "model_path": "/m/dd.pth",
            },
            {"kind": "upscale", "role": "primary", "model_id": "span", "model_path": "/m/span.pth"},
        ]
    )
    pipeline = resolve_image_pipeline(
        raw_stages=[
            {
                "kind": "restore",
                "role": "fix",
                "fix": "colour",
                "model_id": "ddcolor_modelscope",
                "model_path": "/m/dd.pth",
            },
            {"kind": "upscale", "role": "primary", "model_id": "span", "model_path": "/m/span.pth"},
        ],
        model_path="/m/span.pth",
        output_scale=4,
    )
    assert [s.fix for s in stages] == ["colour", None]
    assert [s.fix for s in pipeline.preprocess_stages] == ["colour"]

    passthrough = resolve_image_pipeline(
        raw_stages=[
            {
                "kind": "restore",
                "role": "fix",
                "fix": "colour",
                "model_id": "ddcolor_modelscope",
                "model_path": "/m/dd.pth",
            },
            {"kind": "upscale", "role": "primary", "model_id": "span", "model_path": "/m/span.pth"},
        ],
        model_path="/m/span.pth",
        output_scale=1,
    )
    assert passthrough.primary is None
    assert [s.fix for s in passthrough.preprocess_stages] == ["colour"]


def test_device_routing_runs_pytorch_directly_and_falls_back_to_cpu_for_onnx_routes(
    tmp_path, monkeypatch
):
    ckpt_path = _save_small_checkpoint(tmp_path / "small_ddcolor.pth")
    adapter = ModelAdapter(allow_unverified_checkpoints=True)
    engine = InferenceEngine(adapter)
    notes: list[str] = []
    engine.status_callback = notes.append
    info = adapter.inspect(str(ckpt_path))

    # DirectML / OpenVINO / QNN fall back to CPU with a route note.
    fake_route = SimpleNamespace(
        provider="DmlExecutionProvider",
        device_index=0,
        ep_device=None,
        identity="id",
        label="AMD Radeon (DirectML)",
        persist=False,
        device="directml:0",
        kind="GPU",
    )
    monkeypatch.setattr("localsr.core.inference._onnx_route", lambda _d: fake_route)
    device, precision = engine.load_model(str(ckpt_path), "directml:0", "fp16", info)
    assert device.type == "cpu"
    assert precision == torch.float32
    assert notes[-1] == "DDColor does not run on AMD Radeon (DirectML), so it runs on the CPU."

    # Apple Silicon Neural Engine route runs on PyTorch MPS GPU directly with a route note.
    if torch.backends.mps.is_available():
        notes.clear()
        monkeypatch.setattr("localsr.core.inference._onnx_route", lambda _d: None)
        engine.neural_engine_enabled = True
        mps_device, mps_precision = engine.load_model(str(ckpt_path), "mps", "fp16", info)
        assert mps_device.type == "mps"
        assert mps_precision == torch.float32
        assert (
            notes[-1] == "DDColor does not run on the Neural Engine, so it runs on the Apple GPU."
        )


def test_video_worker_rejects_colour_stage(tmp_path):
    ckpt_path = _save_small_checkpoint(tmp_path / "small_ddcolor.pth")
    server = WorkerServer()
    server.model_adapter = ModelAdapter(allow_unverified_checkpoints=True)
    with pytest.raises(ValueError, match="still images, not video"):
        server._run_video_job(
            "video-color-reject",
            {
                "job_id": "video-color-reject",
                "video_path": str(tmp_path / "in.mp4"),
                "model_path": str(tmp_path / "up.pth"),
                "output_video_path": str(tmp_path / "out.mp4"),
                "device": "cpu",
                "precision": "fp32",
                "tile_size": 128,
                "halo": 8,
                "safe_memory": True,
                "stages": [
                    {
                        "kind": "restore",
                        "role": "fix",
                        "fix": "colour",
                        "model_id": "ddcolor_modelscope",
                        "model_path": str(ckpt_path),
                    },
                    {
                        "kind": "upscale",
                        "role": "primary",
                        "model_id": "up",
                        "model_path": str(tmp_path / "up.pth"),
                    },
                ],
            },
        )


def test_catalog_contains_only_pinned_ddcolor_l():
    models = [model for model in MODEL_CATALOG if model.architecture == "DDColor"]
    assert [model.model_id for model in models] == ["ddcolor_modelscope"]
    model = models[0]
    assert model.native_scale == 1
    assert model.fixes == ("colour",)
    assert model.purposes == (ModelPurpose.COLORIZE, ModelPurpose.PHOTO)
    assert model.sha256 == "d81711971ec59200da26d5e8a1afae8dd3778d495ea8ad7a7dadc769f403f7e7"
    assert "060f67494e31883a4b13cb27f889f3154847ada4" in model.download_url
    assert is_ddcolor_state_dict(_small_ddcolor().state_dict())
