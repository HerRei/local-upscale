import os
import threading

import numpy as np
import pytest
import spandrel
import torch
from PIL import Image, ImageDraw
from spandrel.architectures.HAT import HAT

from localsr.core.image_io import ImageManager
from localsr.core.inference import InferenceEngine
from localsr.core.model_adapter import ModelAdapter


@pytest.fixture(scope="session")
def hat_checkpoint_path(tmp_path_factory):
    """
    Creates a lightweight PyTorch HAT state_dict checkpoint saved as a .pth file.
    The model uses embed_dim=16, window_size=16, upscale=2 to keep inference fast (~1.8 MB checkpoint).
    """
    tmp_dir = tmp_path_factory.mktemp("hat_fixture")
    pth_path = str(tmp_dir / "hat_2x.pth")

    model = HAT(
        img_size=64,
        patch_size=1,
        in_chans=3,
        embed_dim=16,
        depths=[1],
        num_heads=[1],
        window_size=16,
        compress_ratio=2,
        squeeze_factor=2,
        conv_scale=0.01,
        overlap_ratio=0.5,
        mlp_ratio=2.0,
        qkv_bias=True,
        upscale=2,
        upsampler="pixelshuffle",
        resi_connection="1conv",
        num_feat=16,
    )

    state_dict = model.state_dict()
    torch.save(state_dict, pth_path)
    yield pth_path


def test_f5_1_hat_checkpoint_loading(hat_checkpoint_path):
    """
    F5.1: Verifies loading a programmatically constructed PyTorch HAT state_dict checkpoint
    via Spandrel ModelLoader / ModelAdapter under architecture "HAT".
    """
    # Direct Spandrel loading
    loader = spandrel.ModelLoader()
    parsed = loader.load_from_file(hat_checkpoint_path)
    assert parsed.architecture.name == "HAT"
    assert parsed.scale == 2
    assert getattr(parsed, "supports_half", False) is False

    # ModelAdapter inspect
    adapter = ModelAdapter(allow_unverified_checkpoints=True)
    info = adapter.inspect(hat_checkpoint_path)
    assert info.architecture == "HAT"
    assert info.scale == 2
    assert info.in_channels == 3
    assert info.out_channels == 3
    assert info.filename == os.path.basename(hat_checkpoint_path)


def test_f5_2_hat_tile_shape_divisibility(hat_checkpoint_path, tmp_path):
    """
    F5.2: Verification of size_requirements_mult == 16 for window_size=16 cross-attention compatibility.
    Verifies that tile shape divisibility handling pads input tiles to multiples of 16 during processing
    and crops output back to exact expected 2x dimensions.
    """
    adapter = ModelAdapter(allow_unverified_checkpoints=True)
    info = adapter.inspect(hat_checkpoint_path)
    assert info.size_requirements_mult == 16, (
        f"Expected mult 16 for HAT, got {info.size_requirements_mult}"
    )

    # Test processing an image with non-divisible dimensions (e.g., 47x33)
    engine = InferenceEngine(adapter)
    im_mgr = ImageManager()

    input_path = str(tmp_path / "odd_shape.png")
    img = Image.new("RGB", (33, 47), color=(100, 150, 200))
    img.save(input_path)

    img_data = im_mgr.load(input_path)
    cancel_event = threading.Event()

    writer = engine.process_image(
        img_data=img_data,
        model_info=info,
        model_path=hat_checkpoint_path,
        device_str="cpu",
        precision_str="fp32",
        tile_size=64,
        halo=8,
        cancel_event=cancel_event,
        progress_callback=lambda c, t, s: None,
        safe_memory=False,
    )

    arr = writer.get_array()
    # Expected output dimensions: (3, 47*2, 33*2) = (3, 94, 66)
    assert arr.shape == (3, 94, 66), f"Expected shape (3, 94, 66), got {arr.shape}"
    assert arr.dtype == np.uint8
    assert not np.isnan(arr).any()
    writer.cleanup()


def test_f5_3_synthetic_upscaling_32_and_64(hat_checkpoint_path, tmp_path):
    """
    F5.3: Synthetic 32x32 and 64x64 image 2x upscaling verification pass.
    Confirms architecture detection, exact 2x output tensor/array shape, non-NaN uint8 valid pixels,
    and successful Pillow RGB image opening.
    """
    adapter = ModelAdapter(allow_unverified_checkpoints=True)
    info = adapter.inspect(hat_checkpoint_path)
    assert info.architecture == "HAT"
    assert info.scale == 2

    engine = InferenceEngine(adapter)
    im_mgr = ImageManager()

    # 1. 32x32 -> 64x64
    in_32_path = str(tmp_path / "synth_32.png")
    out_64_path = str(tmp_path / "out_64.png")
    Image.new("RGB", (32, 32), color="red").save(in_32_path)

    img_data_32 = im_mgr.load(in_32_path)
    writer_32 = engine.process_image(
        img_data=img_data_32,
        model_info=info,
        model_path=hat_checkpoint_path,
        device_str="cpu",
        precision_str="fp32",
        tile_size=64,
        halo=8,
        cancel_event=threading.Event(),
        progress_callback=lambda c, t, s: None,
        safe_memory=False,
    )

    arr_32 = writer_32.get_array()
    assert arr_32.shape == (3, 64, 64), (
        f"32x32 upscaled array must be (3, 64, 64), got {arr_32.shape}"
    )
    assert arr_32.dtype == np.uint8
    assert not np.isnan(arr_32).any()
    assert arr_32.min() >= 0 and arr_32.max() <= 255

    im_mgr.save(writer_32, out_64_path, format="png")
    writer_32.cleanup()

    assert os.path.exists(out_64_path)
    with Image.open(out_64_path) as img_out_64:
        assert img_out_64.size == (64, 64)
        assert img_out_64.mode == "RGB"

    # 2. 64x64 -> 128x128
    in_64_path = str(tmp_path / "synth_64.png")
    out_128_path = str(tmp_path / "out_128.png")
    Image.new("RGB", (64, 64), color="green").save(in_64_path)

    img_data_64 = im_mgr.load(in_64_path)
    writer_64 = engine.process_image(
        img_data=img_data_64,
        model_info=info,
        model_path=hat_checkpoint_path,
        device_str="cpu",
        precision_str="fp32",
        tile_size=64,
        halo=8,
        cancel_event=threading.Event(),
        progress_callback=lambda c, t, s: None,
        safe_memory=False,
    )

    arr_64 = writer_64.get_array()
    assert arr_64.shape == (3, 128, 128), (
        f"64x64 upscaled array must be (3, 128, 128), got {arr_64.shape}"
    )
    assert arr_64.dtype == np.uint8
    assert not np.isnan(arr_64).any()
    assert arr_64.min() >= 0 and arr_64.max() <= 255

    im_mgr.save(writer_64, out_128_path, format="png")
    writer_64.cleanup()

    assert os.path.exists(out_128_path)
    with Image.open(out_128_path) as img_out_128:
        assert img_out_128.size == (128, 128)
        assert img_out_128.mode == "RGB"


def test_f5_4_real_photograph_500x500_upscaling(hat_checkpoint_path, tmp_path):
    """
    F5.4: Real photograph (~500x500) 2x upscaling verification pass with conservative tile configuration
    (tile_size=128, halo=16). Confirm exact 2x output dimensions (~1000x1000), valid pixels, and clean .dat
    memmap file deletion upon cleanup.
    """
    adapter = ModelAdapter(allow_unverified_checkpoints=True)
    info = adapter.inspect(hat_checkpoint_path)
    engine = InferenceEngine(adapter)
    im_mgr = ImageManager()

    # Create a 500x500 photograph-like synthetic test image
    photo_in_path = str(tmp_path / "photo_500.png")
    photo_out_path = str(tmp_path / "photo_1000.png")

    img = Image.new("RGB", (500, 500), color=(50, 100, 150))
    draw = ImageDraw.Draw(img)
    draw.rectangle([50, 50, 250, 250], fill=(200, 50, 50), outline=(255, 255, 255))
    draw.ellipse([200, 200, 450, 450], fill=(50, 200, 50), outline=(0, 0, 0))
    draw.line([0, 0, 500, 500], fill=(255, 255, 0), width=6)
    img.save(photo_in_path)

    img_data = im_mgr.load(photo_in_path)
    cancel_event = threading.Event()

    writer = engine.process_image(
        img_data=img_data,
        model_info=info,
        model_path=hat_checkpoint_path,
        device_str="cpu",
        precision_str="fp32",
        tile_size=128,
        halo=16,
        cancel_event=cancel_event,
        progress_callback=lambda c, t, s: None,
        safe_memory=False,
    )

    memmap_path = writer.get_path()
    assert os.path.exists(memmap_path), f"Memmap file {memmap_path} must exist during processing"

    arr = writer.get_array()
    assert arr.shape == (3, 1000, 1000), (
        f"500x500 photo 2x upscale must yield (3, 1000, 1000), got {arr.shape}"
    )
    assert arr.dtype == np.uint8
    assert not np.isnan(arr).any()
    assert arr.min() >= 0 and arr.max() <= 255

    im_mgr.save(writer, photo_out_path, format="png")
    writer.cleanup()

    # Verify clean .dat memmap deletion
    assert not os.path.exists(memmap_path), (
        f"Memmap file {memmap_path} must be deleted after cleanup()"
    )

    # Verify saved image file and Pillow reading
    assert os.path.exists(photo_out_path)
    with Image.open(photo_out_path) as out_img:
        assert out_img.size == (1000, 1000), (
            f"Expected PIL image size (1000, 1000), got {out_img.size}"
        )
        assert out_img.mode == "RGB"
