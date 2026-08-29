import os
import threading

import numpy as np
import pytest
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
    The model uses embed_dim=16, window_size=16, upscale=2.
    """
    tmp_dir = tmp_path_factory.mktemp("hat_adv_fixture")
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


@pytest.mark.parametrize(
    "width,height",
    [
        (17, 17),
        (33, 47),
        (501, 499),
        (3, 3),
        (1, 1),
    ],
)
def test_hat_odd_and_non_divisible_dimensions(hat_checkpoint_path, tmp_path, width, height):
    """
    Stress-test HAT upscaling on odd and non-divisible image dimensions.
    Verifies padding alignment, tile output cropping, and exact 2x output shape.
    """
    adapter = ModelAdapter(allow_unverified_checkpoints=True)
    info = adapter.inspect(hat_checkpoint_path)
    engine = InferenceEngine(adapter)
    im_mgr = ImageManager()

    input_path = str(tmp_path / f"input_{width}x{height}.png")
    output_path = str(tmp_path / f"output_{width * 2}x{height * 2}.png")

    # Generate synthetic image pattern
    img = Image.new("RGB", (width, height), color=(128, 64, 32))
    if width > 4 and height > 4:
        draw = ImageDraw.Draw(img)
        draw.line([0, 0, width, height], fill=(255, 255, 0), width=1)
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

    memmap_path = writer.get_path()
    assert os.path.exists(memmap_path), f"Memmap file {memmap_path} must exist before cleanup"

    arr = writer.get_array()
    expected_shape = (3, height * 2, width * 2)
    assert arr.shape == expected_shape, f"Expected shape {expected_shape}, got {arr.shape}"

    # Pixel sanity checks
    assert arr.dtype == np.uint8, f"Expected uint8 dtype, got {arr.dtype}"
    assert not np.isnan(arr).any(), "Output array contains NaN values"
    assert not np.isinf(arr).any(), "Output array contains Inf values"
    assert arr.min() >= 0 and arr.max() <= 255, (
        f"Pixel values out of uint8 bounds: min={arr.min()}, max={arr.max()}"
    )

    im_mgr.save(writer, output_path, format="png")
    writer.cleanup()

    # Lifecycle check: memmap file deleted after cleanup
    assert not os.path.exists(memmap_path), (
        f"Memmap file {memmap_path} should be deleted after cleanup"
    )

    # Image file sanity check
    assert os.path.exists(output_path)
    with Image.open(output_path) as out_img:
        assert out_img.size == (width * 2, height * 2), (
            f"Expected PIL size {(width * 2, height * 2)}, got {out_img.size}"
        )
        assert out_img.mode == "RGB"


@pytest.mark.parametrize(
    "tile_size,halo,img_w,img_h",
    [
        (32, 8, 501, 499),
        (64, 16, 501, 499),
        (32, 16, 17, 17),
        (16, 8, 33, 47),
    ],
)
def test_hat_custom_and_extreme_tile_configs(
    hat_checkpoint_path, tmp_path, tile_size, halo, img_w, img_h
):
    """
    Stress-test HAT model upscaling with custom and extreme tile sizes and halo configurations.
    """
    adapter = ModelAdapter(allow_unverified_checkpoints=True)
    info = adapter.inspect(hat_checkpoint_path)
    engine = InferenceEngine(adapter)
    im_mgr = ImageManager()

    input_path = str(tmp_path / f"tile_test_{img_w}x{img_h}_t{tile_size}_h{halo}.png")
    img = Image.new("RGB", (img_w, img_h), color=(200, 100, 50))
    img.save(input_path)

    img_data = im_mgr.load(input_path)
    cancel_event = threading.Event()

    writer = engine.process_image(
        img_data=img_data,
        model_info=info,
        model_path=hat_checkpoint_path,
        device_str="cpu",
        precision_str="fp32",
        tile_size=tile_size,
        halo=halo,
        cancel_event=cancel_event,
        progress_callback=lambda c, t, s: None,
        safe_memory=False,
    )

    arr = writer.get_array()
    expected_shape = (3, img_h * 2, img_w * 2)
    assert arr.shape == expected_shape, f"Expected shape {expected_shape}, got {arr.shape}"
    assert arr.dtype == np.uint8
    assert not np.isnan(arr).any()
    assert not np.isinf(arr).any()
    assert arr.min() >= 0 and arr.max() <= 255

    writer.cleanup()


def test_hat_memmap_file_lifecycle_and_cancellation(hat_checkpoint_path, tmp_path):
    """
    Verify memmap temporary file lifecycle:
    1. Memmap file exists during processing and has correct byte size.
    2. Calling writer.cleanup() removes the memmap file.
    3. Cancellation during inference properly triggers cleanup and raises InterruptedError.
    """
    adapter = ModelAdapter(allow_unverified_checkpoints=True)
    info = adapter.inspect(hat_checkpoint_path)
    engine = InferenceEngine(adapter)
    im_mgr = ImageManager()

    # Normal lifecycle test
    input_path = str(tmp_path / "lifecycle_input.png")
    img = Image.new("RGB", (100, 100), color=(10, 20, 30))
    img.save(input_path)
    img_data = im_mgr.load(input_path)

    writer = engine.process_image(
        img_data=img_data,
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

    memmap_path = writer.get_path()
    assert os.path.exists(memmap_path), "Memmap file must exist during active processing"
    expected_bytes = 3 * (100 * 2) * (100 * 2)  # uint8 -> 1 byte per element
    assert os.path.getsize(memmap_path) == expected_bytes, (
        f"Expected memmap size {expected_bytes}, got {os.path.getsize(memmap_path)}"
    )

    writer.cleanup()
    assert not os.path.exists(memmap_path), "Memmap file must be deleted upon cleanup()"

    # Cancellation test
    cancel_event = threading.Event()

    def cancel_after_first_tile(c, t, s):
        if c >= 1:
            cancel_event.set()

    with pytest.raises(InterruptedError):
        engine.process_image(
            img_data=img_data,
            model_info=info,
            model_path=hat_checkpoint_path,
            device_str="cpu",
            precision_str="fp32",
            tile_size=32,  # Multiple tiles to allow cancellation after tile 1
            halo=8,
            cancel_event=cancel_event,
            progress_callback=cancel_after_first_tile,
            safe_memory=False,
        )
