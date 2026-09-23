"""Five-point face alignment to the 112x112 ArcFace template (InsightFace's ``norm_crop``)."""

from __future__ import annotations

import cv2
import numpy as np

# right eye, left eye, nose tip, right mouth corner, left mouth corner (image coordinates)
ARCFACE_TEMPLATE = np.array(
    [
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ],
    dtype=np.float32,
)


def similarity_transform(src: np.ndarray, dst: np.ndarray = ARCFACE_TEMPLATE) -> np.ndarray:
    """2x3 similarity (rotation, uniform scale, translation) mapping src landmarks onto dst."""
    src = np.asarray(src, dtype=np.float32).reshape(5, 2)
    m, _ = cv2.estimateAffinePartial2D(src, dst, method=cv2.LMEDS)
    if m is None:
        # degenerate landmarks: fall back to a translation that centres the points
        m = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32)
        m[:, 2] = dst.mean(axis=0) - src.mean(axis=0)
    return np.asarray(m, dtype=np.float32)


def align_face(image: np.ndarray, landmarks: np.ndarray, size: int = 112) -> np.ndarray:
    m = similarity_transform(landmarks)
    return cv2.warpAffine(image, m, (size, size), flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0))


def crop_face(
    image: np.ndarray, bbox: tuple[float, float, float, float], size: int = 112, margin: float = 0.2
) -> np.ndarray:
    """Landmark-free fallback: square crop with margin, resized."""
    h, w = image.shape[:2]
    x1, y1, x2, y2 = bbox
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    half = max(x2 - x1, y2 - y1) * (0.5 + margin)
    ax, ay = int(max(0, cx - half)), int(max(0, cy - half))
    bx, by = int(min(w, cx + half)), int(min(h, cy + half))
    if bx <= ax or by <= ay:
        return np.zeros((size, size, 3), dtype=np.uint8)
    return cv2.resize(image[ay:by, ax:bx], (size, size), interpolation=cv2.INTER_LINEAR)
