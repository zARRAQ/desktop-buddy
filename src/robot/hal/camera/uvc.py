"""USB (UVC) cameras through OpenCV's V4L2 backend, and the same class for file replay."""

from __future__ import annotations

import logging
import time
from typing import Any

import cv2
import numpy as np

from robot.hal.camera.base import Camera, CameraInfo, Frame, apply_orientation

log = logging.getLogger(__name__)

FORMAT_PREFERENCE = ("MJPG", "NV12", "YUYV")


def fourcc_to_str(code: float) -> str:
    v = int(code)
    return "".join(chr((v >> (8 * i)) & 0xFF) for i in range(4)).strip("\0")


class OpenCvCamera(Camera):
    def __init__(
        self,
        device: str | int,
        *,
        width: int = 640,
        height: int = 480,
        fps: int = 30,
        fmt: str = "auto",
        rotation: int = 0,
        flip: str = "none",
        backend_name: str = "uvc",
        loop: bool = True,
    ) -> None:
        self.device = device
        self.info = CameraInfo(backend=backend_name, device=str(device), width=width, height=height, fps=fps)
        self._want = (width, height, fps, fmt)
        self._rotation = rotation
        self._flip = flip
        self._cap: Any | None = None
        self._seq = 0
        self._loop = loop
        self._failures = 0
        self._next_retry = 0.0
        self._is_file = isinstance(device, str) and not device.startswith("/dev/") and not device.isdigit()
        self._last_ts = 0.0

    def open(self) -> None:
        width, height, fps, fmt = self._want
        if self._is_file or (isinstance(self.device, str) and self.device.startswith("rtsp")):
            cap = cv2.VideoCapture(self.device)
        else:
            index = int(self.device) if isinstance(self.device, str) and self.device.isdigit() else self.device
            if isinstance(index, str) and index.startswith("/dev/video"):
                index = int(index.removeprefix("/dev/video"))
            cap = cv2.VideoCapture(index, cv2.CAP_V4L2)
            candidates = [fmt] if fmt != "auto" else list(FORMAT_PREFERENCE)
            for candidate in candidates:
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter.fourcc(*candidate))
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
                cap.set(cv2.CAP_PROP_FPS, fps)
                if fourcc_to_str(cap.get(cv2.CAP_PROP_FOURCC)) == candidate:
                    break
        if not cap.isOpened():
            raise RuntimeError(f"cannot open camera {self.device!r}")
        self._cap = cap
        self.info.width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or width
        self.info.height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or height
        self.info.fps = float(cap.get(cv2.CAP_PROP_FPS)) or fps
        self.info.format = fourcc_to_str(cap.get(cv2.CAP_PROP_FOURCC))
        if self.info.format == "YUYV" and fmt == "auto":
            self.info.notes.append("negotiated YUYV: expect low fps over USB; set camera.format: MJPG")
        log.info(
            "camera: %s %s %dx%d @ %.0f fps format=%s",
            self.info.backend,
            self.device,
            self.info.width,
            self.info.height,
            self.info.fps,
            self.info.format or "?",
        )

    def read(self, timeout: float = 1.0) -> Frame | None:
        if self._cap is None:
            now = time.monotonic()
            if now < self._next_retry:
                time.sleep(min(timeout, self._next_retry - now))
                return None
            try:
                self.open()
            except (RuntimeError, cv2.error) as exc:
                self._failures += 1
                self._next_retry = now + min(10.0, 0.5 * 2**self._failures)
                log.warning("camera reopen failed (%s); retry in %.1fs", exc, self._next_retry - now)
                return None
        cap = self._cap
        if cap is None:
            return None
        ok, bgr = cap.read()
        if not ok or bgr is None:
            if self._is_file and self._loop:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, bgr = cap.read()
            if not ok or bgr is None:
                self._failures += 1
                log.warning("camera read failed (%d); reopening", self._failures)
                self.close()
                self._next_retry = time.monotonic() + min(10.0, 0.5 * 2**self._failures)
                return None
        self._failures = 0
        if self._is_file and self.info.fps > 0:
            # pace playback to the file's frame rate
            now = time.monotonic()
            wait = self._last_ts + 1.0 / self.info.fps - now
            if wait > 0:
                time.sleep(wait)
            self._last_ts = time.monotonic()
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        rgb = apply_orientation(rgb, self._rotation, self._flip)
        self._seq += 1
        return Frame(image=rgb, ts=time.time(), seq=self._seq)

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    @property
    def healthy(self) -> bool:
        return self._cap is not None


def rgb_from_bgr(bgr: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
