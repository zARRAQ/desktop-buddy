"""Camera detection cascade: configured -> CSI (picamera2) -> UVC (/dev/video*) -> RTSP -> null."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from robot.core.config import CameraConfig


@dataclass
class VideoNode:
    device: str  # /dev/video0
    name: str
    capture: bool  # advertises V4L2_CAP_VIDEO_CAPTURE


@dataclass
class CameraProbes:
    csi_cameras: Callable[[], list[str]] = field(default_factory=lambda: real_csi_cameras)
    video_nodes: Callable[[], list[VideoNode]] = field(default_factory=lambda: real_video_nodes)


@dataclass
class CameraDetection:
    backend: str
    device: str = ""
    reasons: list[str] = field(default_factory=list)

    def as_local_config(self) -> dict[str, object]:
        out: dict[str, object] = {"backend": self.backend}
        if self.device:
            out["device"] = self.device
        return out


def real_csi_cameras() -> list[str]:
    try:
        from picamera2 import Picamera2

        return [str(c.get("Model", c.get("Id", "?"))) for c in Picamera2.global_camera_info()]
    except Exception:
        return []


def real_video_nodes(root: Path = Path("/sys/class/video4linux")) -> list[VideoNode]:
    out: list[VideoNode] = []
    if not root.exists():
        return out
    for path in sorted(root.glob("video*"), key=lambda p: int(re.sub(r"\D", "", p.name) or 0)):
        dev = Path("/dev") / path.name
        if not dev.exists():
            continue
        try:
            name = (path / "name").read_text().strip()
        except OSError:
            name = ""
        capture = _is_capture_node(dev)
        out.append(VideoNode(str(dev), name, capture))
    return out


def _is_capture_node(dev: Path) -> bool:
    """A UVC camera exposes two nodes; only the first has the capture capability.

    Checking via the V4L2 ioctl is the right way, but needs a fcntl dance; OpenCV opening
    the node successfully with a real frame size is a reliable proxy that costs ~100 ms.
    """
    try:
        import cv2

        idx = int(dev.name.removeprefix("video"))
        cap = cv2.VideoCapture(idx, cv2.CAP_V4L2)
        ok = cap.isOpened() and cap.get(cv2.CAP_PROP_FRAME_WIDTH) > 0
        cap.release()
        return bool(ok)
    except Exception:
        return False


def detect_camera(cfg: CameraConfig, probes: CameraProbes | None = None) -> CameraDetection:
    p = probes or CameraProbes()
    if cfg.backend != "auto":
        return CameraDetection(
            cfg.backend, cfg.device if cfg.device != "auto" else "", [f"camera.backend={cfg.backend} configured"]
        )
    if cfg.device != "auto" and cfg.device.startswith("rtsp"):
        return CameraDetection("rtsp", cfg.device, ["camera.device is an rtsp:// URL"])
    csi = p.csi_cameras()
    if csi:
        return CameraDetection("csi", "0", [f"picamera2 reports: {', '.join(csi)}"])
    nodes = [n for n in p.video_nodes() if n.capture]
    if nodes:
        if cfg.device != "auto":
            chosen = next((n for n in nodes if n.device == cfg.device), nodes[0])
        else:
            chosen = nodes[0]
        return CameraDetection("uvc", chosen.device, [f"V4L2 capture device {chosen.device} ({chosen.name})"])
    return CameraDetection("null", "", ["no camera found: perception disabled"])
