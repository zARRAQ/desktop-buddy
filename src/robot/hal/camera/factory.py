"""Build a Camera from configuration plus detection."""

from __future__ import annotations

import logging

from robot.core.config import CameraConfig
from robot.hal.camera.base import Camera
from robot.hal.camera.detect import CameraDetection, CameraProbes, detect_camera
from robot.hal.camera.null import NullCamera

log = logging.getLogger(__name__)


def open_camera(
    cfg: CameraConfig, *, probes: CameraProbes | None = None, detection: CameraDetection | None = None
) -> Camera:
    det = detection or detect_camera(cfg, probes)
    for r in det.reasons:
        log.info("camera detect: %s", r)
    try:
        cam = _build(cfg, det)
        cam.open()
        return cam
    except Exception as exc:
        log.error("camera backend %s failed (%s); perception disabled", det.backend, exc)
        cam = NullCamera()
        cam.open()
        return cam


def _build(cfg: CameraConfig, det: CameraDetection) -> Camera:
    if det.backend == "csi":
        from robot.hal.camera.csi import CsiCamera

        index = int(det.device) if det.device.isdigit() else 0
        return CsiCamera(index, width=cfg.width, height=cfg.height, fps=cfg.fps, rotation=cfg.rotation, flip=cfg.flip)
    if det.backend in ("uvc", "rtsp", "file"):
        from robot.hal.camera.uvc import OpenCvCamera

        device = det.device or (cfg.device if cfg.device != "auto" else "/dev/video0")
        return OpenCvCamera(
            device,
            width=cfg.width,
            height=cfg.height,
            fps=cfg.fps,
            fmt=cfg.format,
            rotation=cfg.rotation,
            flip=cfg.flip,
            backend_name=det.backend,
            loop=cfg.loop_file,
        )
    if det.backend == "synthetic":
        from robot.hal.camera.synthetic import SyntheticCamera

        return SyntheticCamera(width=cfg.width, height=cfg.height, fps=cfg.fps)
    return NullCamera()
