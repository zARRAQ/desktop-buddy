"""SCRFD output decoding, backend-agnostic.

SCRFD emits, for each stride (8, 16, 32), a score map, a box-distance map and a keypoint
map with two anchors per cell. Compiled models (Hailo) expose them as nine NHWC tensors.
This decoder groups outputs by spatial size and identifies heads by channel count, so it
does not depend on layer names, which differ between model zoo versions.
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np

from robot.perception.base import FaceDetection, nms

NUM_ANCHORS = 2


def _sigmoid_if_needed(x: np.ndarray) -> np.ndarray:
    # Some compiled graphs include the sigmoid, some do not; scores > 1 or < 0 mean logits.
    if x.min() < 0.0 or x.max() > 1.0:
        return 1.0 / (1.0 + np.exp(-x))
    return x


def decode_scrfd(
    outputs: Mapping[str, np.ndarray],
    input_size: tuple[int, int],
    *,
    score_threshold: float = 0.5,
    nms_threshold: float = 0.4,
) -> list[FaceDetection]:
    """``outputs``: name -> array shaped (H, W, C) or (1, H, W, C). Returns detections in
    model-input pixel coordinates."""
    _in_w, in_h = input_size
    groups: dict[tuple[int, int], dict[str, np.ndarray]] = {}
    for name, arr in outputs.items():
        a = np.asarray(arr, dtype=np.float32)
        if a.ndim == 4:
            a = a[0]
        if a.ndim != 3:
            continue
        h, w, c = a.shape
        head = groups.setdefault((h, w), {})
        if c == NUM_ANCHORS:
            head["score"] = a
        elif c == 4 * NUM_ANCHORS:
            head["bbox"] = a
        elif c == 10 * NUM_ANCHORS:
            head["kps"] = a
        else:
            head.setdefault(f"unknown_{name}", a)

    dets: list[FaceDetection] = []
    for (h, w), head in groups.items():
        if "score" not in head or "bbox" not in head:
            continue
        stride = in_h / h
        scores = _sigmoid_if_needed(head["score"]).reshape(h, w, NUM_ANCHORS)
        boxes = head["bbox"].reshape(h, w, NUM_ANCHORS, 4)
        kps = head["kps"].reshape(h, w, NUM_ANCHORS, 5, 2) if "kps" in head else None
        ys, xs, anchors = np.nonzero(scores >= score_threshold)
        for y, x, anc in zip(ys, xs, anchors, strict=True):
            cx, cy = x * stride, y * stride
            left, top, right, bottom = boxes[y, x, anc] * stride
            bbox = (float(cx - left), float(cy - top), float(cx + right), float(cy + bottom))
            landmarks = None
            if kps is not None:
                landmarks = (kps[y, x, anc] * stride + np.array([cx, cy], dtype=np.float32)).astype(np.float32)
            dets.append(FaceDetection(bbox=bbox, score=float(scores[y, x, anc]), landmarks=landmarks))
    return nms(dets, nms_threshold)


def encode_scrfd_for_test(
    faces: list[tuple[tuple[float, float, float, float], float, np.ndarray | None]],
    input_size: tuple[int, int],
    strides: tuple[int, ...] = (8, 16, 32),
) -> dict[str, np.ndarray]:
    """Inverse of :func:`decode_scrfd` for one face per cell, used by tests."""
    in_w, in_h = input_size
    outputs: dict[str, np.ndarray] = {}
    for s in strides:
        h, w = in_h // s, in_w // s
        outputs[f"score_{s}"] = np.zeros((h, w, NUM_ANCHORS), dtype=np.float32)
        outputs[f"bbox_{s}"] = np.zeros((h, w, NUM_ANCHORS * 4), dtype=np.float32)
        outputs[f"kps_{s}"] = np.zeros((h, w, NUM_ANCHORS * 10), dtype=np.float32)
    for bbox, score, lm in faces:
        x1, y1, x2, y2 = bbox
        size = max(x2 - x1, y2 - y1)
        s = 8 if size < 64 else (16 if size < 160 else 32)
        h, w = in_h // s, in_w // s
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        gx, gy = min(w - 1, int(cx // s)), min(h - 1, int(cy // s))
        ax, ay = gx * s, gy * s
        outputs[f"score_{s}"][gy, gx, 0] = score
        outputs[f"bbox_{s}"][gy, gx, 0:4] = np.array([ax - x1, ay - y1, x2 - ax, y2 - ay]) / s
        if lm is not None:
            outputs[f"kps_{s}"][gy, gx, 0:10] = ((lm - np.array([ax, ay])) / s).reshape(-1)
    return outputs
