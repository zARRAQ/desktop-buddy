"""CPU face pipeline: OpenCV YuNet (detector) + SFace (128-d embedder).

Both models come from the OpenCV Zoo (Apache-2.0) and the runtimes are built into
``cv2`` (``FaceDetectorYN``, ``FaceRecognizerSF``), so a laptop with no accelerator runs the
same perception service as the robot. On a Pi 5 this manages roughly 10 to 15 fps at
640x480 on one core, which is usable while a Hailo is not available.

Model files: ``face_detection_yunet_2023mar.onnx`` and ``face_recognition_sface_2021dec.onnx``
in the models directory (see ``robot provision``).
"""

from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np

from robot.core import paths
from robot.perception.base import FaceDetection, FaceDetector, FaceEmbedder, PerceptionBackend, normalise

log = logging.getLogger(__name__)

YUNET_FILE = "face_detection_yunet_2023mar.onnx"
SFACE_FILE = "face_recognition_sface_2021dec.onnx"
SFACE_COSINE_THRESHOLD = 0.363  # OpenCV's published operating point


def models_available(models_dir: Path | None = None) -> bool:
    d = models_dir or paths.models_dir()
    return (d / YUNET_FILE).exists() and (d / SFACE_FILE).exists()


class YuNetDetector(FaceDetector):
    name = "yunet"

    def __init__(
        self, model_path: Path, *, score_threshold: float = 0.6, nms_threshold: float = 0.3, top_k: int = 50
    ) -> None:
        self._det = cv2.FaceDetectorYN.create(str(model_path), "", (320, 320), score_threshold, nms_threshold, top_k)
        self._size: tuple[int, int] | None = None

    def detect(self, image: np.ndarray) -> list[FaceDetection]:
        h, w = image.shape[:2]
        if self._size != (w, h):
            self._det.setInputSize((w, h))
            self._size = (w, h)
        bgr = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
        _, faces = self._det.detect(bgr)
        out: list[FaceDetection] = []
        if faces is None:
            return out
        for row in faces:
            x, y, bw, bh = (float(v) for v in row[:4])
            lm = np.asarray(row[4:14], dtype=np.float32).reshape(5, 2)
            out.append(FaceDetection(bbox=(x, y, x + bw, y + bh), score=float(row[14]), landmarks=lm))
        out.sort(key=lambda d: d.score, reverse=True)
        return out


class SFaceEmbedder(FaceEmbedder):
    name = "sface"
    dim = 128
    default_threshold = SFACE_COSINE_THRESHOLD

    def __init__(self, model_path: Path) -> None:
        self._rec = cv2.FaceRecognizerSF.create(str(model_path), "")

    def embed(self, image: np.ndarray, det: FaceDetection) -> np.ndarray:
        bgr = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
        if det.landmarks is None:
            from robot.perception.align import crop_face

            face = crop_face(bgr, det.bbox, size=112)
        else:
            x1, y1, x2, y2 = det.bbox
            row = np.concatenate([[x1, y1, x2 - x1, y2 - y1], det.landmarks.reshape(-1), [det.score]]).astype(
                np.float32
            )
            face = self._rec.alignCrop(bgr, row)
        feat = self._rec.feature(face)
        return normalise(np.asarray(feat, dtype=np.float32))


def open_cpu_backend(models_dir: Path | None = None, *, score_threshold: float = 0.6) -> PerceptionBackend:
    d = models_dir or paths.models_dir()
    yunet, sface = d / YUNET_FILE, d / SFACE_FILE
    missing = [str(p) for p in (yunet, sface) if not p.exists()]
    if missing:
        raise FileNotFoundError(f"CPU face models missing: {', '.join(missing)}. Run `robot provision`.")
    log.info("perception backend: cpu (YuNet + SFace)")
    return PerceptionBackend("cpu", YuNetDetector(yunet, score_threshold=score_threshold), SFaceEmbedder(sface))
