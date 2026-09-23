"""Synthetic camera for development without a webcam.

Produces a moving "person" (a skin-toned blob with two dark eyes on a body) so the camera
pipeline, letterboxing and benchmarking can run anywhere. Real detectors will not usually
fire on it; that is what the ``file`` backend with a recorded clip is for. Optionally pastes
a real portrait image (``image=``) so the CPU detector has something to find.
"""

from __future__ import annotations

import math
import time

import cv2
import numpy as np

from robot.hal.camera.base import Camera, CameraInfo, Frame


class SyntheticCamera(Camera):
    def __init__(self, *, width: int = 640, height: int = 480, fps: int = 30, image: np.ndarray | None = None) -> None:
        self.info = CameraInfo(
            backend="synthetic", device="synthetic", width=width, height=height, fps=fps, format="RGB"
        )
        self._seq = 0
        self._t0 = time.monotonic()
        self._last = 0.0
        self._image = image
        self.person_x = 0.5  # normalised, settable by the simulator
        self.person_visible = True

    def open(self) -> None:
        pass

    def read(self, timeout: float = 1.0) -> Frame | None:
        period = 1.0 / max(1, self.info.fps)
        now = time.monotonic()
        wait = self._last + period - now
        if wait > 0:
            time.sleep(min(wait, timeout))
        self._last = time.monotonic()
        w, h = self.info.width, self.info.height
        img = np.zeros((h, w, 3), dtype=np.uint8)
        img[:] = (40, 44, 52)
        t = self._last - self._t0
        if self.person_visible:
            cx = int(self.person_x * w) if self._image is None else int(w * (0.5 + 0.3 * math.sin(t * 0.5)))
            if self._image is not None:
                ih, iw = self._image.shape[:2]
                scale = min(0.6 * h / ih, 0.6 * w / iw)
                face = cv2.resize(self._image, (int(iw * scale), int(ih * scale)))
                fh, fw = face.shape[:2]
                x0 = max(0, min(w - fw, cx - fw // 2))
                y0 = max(0, (h - fh) // 2)
                img[y0 : y0 + fh, x0 : x0 + fw] = face
            else:
                cy = int(h * 0.45)
                r = int(h * 0.12)
                cv2.circle(img, (cx, cy), r, (224, 172, 140), -1)
                cv2.circle(img, (cx - r // 3, cy - r // 5), r // 6, (30, 30, 30), -1)
                cv2.circle(img, (cx + r // 3, cy - r // 5), r // 6, (30, 30, 30), -1)
                cv2.rectangle(img, (cx - r, cy + r), (cx + r, h), (60, 90, 160), -1)
        self._seq += 1
        return Frame(image=img, ts=time.time(), seq=self._seq)

    def close(self) -> None:
        pass
