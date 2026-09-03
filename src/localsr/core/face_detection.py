"""Bounded CPU face detection for the face-aware restoration pipeline.

The release worker uses OpenCV's YuNet detector.  Its tiny, independently
MIT-licensed checkpoint is downloaded from OpenCV Zoo on first face-aware use
through LocalSR's normal size/SHA-256 verification path.  This avoids bundling
the detector weight and avoids MediaPipe's unrelated JAX, plotting, audio, and
GUI dependency tree.

The detector stays on CPU, so it does not contend with the inference device.
For video, the caller caches masks across neighboring frames.
"""

from __future__ import annotations

import importlib
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .model_catalog import FACE_DETECTOR_MODEL, ModelStore, download_model

_download_lock = threading.Lock()
_detectors = threading.local()


class FaceDetectionUnavailable(RuntimeError):
    """Raised when a requested face-aware job cannot initialize its detector."""


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


def _import_cv2():
    return importlib.import_module("cv2")


def _detector_model_path(
    *,
    store: ModelStore | None,
    cancel_event: threading.Event | None,
    allow_download: bool,
) -> Path:
    model_store = store or ModelStore()
    path = model_store.path_for(FACE_DETECTOR_MODEL)
    if model_store.is_installed(FACE_DETECTOR_MODEL):
        return path
    if not allow_download:
        raise FaceDetectionUnavailable(
            "The verified YuNet face detector is not installed. Run the face-aware job once "
            "while online to install it."
        )
    with _download_lock:
        if not model_store.is_installed(FACE_DETECTOR_MODEL):
            try:
                download_model(FACE_DETECTOR_MODEL, path, cancel_event=cancel_event)
            except Exception as error:  # noqa: BLE001 - preserve actionable download cause
                raise FaceDetectionUnavailable(
                    f"Could not install the verified YuNet face detector: {error}"
                ) from error
    if not model_store.is_installed(FACE_DETECTOR_MODEL):
        raise FaceDetectionUnavailable(
            "The YuNet face detector was downloaded but failed verification."
        )
    return path


def _detector_for(cv2, path: Path, width: int, height: int, min_confidence: float):
    stat = path.stat()
    key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns)
    cached_key = getattr(_detectors, "key", None)
    detector = getattr(_detectors, "instance", None)
    if detector is None or cached_key != key:
        detector = cv2.FaceDetectorYN.create(
            str(path),
            "",
            (width, height),
            score_threshold=float(min_confidence),
            nms_threshold=0.3,
            top_k=5000,
        )
        _detectors.key = key
        _detectors.instance = detector
    detector.setInputSize((width, height))
    detector.setScoreThreshold(float(min_confidence))
    return detector


def detect_faces(
    rgb: np.ndarray,
    min_confidence: float = 0.5,
    *,
    store: ModelStore | None = None,
    cancel_event: threading.Event | None = None,
    allow_download: bool = True,
) -> FaceMask:
    """Run the verified YuNet detector on one RGB frame.

    A missing optional OpenCV runtime means the capability is unavailable and
    returns an empty mask, matching feature negotiation.  Once the runtime is
    present, model download or initialization failures are surfaced instead of
    silently claiming that an image contains no faces.
    """
    h, w = rgb.shape[:2]
    try:
        cv2 = _import_cv2()
    except ImportError:
        return _empty_mask(h, w)
    if rgb.ndim != 3 or rgb.shape[2] < 3 or h <= 0 or w <= 0:
        raise ValueError("Face detection requires a non-empty H×W×3 RGB image.")
    if not 0.0 <= min_confidence <= 1.0:
        raise ValueError("Face detection confidence must be between 0 and 1.")

    path = _detector_model_path(
        store=store,
        cancel_event=cancel_event,
        allow_download=allow_download,
    )
    detector = _detector_for(cv2, path, w, h, min_confidence)
    bgr = np.ascontiguousarray(rgb[:, :, :3][:, :, ::-1], dtype=np.uint8)
    boxes: list[FaceBox] = []
    mask = np.zeros((h, w), dtype=bool)
    try:
        _detected, faces = detector.detect(bgr)
    except Exception as error:  # noqa: BLE001 - normalize native OpenCV diagnostics
        raise FaceDetectionUnavailable(f"YuNet face detection failed: {error}") from error

    if faces is not None:
        for face in faces:
            x = max(0, int(round(float(face[0]))))
            y = max(0, int(round(float(face[1]))))
            bw = min(w - x, int(round(float(face[2]))))
            bh = min(h - y, int(round(float(face[3]))))
            confidence = float(face[-1])
            if bw <= 0 or bh <= 0 or confidence < min_confidence:
                continue
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
