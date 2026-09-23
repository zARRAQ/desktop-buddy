"""Raspberry Pi camera modules through picamera2 (apt package, seen via system site-packages).

Not yet exercised on real hardware. Written against the picamera2 documentation:
``Picamera2.global_camera_info()``, ``create_video_configuration``, ``capture_array``.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import numpy as np

from robot.hal.camera.base import Camera, CameraInfo, Frame, apply_orientation

log = logging.getLogger(__name__)


class CsiCamera(Camera):
    def __init__(
        self,
        index: int = 0,
        *,
        width: int = 640,
        height: int = 480,
        fps: int = 30,
        rotation: int = 0,
        flip: str = "none",
    ) -> None:
        self.index = index
        self.info = CameraInfo(
            backend="csi", device=f"csi{index}", width=width, height=height, fps=fps, format="RGB888"
        )
        self._rotation = rotation
        self._flip = flip
        self._cam: Any | None = None
        self._seq = 0

    def open(self) -> None:
        from picamera2 import Picamera2

        cam = Picamera2(self.index)
        config = cam.create_video_configuration(
            main={"size": (self.info.width, self.info.height), "format": "RGB888"},
            controls={"FrameRate": self.info.fps},
        )
        cam.configure(config)
        cam.start()
        self._cam = cam
        log.info("camera: csi%d %dx%d @ %d fps", self.index, self.info.width, self.info.height, self.info.fps)

    def read(self, timeout: float = 1.0) -> Frame | None:
        if self._cam is None:
            return None
        arr: np.ndarray = self._cam.capture_array("main")
        # picamera2 follows libcamera's little-endian naming: "RGB888" arrives as BGR in memory.
        rgb = np.ascontiguousarray(arr[..., ::-1]) if arr.shape[-1] == 3 else np.ascontiguousarray(arr[..., 2::-1])
        rgb = apply_orientation(rgb, self._rotation, self._flip)
        self._seq += 1
        return Frame(image=rgb, ts=time.time(), seq=self._seq)

    def close(self) -> None:
        if self._cam is not None:
            try:
                self._cam.stop()
                self._cam.close()
            finally:
                self._cam = None

    @property
    def healthy(self) -> bool:
        return self._cam is not None
