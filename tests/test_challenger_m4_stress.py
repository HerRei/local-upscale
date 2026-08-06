import glob
import os
import tempfile
import threading
from unittest.mock import patch

import numpy as np
import pytest
import torch
from PIL import Image
from spandrel.architectures.HAT import HAT

from localsr.core.image_io import ImageManager
from localsr.core.inference import InferenceEngine
from localsr.core.model_adapter import ModelAdapter
from localsr.core.output_writer import OutputWriter
from localsr.worker.server import WorkerServer


@pytest.fixture(scope="session")
def hat_checkpoint_path(tmp_path_factory):
    """
    Lightweight PyTorch HAT checkpoint for adversarial testing.
    """
    tmp_dir = tmp_path_factory.mktemp("hat_challenger_fixture")
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


def count_temp_dat_files():
    pattern = os.path.join(tempfile.gettempdir(), "localsr_*.dat")
    return len(glob.glob(pattern))


def test_outputwriter_init_failure_cleanup():
    """
    Test that if OutputWriter raises an exception during np.memmap initialization,
    the temporary file created by tempfile.mkstemp is cleaned up and not leaked.
    """
    initial_count = count_temp_dat_files()

    with (
        patch("numpy.memmap", side_effect=OSError("Simulated memmap creation failure")),
        pytest.raises(OSError, match="Simulated memmap creation failure"),
    ):
        OutputWriter((3, 100, 100))

    final_count = count_temp_dat_files()
    assert final_count == initial_count, (
        f"OutputWriter leaked temp file on init failure! Before: {initial_count}, After: {final_count}"
    )


def test_hat_tiling_halo_larger_than_tile_size(hat_checkpoint_path, tmp_path):
    """
    Adversarial test: halo > tile_size (e.g. tile_size=16, halo=32).
    Verifies pad calculations, tile extraction, and accurate output dimensions.
    """
    adapter = ModelAdapter()
    info = adapter.inspect(hat_checkpoint_path)
    engine = InferenceEngine(adapter)
    im_mgr = ImageManager()

    input_path = str(tmp_path / "halo_large.png")
    img = Image.new("RGB", (40, 40), color=(100, 200, 50))
    img.save(input_path)

    img_data = im_mgr.load(input_path)
    cancel_event = threading.Event()

    writer = engine.process_image(
        img_data=img_data,
        model_info=info,
        model_path=hat_checkpoint_path,
        device_str="cpu",
        precision_str="fp32",
        tile_size=16,
        halo=32,  # halo > tile_size
        cancel_event=cancel_event,
        progress_callback=lambda c, t, s: None,
        safe_memory=False,
    )

    arr = writer.get_array()
    assert arr.shape == (3, 80, 80), f"Expected shape (3, 80, 80), got {arr.shape}"
    assert arr.dtype == np.uint8
    assert not np.isnan(arr).any()
    writer.cleanup()


def test_hat_tiling_halo_zero(hat_checkpoint_path, tmp_path):
    """
    Adversarial test: halo=0 with odd image dimensions (33x47).
    """
    adapter = ModelAdapter()
    info = adapter.inspect(hat_checkpoint_path)
    engine = InferenceEngine(adapter)
    im_mgr = ImageManager()

    input_path = str(tmp_path / "halo_zero.png")
    img = Image.new("RGB", (33, 47), color=(50, 50, 200))
    img.save(input_path)

    img_data = im_mgr.load(input_path)
    cancel_event = threading.Event()

    writer = engine.process_image(
        img_data=img_data,
        model_info=info,
        model_path=hat_checkpoint_path,
        device_str="cpu",
        precision_str="fp32",
        tile_size=16,
        halo=0,  # zero halo
        cancel_event=cancel_event,
        progress_callback=lambda c, t, s: None,
        safe_memory=False,
    )

    arr = writer.get_array()
    assert arr.shape == (3, 94, 66), f"Expected shape (3, 94, 66), got {arr.shape}"
    assert arr.dtype == np.uint8
    assert not np.isnan(arr).any()
    writer.cleanup()


def test_hat_exception_during_inference_cleans_memmap(hat_checkpoint_path, tmp_path):
    """
    Test that if model forward pass raises an exception mid-inference,
    process_image catches it, calls writer.cleanup(), and re-raises without leaking .dat files.
    """
    adapter = ModelAdapter()
    info = adapter.inspect(hat_checkpoint_path)
    engine = InferenceEngine(adapter)
    im_mgr = ImageManager()

    input_path = str(tmp_path / "inf_error.png")
    img = Image.new("RGB", (64, 64), color=(10, 20, 30))
    img.save(input_path)
    img_data = im_mgr.load(input_path)

    initial_dats = count_temp_dat_files()

    with (
        patch.object(
            engine.model_adapter,
            "load",
            side_effect=RuntimeError("Simulated model execution failure"),
        ),
        pytest.raises(RuntimeError, match="Simulated model execution failure"),
    ):
        engine.process_image(
            img_data=img_data,
            model_info=info,
            model_path=hat_checkpoint_path,
            device_str="cpu",
            precision_str="fp32",
            tile_size=32,
            halo=8,
            cancel_event=threading.Event(),
            progress_callback=lambda c, t, s: None,
            safe_memory=False,
        )

    final_dats = count_temp_dat_files()
    assert final_dats == initial_dats, (
        f"Memmap file leaked on inference error! Before: {initial_dats}, After: {final_dats}"
    )


def test_hat_exception_during_progress_callback_cleans_memmap(hat_checkpoint_path, tmp_path):
    """
    Test that if progress_callback raises an exception (e.g. KeyboardInterrupt or error),
    process_image cleans up the memmap file.
    """
    adapter = ModelAdapter()
    info = adapter.inspect(hat_checkpoint_path)
    engine = InferenceEngine(adapter)
    im_mgr = ImageManager()

    input_path = str(tmp_path / "cb_error.png")
    img = Image.new("RGB", (64, 64), color=(10, 20, 30))
    img.save(input_path)
    img_data = im_mgr.load(input_path)

    initial_dats = count_temp_dat_files()

    def bad_callback(c, t, s):
        if c >= 1:
            raise RuntimeError("Progress callback error")

    with pytest.raises(RuntimeError, match="Progress callback error"):
        engine.process_image(
            img_data=img_data,
            model_info=info,
            model_path=hat_checkpoint_path,
            device_str="cpu",
            precision_str="fp32",
            tile_size=32,
            halo=8,
            cancel_event=threading.Event(),
            progress_callback=bad_callback,
            safe_memory=False,
        )

    final_dats = count_temp_dat_files()
    assert final_dats == initial_dats, (
        f"Memmap file leaked on progress callback error! Before: {initial_dats}, After: {final_dats}"
    )


def test_hat_exception_during_image_save_cleans_memmap(hat_checkpoint_path, tmp_path):
    """
    Test that if ImageManager.save raises an exception inside WorkerServer._run_job,
    the finally block in _run_job cleans up out_file.
    """
    server = WorkerServer()

    input_path = str(tmp_path / "save_error_in.png")
    img = Image.new("RGB", (32, 32), color=(100, 100, 100))
    img.save(input_path)

    # Invalid directory path to trigger save error
    output_path = str(tmp_path / "nonexistent_dir" / "out.png")

    job_data = {
        "job_id": "test_save_err_1",
        "image_path": input_path,
        "model_path": hat_checkpoint_path,
        "output_path": output_path,
        "output_format": "png",
        "device": "cpu",
        "precision": "fp32",
        "tile_size": 32,
        "halo": 8,
        "jpeg_quality": 95,
        "preserve_metadata": True,
        "safe_memory": False,
    }

    initial_dats = count_temp_dat_files()

    with (
        patch("localsr.worker.server.send_message"),
        pytest.raises(FileNotFoundError),
    ):
        server._run_job("test_save_err_1", job_data)

    final_dats = count_temp_dat_files()
    assert final_dats == initial_dats, (
        f"Memmap file leaked on image save failure! Before: {initial_dats}, After: {final_dats}"
    )


def test_hat_process_cancellation_immediate(hat_checkpoint_path, tmp_path):
    """
    Test setting cancel_event BEFORE process_image starts.
    Verifies clean InterruptedError raise and 0 leaked .dat files.
    """
    adapter = ModelAdapter()
    info = adapter.inspect(hat_checkpoint_path)
    engine = InferenceEngine(adapter)
    im_mgr = ImageManager()

    input_path = str(tmp_path / "cancel_imm.png")
    img = Image.new("RGB", (64, 64), color=(10, 20, 30))
    img.save(input_path)
    img_data = im_mgr.load(input_path)

    cancel_event = threading.Event()
    cancel_event.set()

    initial_dats = count_temp_dat_files()

    with pytest.raises(InterruptedError):
        engine.process_image(
            img_data=img_data,
            model_info=info,
            model_path=hat_checkpoint_path,
            device_str="cpu",
            precision_str="fp32",
            tile_size=32,
            halo=8,
            cancel_event=cancel_event,
            progress_callback=lambda c, t, s: None,
            safe_memory=False,
        )

    final_dats = count_temp_dat_files()
    assert final_dats == initial_dats, (
        f"Memmap file leaked on immediate cancel! Before: {initial_dats}, After: {final_dats}"
    )


@pytest.mark.parametrize(
    "w,h",
    [
        (1, 1),
        (2, 1),
        (1, 2),
        (3, 3),
        (15, 15),
        (17, 19),
    ],
)
def test_hat_tiny_and_asymmetric_dimensions(hat_checkpoint_path, tmp_path, w, h):
    """
    Test tiny and asymmetric image dimensions (1x1 up to 17x19).
    """
    adapter = ModelAdapter()
    info = adapter.inspect(hat_checkpoint_path)
    engine = InferenceEngine(adapter)
    im_mgr = ImageManager()

    input_path = str(tmp_path / f"tiny_{w}x{h}.png")
    img = Image.new("RGB", (w, h), color=(255, 128, 64))
    img.save(input_path)

    img_data = im_mgr.load(input_path)
    writer = engine.process_image(
        img_data=img_data,
        model_info=info,
        model_path=hat_checkpoint_path,
        device_str="cpu",
        precision_str="fp32",
        tile_size=128,
        halo=16,
        cancel_event=threading.Event(),
        progress_callback=lambda c, t, s: None,
        safe_memory=False,
    )

    arr = writer.get_array()
    assert arr.shape == (3, h * 2, w * 2)
    assert arr.dtype == np.uint8
    assert not np.isnan(arr).any()
    writer.cleanup()
