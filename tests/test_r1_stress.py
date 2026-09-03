import os
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageCms

from localsr.core.image_io import ImageManager
from localsr.core.output_writer import OutputWriter
from localsr.worker.server import WorkerServer


class StressDummyModelAdapter:
    def __init__(self, scale=2):
        self.scale = scale

    def inspect(self, path):
        from localsr.core.model_adapter import NormalizedModelInfo

        return NormalizedModelInfo(
            architecture="HAT",
            scale=self.scale,
            in_channels=3,
            out_channels=3,
            tiling_supported=True,
            half_supported=False,
            size_requirements_min=1,
            size_requirements_mult=16,
            filename="stress_model.pth",
            warnings=[],
        )

    def load(self, path, dev, prec):
        return None, None

    def release(self):
        pass


def create_srgb_icc():
    srgb = ImageCms.createProfile("sRGB")
    return ImageCms.ImageCmsProfile(srgb).tobytes()


def test_atomic_rename_formats_and_overwrite(tmp_path, monkeypatch):
    """
    Stress-tests atomic saving (.tmp -> final path via os.replace) across format types
    (PNG, JPEG, TIFF) and confirms overwriting existing files works cleanly.
    """
    manager = ImageManager()
    mmap_arr = np.zeros((3, 30, 30), dtype=np.uint8)

    replace_history = []
    orig_replace = os.replace

    def tracking_replace(src, dst):
        replace_history.append((src, dst))
        # Ensure .tmp exists right before replacement
        assert os.path.exists(src), f"Temporary file {src} must exist before os.replace"
        return orig_replace(src, dst)

    monkeypatch.setattr(os, "replace", tracking_replace)

    formats = [("png", "out.png"), ("jpg", "out.jpg"), ("tiff", "out.tiff")]

    for fmt, filename in formats:
        dest_path = str(tmp_path / filename)
        calls_before = len(replace_history)

        # Save image
        manager.save_from_writer(
            writer_mmap=mmap_arr,
            destination_path=dest_path,
            format=fmt,
            quality=90,
            preserve_metadata=False,
            icc_profile=None,
            safe_exif={},
            scale=1,
        )

        # Assert the final file exists and this save left no app-owned temporary
        # file behind.  Output names are intentionally randomized so LocalSR
        # never adopts or deletes a user's pre-existing ``<output>.tmp`` file.
        assert os.path.exists(dest_path), f"Destination {dest_path} should exist"
        assert not list(tmp_path.glob(f".{filename}.localsr-image-*.tmp"))

        # Confirm one unique same-directory temporary was atomically replaced.
        assert len(replace_history) == calls_before + 1
        temporary, destination = replace_history[-1]
        assert Path(temporary).parent == tmp_path
        assert Path(temporary).name.startswith(f".{filename}.localsr-image-")
        assert Path(destination) == Path(dest_path)

    # Overwrite test: create an existing file with dummy content first
    overwrite_target = str(tmp_path / "overwrite_test.png")
    with open(overwrite_target, "w") as f:
        f.write("old data")

    manager.save_from_writer(
        writer_mmap=mmap_arr,
        destination_path=overwrite_target,
        format="png",
        quality=98,
        preserve_metadata=False,
        icc_profile=None,
        safe_exif={},
        scale=1,
    )

    assert os.path.exists(overwrite_target)
    assert not os.path.exists(overwrite_target + ".tmp")
    with Image.open(overwrite_target) as img:
        assert img.size == (30, 30)


def test_orphan_tmp_cleanup_on_save_exception(tmp_path, monkeypatch):
    """
    Stress-tests exception handling during save to guarantee .tmp files are NEVER
    left orphaned when an error occurs during PIL save or os.replace.
    """
    manager = ImageManager()
    mmap_arr = np.zeros((3, 20, 20), dtype=np.uint8)

    # 1. Exception during os.replace
    dest_path1 = str(tmp_path / "fail_replace.png")

    def faulty_replace(src, dst):
        assert os.path.exists(src), ".tmp file must exist when os.replace fails"
        raise OSError("Simulated filesystem I/O replace error")

    monkeypatch.setattr(os, "replace", faulty_replace)

    with pytest.raises(OSError, match="Simulated filesystem I/O replace error"):
        manager.save_from_writer(
            writer_mmap=mmap_arr,
            destination_path=dest_path1,
            format="png",
            quality=98,
            preserve_metadata=False,
            icc_profile=None,
            safe_exif={},
            scale=1,
        )

    assert not list(tmp_path.glob(".fail_replace.png.localsr-image-*.tmp")), (
        "Orphaned app-owned temporary must be cleaned up on replace failure"
    )
    assert not os.path.exists(dest_path1), "Destination path should not exist on replace failure"

    # 2. Exception during PIL image save (partial file creation simulated)
    monkeypatch.undo()
    dest_path2 = str(tmp_path / "fail_pil_save.png")

    def faulty_pil_save(self, fp, *args, **kwargs):
        # Create a dummy partial file on disk
        if isinstance(fp, str):
            with open(fp, "wb") as f:
                f.write(b"partial byte stream")
        raise RuntimeError("Simulated PIL save encoding error")

    monkeypatch.setattr(Image.Image, "save", faulty_pil_save)

    with pytest.raises(RuntimeError, match="Simulated PIL save encoding error"):
        manager.save_from_writer(
            writer_mmap=mmap_arr,
            destination_path=dest_path2,
            format="png",
            quality=98,
            preserve_metadata=False,
            icc_profile=None,
            safe_exif={},
            scale=1,
        )

    assert not list(tmp_path.glob(".fail_pil_save.png.localsr-image-*.tmp")), (
        "Orphaned app-owned temporary must be cleaned up on PIL save failure"
    )
    assert not os.path.exists(dest_path2), "Destination path should not exist on PIL save failure"


def test_metadata_and_alpha_scaling_passing(tmp_path):
    """
    Stress-tests metadata passing (icc_profile, safe_exif, orientation) and alpha channel scaling.
    """
    in_rgba_path = str(tmp_path / "in_alpha.png")
    arr_in = np.zeros((10, 10, 4), dtype=np.uint8)
    arr_in[:, :, 3] = 200
    Image.fromarray(arr_in, "RGBA").save(in_rgba_path)

    manager = ImageManager()
    img_data = manager.load(in_rgba_path)

    assert img_data["original_mode"] == "RGBA"
    assert img_data["alpha_image"] is not None

    icc_bytes = create_srgb_icc()
    safe_exif = {315: "Stress Artist", 306: "2026:08:06 12:00:00"}

    # 1. Save RGBA output to PNG with 2x scale
    out_mmap = np.zeros((3, 20, 20), dtype=np.uint8)
    out_png = str(tmp_path / "out_alpha_2x.png")

    manager.save_from_writer(
        writer_mmap=out_mmap,
        destination_path=out_png,
        format="png",
        quality=98,
        preserve_metadata=True,
        icc_profile=icc_bytes,
        safe_exif=safe_exif,
        scale=2,
    )

    with Image.open(out_png) as img:
        assert img.mode == "RGBA"
        assert img.size == (20, 20)
        assert img.getchannel("A").size == (20, 20)

    # 2. Save RGBA output to JPEG with 2x scale (must convert RGBA to RGB)
    out_jpg = str(tmp_path / "out_alpha_2x.jpg")

    manager.save_from_writer(
        writer_mmap=out_mmap,
        destination_path=out_jpg,
        format="jpeg",
        quality=98,
        preserve_metadata=True,
        icc_profile=icc_bytes,
        safe_exif=safe_exif,
        scale=2,
    )

    with Image.open(out_jpg) as img:
        assert img.mode == "RGB"
        assert img.size == (20, 20)
        exif = img.getexif()
        assert exif[315] == "Stress Artist"
        assert exif[274] == 1


def test_image_manager_load_valid_icc_profile(tmp_path):
    """
    A valid input ICC profile is converted to sRGB and remains embedded metadata.
    """
    img_icc_path = str(tmp_path / "icc_input.jpg")
    img = Image.new("RGB", (10, 10), color="blue")
    icc_bytes = create_srgb_icc()
    img.save(img_icc_path, icc_profile=icc_bytes)

    manager = ImageManager()
    data = manager.load(img_icc_path)

    assert data["has_invalid_icc"] is False
    assert isinstance(data["icc_profile"], bytes)
    assert len(data["icc_profile"]) > 0


def test_output_writer_double_cleanup_idempotency():
    """
    Verifies that calling OutputWriter.cleanup() multiple times is safe and idempotent.
    """
    writer = OutputWriter((3, 10, 10), dtype=np.uint8)
    dat_path = writer.get_path()
    assert os.path.exists(dat_path)

    # First cleanup
    writer.cleanup()
    assert not os.path.exists(dat_path)
    assert writer.mmap is None

    # Second cleanup call should be no-op
    writer.cleanup()
    assert not os.path.exists(dat_path)


def test_worker_run_job_memmap_cleanup_normal_completion(tmp_path, monkeypatch):
    """
    Empirically verifies unconditional .dat memmap cleanup in WorkerServer._run_job()
    under normal job completion.
    """
    server = WorkerServer()
    server.model_adapter = StressDummyModelAdapter(scale=2)

    # Real OutputWriter creating a real .dat file on disk
    writer = OutputWriter((3, 20, 20), dtype=np.uint8)
    dat_file_path = writer.get_path()

    assert os.path.exists(dat_file_path), "Real memmap .dat file must exist initially"

    class MockEngine:
        def process_image(self, **kwargs):
            return writer

    server.engine = MockEngine()

    # Create dummy input image
    in_img_path = str(tmp_path / "input.png")
    Image.new("RGB", (10, 10), color="red").save(in_img_path)
    out_img_path = str(tmp_path / "output.png")

    job_data = {
        "job_id": "stress_job_normal",
        "image_path": in_img_path,
        "model_path": "model.pth",
        "output_path": out_img_path,
        "output_format": "png",
        "device": "cpu",
        "precision": "fp32",
        "tile_size": 32,
        "halo": 4,
        "jpeg_quality": 98,
        "preserve_metadata": True,
        "safe_memory": True,
    }

    server._run_job("stress_job_normal", job_data)

    # Assert output image was created successfully
    assert os.path.exists(out_img_path)
    # Assert memmap .dat file was UNCONDITIONALLY cleaned up from disk
    assert not os.path.exists(dat_file_path), (
        ".dat memmap file must be deleted after job completion"
    )


def test_worker_run_job_memmap_cleanup_on_cancellation(tmp_path):
    """
    Empirically verifies unconditional .dat memmap cleanup in WorkerServer._run_job()
    when job is cancelled midway.
    """
    server = WorkerServer()
    server.model_adapter = StressDummyModelAdapter(scale=2)

    writer = OutputWriter((3, 20, 20), dtype=np.uint8)
    dat_file_path = writer.get_path()
    assert os.path.exists(dat_file_path)

    class CancellingEngine:
        def process_image(self, cancel_event, **kwargs):
            cancel_event.set()
            return writer

    server.engine = CancellingEngine()

    in_img_path = str(tmp_path / "input_cancel.png")
    Image.new("RGB", (10, 10)).save(in_img_path)
    out_img_path = str(tmp_path / "output_cancel.png")

    job_data = {
        "job_id": "stress_job_cancel",
        "image_path": in_img_path,
        "model_path": "model.pth",
        "output_path": out_img_path,
        "output_format": "png",
        "device": "cpu",
        "precision": "fp32",
        "tile_size": 32,
        "halo": 4,
        "jpeg_quality": 98,
        "preserve_metadata": True,
        "safe_memory": True,
    }

    server._run_job("stress_job_cancel", job_data)

    # Output file should NOT be created
    assert not os.path.exists(out_img_path)
    # Memmap file MUST be cleaned up from disk
    assert not os.path.exists(dat_file_path), ".dat memmap file must be deleted on job cancellation"


def test_worker_run_job_memmap_cleanup_on_forced_exception(tmp_path):
    """
    Empirically verifies unconditional .dat memmap cleanup in WorkerServer._run_job()
    under forced exception inside im_manager.save() or process_image().
    """
    server = WorkerServer()
    server.model_adapter = StressDummyModelAdapter(scale=2)

    writer = OutputWriter((3, 20, 20), dtype=np.uint8)
    dat_file_path = writer.get_path()
    assert os.path.exists(dat_file_path)

    class FailingSaveEngine:
        def process_image(self, **kwargs):
            return writer

    server.engine = FailingSaveEngine()

    in_img_path = str(tmp_path / "input_error.png")
    Image.new("RGB", (10, 10)).save(in_img_path)
    # Target path in non-existent directory to force save exception
    out_img_path = str(tmp_path / "non_existent_dir" / "output_error.png")

    job_data = {
        "job_id": "stress_job_error",
        "image_path": in_img_path,
        "model_path": "model.pth",
        "output_path": out_img_path,
        "output_format": "png",
        "device": "cpu",
        "precision": "fp32",
        "tile_size": 32,
        "halo": 4,
        "jpeg_quality": 98,
        "preserve_metadata": True,
        "safe_memory": True,
    }

    with pytest.raises(FileNotFoundError):
        server._run_job("stress_job_error", job_data)

    # Memmap file MUST be cleaned up from disk in finally block
    assert not os.path.exists(dat_file_path), ".dat memmap file must be deleted on forced exception"


def test_worker_run_job_exception_before_memmap_creation(tmp_path):
    """
    Verifies that if an exception occurs before out_file is created (out_file is None),
    WorkerServer._run_job() handles the finally block safely without AttributeError.
    """
    server = WorkerServer()
    server.model_adapter = StressDummyModelAdapter(scale=2)

    # Missing image path will cause load() to raise FileNotFoundError
    job_data = {
        "job_id": "stress_job_no_file",
        "image_path": str(tmp_path / "missing_input.png"),
        "model_path": "model.pth",
        "output_path": str(tmp_path / "out.png"),
        "output_format": "png",
        "device": "cpu",
        "precision": "fp32",
        "tile_size": 32,
        "halo": 4,
        "jpeg_quality": 98,
        "preserve_metadata": True,
        "safe_memory": True,
    }

    with pytest.raises(FileNotFoundError):
        server._run_job("stress_job_no_file", job_data)
