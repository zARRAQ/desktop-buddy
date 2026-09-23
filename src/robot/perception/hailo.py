"""Hailo face pipeline: SCRFD (detector) + ArcFace MobileFaceNet (512-d embedder).

Uses the same models as Hailo's own ``hailo-apps`` face recognition pipeline (Model Zoo
v2.17.0 for Hailo-8/8L, v5.2.0 for Hailo-10H), driven directly through the HailoRT Python
``InferModel`` API rather than GStreamer, so perception is the same class of object as the
CPU backend and the rest of the stack does not know which one is running.

Not yet exercised on real hardware. The decoder and alignment are unit-tested; the HailoRT
calls follow the 4.2x API (``VDevice.create_infer_model``, ``configure``, bindings).
"""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from typing import Any

import numpy as np

from robot.core import paths
from robot.hal.camera.letterbox import letterbox
from robot.perception.align import align_face, crop_face
from robot.perception.base import FaceDetection, FaceDetector, FaceEmbedder, PerceptionBackend, normalise
from robot.perception.scrfd import decode_scrfd

log = logging.getLogger(__name__)

ARCFACE_COSINE_THRESHOLD = 0.5  # hailo-apps' operating point

DETECTOR_FOR_ARCH = {"hailo8": "scrfd_10g", "hailo8l": "scrfd_2.5g", "hailo10h": "scrfd_10g"}
EMBEDDER_NAME = "arcface_mobilefacenet"


def detect_arch() -> str | None:
    """``hailo8l`` / ``hailo8`` / ``hailo10h`` from the device, or None without HailoRT."""
    try:
        from hailo_platform import Device

        devices = Device.scan()
        if not devices:
            return None
        with Device(devices[0]) as dev:
            arch = str(dev.control.identify().device_architecture)
    except Exception as exc:
        log.debug("hailo arch probe failed: %s", exc)
        return None
    a = arch.lower().replace("_", "").replace("-", "")
    if "10h" in a:
        return "hailo10h"
    if "8l" in a:
        return "hailo8l"
    if "8" in a:
        return "hailo8"
    return None


class HailoModel:
    """One HEF configured for synchronous single-image inference."""

    def __init__(self, hef_path: Path, vdevice: Any) -> None:
        from hailo_platform import FormatType

        self.path = hef_path
        self.infer_model = vdevice.create_infer_model(str(hef_path))
        self.infer_model.set_batch_size(1)
        self.infer_model.input().set_format_type(FormatType.UINT8)
        for name in self.infer_model.output_names:
            self.infer_model.output(name).set_format_type(FormatType.FLOAT32)
        self.configured = self.infer_model.configure()
        shape = self.infer_model.input().shape  # (H, W, C)
        self.input_h, self.input_w = int(shape[0]), int(shape[1])
        self.output_names: list[str] = list(self.infer_model.output_names)

    def run(self, image_hwc_uint8: np.ndarray, timeout_ms: int = 1000) -> dict[str, np.ndarray]:
        bindings = self.configured.create_bindings()
        bindings.input().set_buffer(np.ascontiguousarray(image_hwc_uint8))
        outputs: dict[str, np.ndarray] = {}
        for name in self.output_names:
            buf = np.empty(self.infer_model.output(name).shape, dtype=np.float32)
            bindings.output(name).set_buffer(buf)
            outputs[name] = buf
        self.configured.run([bindings], timeout_ms)
        return outputs

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.configured.shutdown()


class ScrfdHailoDetector(FaceDetector):
    name = "scrfd"

    def __init__(self, model: HailoModel, *, score_threshold: float = 0.5) -> None:
        self.model = model
        self.score_threshold = score_threshold

    def detect(self, image: np.ndarray) -> list[FaceDetection]:
        tensor, lb = letterbox(image, self.model.input_w, self.model.input_h)
        outputs = self.model.run(tensor)
        dets = decode_scrfd(outputs, (self.model.input_w, self.model.input_h), score_threshold=self.score_threshold)
        out: list[FaceDetection] = []
        for d in dets:
            bbox = lb.bbox_to_original(*d.bbox)
            lm = None
            if d.landmarks is not None:
                lm = np.array([lb.to_original(x, y) for x, y in d.landmarks], dtype=np.float32)
            out.append(FaceDetection(bbox=bbox, score=d.score, landmarks=lm))
        return out


class ArcfaceHailoEmbedder(FaceEmbedder):
    name = "arcface"
    dim = 512
    default_threshold = ARCFACE_COSINE_THRESHOLD

    def __init__(self, model: HailoModel) -> None:
        self.model = model

    def embed(self, image: np.ndarray, det: FaceDetection) -> np.ndarray:
        size = self.model.input_w
        face = align_face(image, det.landmarks, size) if det.landmarks is not None else crop_face(image, det.bbox, size)
        outputs = self.model.run(face)
        vec = next(iter(outputs.values()))
        return normalise(vec)


def hef_paths(arch: str, models_dir: Path | None = None) -> tuple[Path, Path]:
    d = (models_dir or paths.models_dir()) / "hailo" / arch
    return d / f"{DETECTOR_FOR_ARCH[arch]}.hef", d / f"{EMBEDDER_NAME}.hef"


def open_hailo_backend(
    arch: str = "auto", models_dir: Path | None = None, *, score_threshold: float = 0.5
) -> PerceptionBackend:
    from hailo_platform import HailoSchedulingAlgorithm, VDevice

    if arch == "auto":
        detected = detect_arch()
        if detected is None:
            raise RuntimeError("no Hailo device found (hailortcli fw-control identify)")
        arch = detected
    det_hef, emb_hef = hef_paths(arch, models_dir)
    missing = [str(p) for p in (det_hef, emb_hef) if not p.exists()]
    if missing:
        raise FileNotFoundError(f"Hailo models missing: {', '.join(missing)}. Run `robot provision`.")
    params = VDevice.create_params()
    params.scheduling_algorithm = HailoSchedulingAlgorithm.ROUND_ROBIN
    vdevice = VDevice(params)
    detector = ScrfdHailoDetector(HailoModel(det_hef, vdevice), score_threshold=score_threshold)
    embedder = ArcfaceHailoEmbedder(HailoModel(emb_hef, vdevice))
    log.info("perception backend: hailo (%s, %s + %s)", arch, det_hef.name, emb_hef.name)
    backend = PerceptionBackend(f"hailo-{arch}", detector, embedder)
    backend.name = "hailo"  # embeddings are comparable across Hailo archs (same arcface model)
    return backend
