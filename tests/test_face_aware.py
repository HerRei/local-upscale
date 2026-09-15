"""Tests for the face-aware restoration pipeline components.

These tests use dummy models (constant-color outputs) so they run on CPU
without real weights. MediaPipe is not required — face masks are
constructed manually for deterministic testing.
"""

from __future__ import annotations

import threading
from dataclasses import replace

import numpy as np
import pytest
import torch
from torch import nn

from localsr.core.face_compositing import blend_tile_outputs, classify_tile
from localsr.core.face_detection import FaceBox, FaceMask, face_area_ratio, smooth_alpha
from localsr.core.inference import InferenceEngine
from localsr.core.model_adapter import NormalizedModelInfo
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


def test_detect_faces_without_optional_runtime_returns_empty(monkeypatch):
    from localsr.core import face_detection

    def unavailable():
        raise ImportError("cv2 unavailable")

    monkeypatch.setattr(face_detection, "_import_cv2", unavailable)

    rgb = np.zeros((100, 100, 3), dtype=np.uint8)
    result = face_detection.detect_faces(rgb)
    assert not result.has_faces
    assert result.boxes == ()
    assert result.mask.shape == (100, 100)


def test_yunet_detection_clamps_boxes_and_filters_low_scores(tmp_path, monkeypatch):
    from localsr.core import face_detection

    detector_path = tmp_path / "yunet.onnx"
    detector_path.write_bytes(b"model")

    class FakeDetector:
        def detect(self, _bgr):
            return 1, np.array(
                [
                    [-5.0, 10.0, 25.0, 30.0, *([0.0] * 10), 0.92],
                    [40.0, 40.0, 10.0, 10.0, *([0.0] * 10), 0.20],
                ],
                dtype=np.float32,
            )

    monkeypatch.setattr(face_detection, "_import_cv2", lambda: object())
    monkeypatch.setattr(
        face_detection,
        "_detector_model_path",
        lambda **_kwargs: detector_path,
    )
    monkeypatch.setattr(
        face_detection,
        "_detector_for",
        lambda *_args, **_kwargs: FakeDetector(),
    )

    result = face_detection.detect_faces(np.zeros((50, 50, 3), dtype=np.uint8))

    assert result.boxes == (FaceBox(x=0, y=10, w=25, h=30, confidence=pytest.approx(0.92)),)
    assert result.mask[10:40, 0:25].all()
    assert not result.mask[:, 25:].any()


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


def test_blend_tile_outputs_applies_documented_fidelity_strength():
    face = np.full((3, 8, 8), 200, dtype=np.uint8)
    general = np.full((3, 8, 8), 40, dtype=np.uint8)
    alpha = np.ones((8, 8), dtype=np.float32)

    preserved = blend_tile_outputs(face, general, alpha, fidelity=0.0)
    balanced = blend_tile_outputs(face, general, alpha, fidelity=0.5)
    restored = blend_tile_outputs(face, general, alpha, fidelity=1.0)

    assert np.all(preserved == 40)
    assert np.all(balanced == 120)
    assert np.all(restored == 200)
    with pytest.raises(ValueError, match="between 0 and 1"):
        blend_tile_outputs(face, general, alpha, fidelity=1.1)


def test_fidelity_blends_original_and_restored_only_inside_face_mask():
    face = np.full((3, 8, 8), 220, dtype=np.uint8)
    general = np.full((3, 8, 8), 40, dtype=np.uint8)
    identity = np.full((3, 8, 8), 100, dtype=np.uint8)
    alpha = np.zeros((8, 8), dtype=np.float32)
    alpha[:, :4] = 1.0

    preserved = blend_tile_outputs(face, general, alpha, fidelity=0.0, identity_output=identity)
    restored = blend_tile_outputs(face, general, alpha, fidelity=1.0, identity_output=identity)

    assert preserved[:, 2:-2, :2].mean() == pytest.approx(100, abs=1)
    assert restored[:, 2:-2, :2].mean() == pytest.approx(220, abs=1)
    assert preserved[:, 2:-2, -2:].mean() == pytest.approx(40, abs=1)
    assert restored[:, 2:-2, -2:].mean() == pytest.approx(40, abs=1)


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


def test_face_aware_rejects_incompatible_model_geometry_before_processing():
    general = NormalizedModelInfo(
        architecture="General",
        scale=2,
        in_channels=3,
        out_channels=3,
        tiling_supported=True,
        half_supported=False,
        size_requirements_min=1,
        size_requirements_mult=1,
        filename="general.pth",
        warnings=[],
    )
    arguments = dict(
        engine=object(),
        img_tensor=torch.zeros((3, 8, 8)),
        face_mask=FaceMask(mask=np.ones((8, 8), dtype=bool), boxes=(FaceBox(0, 0, 8, 8, 1),)),
        general_model_path="general.pth",
        face_model_path="face.pth",
        general_model_info=general,
        device_str="cpu",
        precision_str="fp32",
        tile_size=8,
        halo=0,
        cancel_event=threading.Event(),
    )

    with pytest.raises(ValueError, match="same native scale"):
        process_frame_face_aware(face_model_info=replace(general, scale=4), **arguments)
    with pytest.raises(ValueError, match="compatible output channels"):
        process_frame_face_aware(face_model_info=replace(general, out_channels=1), **arguments)


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
        face_fidelity=0.65,
    )
    import json

    data = json.loads(req.to_json())["data"]
    assert data["face_model_path"] == "/tmp/face.pth"
    assert data["face_fidelity"] == 0.65


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
        face_fidelity=0.65,
    )
    import json

    data = json.loads(req.to_json())["data"]
    assert data["face_model_path"] == "/tmp/face.pth"
    assert data["face_fidelity"] == 0.65
