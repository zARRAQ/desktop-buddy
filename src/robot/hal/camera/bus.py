"""Camera whose frames arrive as ``camera.frame`` bus messages (JPEG), published by the
OpenMV bridge service. The owning service calls :meth:`push` from ``on_message``."""

from __future__ import annotations

import threading
import time
from typing import Any

import cv2
import numpy as np

from robot.hal.camera.base import Camera, CameraInfo, Frame


class BusCamera(Camera):
    def __init__(self, width: int, height: int, *, source: str = "openmv") -> None:
        self.info = CameraInfo(backend="bus", device=source, width=width, height=height, format="JPEG")
        self._latest: Frame | None = None
        self._lock = threading.Lock()
        self.received = 0
        self.decode_errors = 0
        self.last_push = 0.0

    def open(self) -> None:
        return

    def push(self, data: dict[str, Any]) -> None:
        jpeg = data.get("jpeg")
        if not isinstance(jpeg, bytes | bytearray) or not jpeg:
            return
        img = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            self.decode_errors += 1
            return
        rgb = np.ascontiguousarray(img[:, :, ::-1])
        self.received += 1
        self.last_push = time.monotonic()
        self.info.width, self.info.height = int(rgb.shape[1]), int(rgb.shape[0])
        with self._lock:
            self._latest = Frame(rgb, ts=float(data.get("ts", time.time())), seq=int(data.get("seq", self.received)))

    def read(self, timeout: float = 1.0) -> Frame | None:
        """Non-blocking: the owning service pumps frames in between ticks, so waiting here
        would only delay them."""
        with self._lock:
            f, self._latest = self._latest, None
        return f

    def close(self) -> None:
        return

    @property
    def healthy(self) -> bool:
        return self.received == 0 or time.monotonic() - self.last_push < 5.0
