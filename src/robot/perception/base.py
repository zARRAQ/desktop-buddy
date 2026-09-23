"""Detector and embedder interfaces shared by the CPU and Hailo backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np


@dataclass
class FaceDetection:
    bbox: tuple[float, float, float, float]  # x1, y1, x2, y2 in frame pixels
    score: float
    landmarks: np.ndarray | None = None  # 5 x 2: right eye, left eye, nose, mouth right, mouth left

    @property
    def width(self) -> float:
        return self.bbox[2] - self.bbox[0]

    @property
    def height(self) -> float:
        return self.bbox[3] - self.bbox[1]

    @property
    def center(self) -> tuple[float, float]:
        return (self.bbox[0] + self.bbox[2]) / 2.0, (self.bbox[1] + self.bbox[3]) / 2.0

    def normalised(self, frame_w: int, frame_h: int) -> tuple[float, float, float, float]:
        x1, y1, x2, y2 = self.bbox
        return (x1 / frame_w, y1 / frame_h, x2 / frame_w, y2 / frame_h)


class FaceDetector(ABC):
    name: str = "abstract"

    @abstractmethod
    def detect(self, image: np.ndarray) -> list[FaceDetection]:
        """``image`` is HxWx3 RGB uint8. Returns detections sorted by score, best first."""

    def close(self) -> None: ...


class FaceEmbedder(ABC):
    name: str = "abstract"
    dim: int = 0
    default_threshold: float = 0.5  # cosine similarity for "same person"

    @abstractmethod
    def embed(self, image: np.ndarray, det: FaceDetection) -> np.ndarray:
        """L2-normalised float32 vector of length ``dim``."""

    def close(self) -> None: ...


@dataclass
class PerceptionBackend:
    name: str
    detector: FaceDetector
    embedder: FaceEmbedder

    def close(self) -> None:
        self.detector.close()
        self.embedder.close()


def iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    if inter <= 0.0:
        return 0.0
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / max(1e-9, area_a + area_b - inter)


def nms(dets: list[FaceDetection], threshold: float = 0.4) -> list[FaceDetection]:
    ordered = sorted(dets, key=lambda d: d.score, reverse=True)
    keep: list[FaceDetection] = []
    for d in ordered:
        if all(iou(d.bbox, k.bbox) < threshold for k in keep):
            keep.append(d)
    return keep


def normalise(vec: np.ndarray) -> np.ndarray:
    v = np.asarray(vec, dtype=np.float32).ravel()
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v
