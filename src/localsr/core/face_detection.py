"""Face detection for the face-aware restoration pipeline.

MediaPipe is the intended detector (~5ms/frame on CPU, Apache-2.0,
no GPU contention with the inference device). It is an optional
dependency: when mediapipe is not installed, detect_faces returns an
empty mask and the pipeline falls back to the general model only.

The detector runs in the worker process on CPU alongside PyAV decode.
For video, the caller can cache detection results across neighboring
frames since faces move slowly at 30 fps.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class FaceBox:
    """A single detected face bounding box in source-frame pixel coordinates."""

    x: int
    y: int
    w: int
    h: int
    confidence: float


@dataclass(frozen=True)
class FaceMask:
    """Per-pixel face mask plus the boxes that produced it.

    mask is a (H, W) boolean array where True means the pixel is inside
    at least one face bounding box. boxes is the list of detected faces.
    """

    mask: np.ndarray  # (H, W) bool
    boxes: tuple[FaceBox, ...]

    @property
    def has_faces(self) -> bool:
        return len(self.boxes) > 0


def _empty_mask(height: int, width: int) -> FaceMask:
    return FaceMask(mask=np.zeros((height, width), dtype=bool), boxes=())


def detect_faces(rgb: np.ndarray, min_confidence: float = 0.5) -> FaceMask:
    """Run face detection on an RGB frame.

    Returns a FaceMask with a boolean mask and the detected boxes. When
    MediaPipe is not installed, returns an empty mask (no faces).
    """
    h, w = rgb.shape[:2]
    try:
        import mediapipe as mp
    except ImportError:
        return _empty_mask(h, w)

    boxes: list[FaceBox] = []
    mask = np.zeros((h, w), dtype=bool)

    try:
        solution = mp.face_detection.FaceDetection(
            model_selection=1, min_detection_confidence=min_confidence
        )
        results = solution.process(rgb)
        solution.close()
    except Exception:  # noqa: BLE001
        return _empty_mask(h, w)

    if results.detections:
        for detection in results.detections:
            bbox = detection.location_data.relative_bounding_box
            x = max(0, int(bbox.xmin * w))
            y = max(0, int(bbox.ymin * h))
            bw = min(w - x, int(bbox.width * w))
            bh = min(h - y, int(bbox.height * h))
            if bw <= 0 or bh <= 0:
                continue
            confidence = float(detection.score[0]) if detection.score else 0.0
            boxes.append(FaceBox(x=x, y=y, w=bw, h=bh, confidence=confidence))
            mask[y : y + bh, x : x + bw] = True

    return FaceMask(mask=mask, boxes=tuple(boxes))


def face_mask_for_tile_core(
    face_mask: np.ndarray, tile_core_x: int, tile_core_y: int, tile_core_w: int, tile_core_h: int
) -> np.ndarray:
    """Return the face-mask region corresponding to a tile's core, as float32.

    Values are 0.0 (non-face) or 1.0 (face). The caller blends boundary
    tiles using this as the alpha.
    """
    return face_mask[
        tile_core_y : tile_core_y + tile_core_h, tile_core_x : tile_core_x + tile_core_w
    ].astype(np.float32)


def smooth_alpha(alpha: np.ndarray, kernel_size: int = 5) -> np.ndarray:
    """Light box-filter on the alpha mask for smoother boundary transitions.

    Uses a simple separable box filter implemented with numpy so no
    OpenCV dependency is needed.
    """
    if kernel_size <= 1:
        return alpha
    k = kernel_size
    # Pad with edge values to avoid shrinking the output.
    padded = np.pad(alpha, k // 2, mode="edge")
    # Separable box filter: sum along rows, then along cols, then divide.
    row_sum = np.cumsum(padded, axis=1)
    row_sum[:, k:] -= row_sum[:, :-k]
    col_sum = np.cumsum(row_sum, axis=0)
    col_sum[k:, :] -= col_sum[:-k, :]
    return (col_sum[k - 1 :, k - 1 :] / float(k * k)).clip(0.0, 1.0)


def face_area_ratio(face_mask: FaceMask) -> float:
    """Fraction of the frame area covered by faces."""
    if face_mask.mask.size == 0:
        return 0.0
    return float(face_mask.mask.mean())
