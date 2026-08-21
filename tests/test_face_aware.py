"""Tests for the face-aware restoration pipeline components.

These tests use dummy models (constant-color outputs) so they run on CPU
without real weights. MediaPipe is not required — face masks are
constructed manually for deterministic testing.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import numpy as np
import pytest
import torch
from torch import nn

from localsr.core.face_compositing import blend_tile_outputs, classify_tile
from localsr.core.face_detection import FaceBox, FaceMask, face_area_ratio, smooth_alpha
from localsr.core.inference import InferenceEngine
from localsr.core.model_adapter import NormalizedModelInfo
from localsr.core.tiling import generate_tiles, tile_face_overlap
from localsr.core.video_pipeline import process_frame_face_aware

# ── Dummy models ────────────────────────────────────────────────────


class ConstantColorModel(nn.Module):
    """Outputs a constant color for every input. Used to distinguish
    face-model output from general-model output in tests."""

    def __init__(self, scale: int, color: float):
        super().__init__()
        self.scale = scale
        self.color = color

    def forward(self, x):
        _, _, h, w = x.shape
        out = torch.full((1, 3, h * self.scale, w * self.scale), self.color)
        return out


class DummyAdapter:
    """Loads a ConstantColorModel with the given color."""

    def __init__(self, scale: int, color: float):
        self.scale = scale
        self.color = color
        self.model_info = NormalizedModelInfo(
            architecture="Dummy",
            scale=scale,
            in_channels=3,
            out_channels=3,
            tiling_supported=True,
            half_supported=False,
            size_requirements_min=1,
            size_requirements_mult=1,
            filename="dummy.pth",
            warnings=[],
        )

    def load(self, path, device, precision):
        return ConstantColorModel(self.scale, self.color).to(device).to(precision), None

    def release(self):
        pass


# ── face_detection tests ────────────────────────────────────────────


def test_detect_faces_without_mediapipe_returns_empty():
    from localsr.core.face_detection import detect_faces

    rgb = np.zeros((100, 100, 3), dtype=np.uint8)
    result = detect_faces(rgb)
    assert not result.has_faces
    assert result.boxes == ()
    assert result.mask.shape == (100, 100)


def test_face_mask_for_tile_core_returns_float_alpha():
    mask = np.zeros((50, 50), dtype=bool)
    mask[10:30, 10:30] = True
    alpha = _face_mask_for_tile_core(mask, 5, 5, 20, 20)
    assert alpha.shape == (20, 20)
    assert alpha.dtype == np.float32
    # The tile core at (5,5) size (20,20) overlaps the face at (10:30, 10:30)
    # in the region (10:25, 10:25) relative to the frame.
    assert alpha[5, 5] == 1.0  # inside the face box
    assert alpha[0, 0] == 0.0  # outside


def _face_mask_for_tile_core(mask, x, y, w, h):
    from localsr.core.face_detection import face_mask_for_tile_core

    return face_mask_for_tile_core(mask, x, y, w, h)


def test_smooth_alpha_blurs_boundaries():
    alpha = np.zeros((20, 20), dtype=np.float32)
    alpha[10:, :] = 1.0
    smoothed = smooth_alpha(alpha, kernel_size=5)
    # The boundary at row 10 should now have intermediate values.
    assert 0.0 < smoothed[10, 10] < 1.0
    # Far from the boundary, values are still 0 or 1.
    assert smoothed[0, 10] == pytest.approx(0.0, abs=0.1)
    assert smoothed[19, 10] == pytest.approx(1.0, abs=0.1)


def test_face_area_ratio():
    mask = np.zeros((100, 100), dtype=bool)
    mask[25:75, 25:75] = True
    fm = FaceMask(mask=mask, boxes=(FaceBox(25, 25, 50, 50, 0.9),))
    assert face_area_ratio(fm) == pytest.approx(0.25)


# ── face_compositing tests ──────────────────────────────────────────


def test_classify_tile_face():
    alpha = np.ones((10, 10), dtype=np.float32)
    assert classify_tile(alpha) == "face"


def test_classify_tile_general():
    alpha = np.zeros((10, 10), dtype=np.float32)
    assert classify_tile(alpha) == "general"


def test_classify_tile_boundary():
    alpha = np.zeros((10, 10), dtype=np.float32)
    alpha[:5] = 1.0
    assert classify_tile(alpha) == "boundary"


def test_blend_tile_outputs_uses_face_model_where_alpha_is_one():
    face = np.full((3, 10, 10), 200, dtype=np.uint8)
    general = np.full((3, 10, 10), 50, dtype=np.uint8)
    alpha = np.ones((10, 10), dtype=np.float32)
    blended = blend_tile_outputs(face, general, alpha)
    assert np.all(blended == pytest.approx(200, abs=5))


def test_blend_tile_outputs_uses_general_where_alpha_is_zero():
    face = np.full((3, 10, 10), 200, dtype=np.uint8)
    general = np.full((3, 10, 10), 50, dtype=np.uint8)
    alpha = np.zeros((10, 10), dtype=np.float32)
    blended = blend_tile_outputs(face, general, alpha)
    assert np.all(blended == pytest.approx(50, abs=5))


def test_blend_tile_outputs_mixed_alpha():
    face = np.full((3, 10, 10), 200, dtype=np.uint8)
    general = np.full((3, 10, 10), 50, dtype=np.uint8)
    alpha = np.full((10, 10), 0.5, dtype=np.float32)
    blended = blend_tile_outputs(face, general, alpha)
    # With smoothing the exact value won't be 125 but it should be between.
    assert np.all(blended > 80)
    assert np.all(blended < 170)


# ── tiling tests ────────────────────────────────────────────────────


def test_tile_face_overlap_full():
    mask = np.ones((64, 64), dtype=bool)
    tiles = list(generate_tiles(64, 64, 64, 4, 4))
    assert len(tiles) == 1
    assert tile_face_overlap(tiles[0], mask) == pytest.approx(1.0)


def test_tile_face_overlap_none():
    mask = np.zeros((64, 64), dtype=bool)
    tiles = list(generate_tiles(64, 64, 64, 4, 4))
    assert tile_face_overlap(tiles[0], mask) == pytest.approx(0.0)


def test_tile_face_overlap_partial():
    mask = np.zeros((64, 64), dtype=bool)
    mask[:, :32] = True  # left half is face
    tiles = list(generate_tiles(64, 64, 64, 4, 4))
    overlap = tile_face_overlap(tiles[0], mask)
    assert 0.4 < overlap < 0.6


# ── InferenceEngine dual-model tests ────────────────────────────────


def test_dual_model_loading_and_switching():
    """Load two models, verify switching doesn't reload."""
    adapter_a = DummyAdapter(2, 0.8)
    DummyAdapter(2, 0.2)

    # We need a single adapter that can load both paths. Create a custom one.
    class DualAdapter:
        def __init__(self):
            self.model_info = adapter_a.model_info
            self.call_count = 0

        def load(self, path, device, precision):
            self.call_count += 1
            if "face" in path:
                return ConstantColorModel(2, 0.2).to(device).to(precision), None
            return ConstantColorModel(2, 0.8).to(device).to(precision), None

        def release(self):
            pass

    dual = DualAdapter()
    engine = InferenceEngine(dual)

    # Load general model.
    engine.load_model("general.pth", "cpu", "fp32", dual.model_info)
    assert dual.call_count == 1
    assert engine.active_model is not None

    # Load face model.
    engine.load_model("face.pth", "cpu", "fp32", dual.model_info)
    assert dual.call_count == 2

    # Switch back to general — should NOT reload.
    engine.load_model("general.pth", "cpu", "fp32", dual.model_info)
    assert dual.call_count == 2  # still 2, no new load

    # Switch to face again — no reload.
    engine.load_model("face.pth", "cpu", "fp32", dual.model_info)
    assert dual.call_count == 2

    engine.release_model()


def test_release_one_model_keeps_others():
    class MultiAdapter:
        def __init__(self):
            self.model_info = NormalizedModelInfo(
                architecture="Dummy",
                scale=2,
                in_channels=3,
                out_channels=3,
                tiling_supported=True,
                half_supported=False,
                size_requirements_min=1,
                size_requirements_mult=1,
                filename="dummy.pth",
                warnings=[],
            )

        def load(self, path, device, precision):
            return ConstantColorModel(2, 0.5).to(device).to(precision), None

        def release(self):
            pass

    adapter = MultiAdapter()
    engine = InferenceEngine(adapter)
    engine.load_model("a.pth", "cpu", "fp32", adapter.model_info)
    engine.load_model("b.pth", "cpu", "fp32", adapter.model_info)
    assert len(engine._loaded_models) == 2

    engine.release_model("a.pth")
    assert len(engine._loaded_models) == 1
    assert "b.pth" in engine._loaded_models
    assert engine._active_path == "b.pth"

    engine.release_model()


# ── process_frame_face_aware end-to-end test ────────────────────────


def test_process_frame_face_aware_routes_tiles_correctly():
    """Face tiles get face model color, general tiles get general model
    color, boundary tiles get a blend."""
    scale = 2

    class FaceAwareAdapter:
        def __init__(self):
            self.model_info = NormalizedModelInfo(
                architecture="Dummy",
                scale=scale,
                in_channels=3,
                out_channels=3,
                tiling_supported=True,
                half_supported=False,
                size_requirements_min=1,
                size_requirements_mult=1,
                filename="dummy.pth",
                warnings=[],
            )

        def load(self, path, device, precision):
            if "face" in path:
                return ConstantColorModel(scale, 0.9).to(device).to(precision), None
            return ConstantColorModel(scale, 0.1).to(device).to(precision), None

        def release(self):
            pass

    adapter = FaceAwareAdapter()
    engine = InferenceEngine(adapter)

    # Create a 64×64 frame with a face in the left half.
    img_tensor = torch.rand((3, 64, 64))
    mask = np.zeros((64, 64), dtype=bool)
    mask[:, :32] = True  # left half is "face"
    face_mask = FaceMask(mask=mask, boxes=(FaceBox(0, 0, 32, 64, 0.9),))

    result = process_frame_face_aware(
        engine=engine,
        img_tensor=img_tensor,
        face_mask=face_mask,
        general_model_path="general.pth",
        face_model_path="face.pth",
        general_model_info=adapter.model_info,
        face_model_info=adapter.model_info,
        device_str="cpu",
        precision_str="fp32",
        tile_size=64,
        halo=4,
        cancel_event=threading.Event(),
        safe_memory=False,
        face_threshold_high=0.85,
        face_threshold_low=0.15,
    )

    assert result.shape == (3, 128, 128)
    # The left half (face region) should be brighter (face model = 0.9).
    left_mean = result[:, :, :64].mean()
    right_mean = result[:, :, 64:].mean()
    assert left_mean > right_mean
    # Face model outputs ~230 (0.9*255), general ~25 (0.1*255).
    assert left_mean > 150
    assert right_mean < 60

    engine.release_model()


def test_process_frame_face_aware_with_no_faces_uses_general():
    """When face_mask has no faces, all tiles should use the general model."""
    scale = 2

    class FaceAwareAdapter:
        def __init__(self):
            self.model_info = NormalizedModelInfo(
                architecture="Dummy",
                scale=scale,
                in_channels=3,
                out_channels=3,
                tiling_supported=True,
                half_supported=False,
                size_requirements_min=1,
                size_requirements_mult=1,
                filename="dummy.pth",
                warnings=[],
            )

        def load(self, path, device, precision):
            if "face" in path:
                return ConstantColorModel(scale, 0.9).to(device).to(precision), None
            return ConstantColorModel(scale, 0.1).to(device).to(precision), None

        def release(self):
            pass

    adapter = FaceAwareAdapter()
    engine = InferenceEngine(adapter)

    img_tensor = torch.rand((3, 64, 64))
    empty_mask = FaceMask(mask=np.zeros((64, 64), dtype=bool), boxes=())

    result = process_frame_face_aware(
        engine=engine,
        img_tensor=img_tensor,
        face_mask=empty_mask,
        general_model_path="general.pth",
        face_model_path="face.pth",
        general_model_info=adapter.model_info,
        face_model_info=adapter.model_info,
        device_str="cpu",
        precision_str="fp32",
        tile_size=64,
        halo=4,
        cancel_event=threading.Event(),
        safe_memory=False,
    )

    # All tiles classified as "general" → output should be dark (0.1*255 ≈ 25).
    assert result.mean() < 60

    engine.release_model()


# ── Protocol message tests ──────────────────────────────────────────


def test_face_detection_protocol_messages():
    import json

    from localsr.protocol.messages import (
        DetectFacesRequest,
        FaceDetectionUnavailable,
        FacesDetected,
    )

    req = DetectFacesRequest(image_path="/tmp/test.png")
    assert json.loads(req.to_json())["type"] == "detect_faces_request"

    detected = FacesDetected(
        image_path="/tmp/test.png",
        boxes=[{"x": 10, "y": 20, "w": 30, "h": 40, "confidence": 0.95}],
    )
    assert json.loads(detected.to_json())["type"] == "faces_detected"
    assert json.loads(detected.to_json())["data"]["boxes"][0]["confidence"] == 0.95

    unavailable = FaceDetectionUnavailable(
        image_path="/tmp/test.png", message="MediaPipe not installed"
    )
    assert json.loads(unavailable.to_json())["type"] == "face_detection_unavailable"


def test_job_request_includes_face_model_path():
    from localsr.protocol.messages import JobRequest

    req = JobRequest(
        job_id="j1",
        image_path="/tmp/test.png",
        model_path="/tmp/general.pth",
        output_path="/tmp/out.png",
        output_format="png",
        device="cpu",
        tile_size=128,
        halo=16,
        precision="fp32",
        jpeg_quality=98,
        preserve_metadata=True,
        safe_memory=True,
        face_model_path="/tmp/face.pth",
    )
    import json

    data = json.loads(req.to_json())["data"]
    assert data["face_model_path"] == "/tmp/face.pth"


def test_video_job_request_includes_face_model_path():
    from localsr.protocol.messages import VideoJobRequest

    req = VideoJobRequest(
        job_id="j1",
        video_path="/tmp/test.mp4",
        model_path="/tmp/general.pth",
        output_video_path="/tmp/out.mp4",
        container="mp4",
        crf=18,
        fps=None,
        device="cpu",
        tile_size=128,
        halo=16,
        precision="fp32",
        safe_memory=True,
        face_model_path="/tmp/face.pth",
    )
    import json

    data = json.loads(req.to_json())["data"]
    assert data["face_model_path"] == "/tmp/face.pth"


def test_jobs_auto_pair_installed_face_companion(tmp_path):
    """Face-aware restoration follows the model choice, not a button."""
    import subprocess
    import sys
    import textwrap

    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "src")
    script = textwrap.dedent(
        f"""
        from pathlib import Path
        from localsr.core.model_catalog import MODEL_CATALOG
        from localsr.ui.slint_app import CUSTOM_MODEL_ID, create_slint_application

        base = Path({str(tmp_path)!r})
        models = base / "models"
        models.mkdir(parents=True, exist_ok=True)
        catalog = {{m.model_id: m for m in MODEL_CATALOG}}
        for model_id in ("hat_s_x4", "hat_s_x4_face"):
            model = catalog[model_id]
            with open(models / model.filename, "wb") as handle:
                handle.truncate(model.size_bytes)

        application = create_slint_application(
            start_worker=False,
            settings_path=base / "settings.json",
            model_root=models,
        )
        application.set_task(0)

        def select(model_id):
            index = next(
                i for i, m in enumerate(application.filtered_models)
                if (m.model_id if m is not None else CUSTOM_MODEL_ID) == model_id
            )
            application.set_model_index(index)

        # General model with an installed FACE companion: jobs carry it.
        select("hat_s_x4")
        companion = application._face_companion_path()
        assert companion is not None
        assert companion.endswith(catalog["hat_s_x4_face"].filename)

        # The face model itself pairs back to a GENERAL model: no companion.
        select("hat_s_x4_face")
        assert application._face_companion_path() is None

        # Companion not installed: no pairing.
        (models / catalog["hat_s_x4_face"].filename).unlink()
        select("hat_s_x4")
        assert application._face_companion_path() is None
        application.shutdown()
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
