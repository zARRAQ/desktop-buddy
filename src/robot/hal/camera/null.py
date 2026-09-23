"""No camera: read() returns None after the timeout. Perception stays idle."""

from __future__ import annotations

import time

from robot.hal.camera.base import Camera, CameraInfo, Frame


class NullCamera(Camera):
    def __init__(self) -> None:
        self.info = CameraInfo(backend="null")

    def open(self) -> None:
        pass

    def read(self, timeout: float = 1.0) -> Frame | None:
        time.sleep(timeout)
        return None

    def close(self) -> None:
        pass
