"""Camera detection cascade: configured -> CSI (picamera2) -> UVC (/dev/video*) -> RTSP -> null."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from robot.core.config import CameraConfig

# Nodes the Pi's own SoC exposes: the CSI front end, the ISP and the legacy codec blocks.
# They open fine through OpenCV but never deliver frames from a webcam. On a Pi 5 they
# occupy /dev/video0 to /dev/video7 and /dev/video19 upward, so a USB webcam lands somewhere
# in between and a "first node that opens" rule picks the wrong one.
INTERNAL_NODE_NAMES = ("rp1-cfe", "pispbe", "pisp", "bcm2835", "unicam", "rpi-hevc", "rpivid", "csi2")


@dataclass
class VideoNode:
    device: str  # /dev/video0
    name: str
    capture: bool  # advertises V4L2_CAP_VIDEO_CAPTURE
    usb: bool = False  # sits on a USB bus (a webcam) rather than the SoC

    @property
    def internal(self) -> bool:
        low = self.name.lower()
        return any(k in low for k in INTERNAL_NODE_NAMES)


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
        """What ``--save`` writes. A UVC node number is not saved: /dev/videoN changes with
        plug order and boot, and detection finds the webcam again in a few hundred ms."""
        out: dict[str, object] = {"backend": self.backend}
        if self.device and self.backend != "uvc":
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
        usb = _on_usb_bus(path)
        # skip the probe for the SoC's own nodes; opening them costs time and proves nothing
        internal = any(k in name.lower() for k in INTERNAL_NODE_NAMES)
        capture = False if internal else _is_capture_node(dev)
        out.append(VideoNode(str(dev), name, capture, usb))
    return out


def _on_usb_bus(sysfs_node: Path) -> bool:
    """True when the sysfs device chain of a video node passes through a USB bus."""
    try:
        real = (sysfs_node / "device").resolve()
    except OSError:
        return False
    return "/usb" in str(real) or any(part.startswith("usb") for part in real.parts)


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


def pick_video_node(nodes: list[VideoNode], wanted: str = "auto") -> VideoNode | None:
    """The node a webcam most likely is: the requested one if it captures, else the first
    USB capture node, else the first non-internal capture node. Never an internal node."""
    usable = [n for n in nodes if n.capture and not n.internal]
    if not usable:
        return None
    if wanted != "auto":
        exact = next((n for n in usable if n.device == wanted), None)
        if exact is not None:
            return exact
    return next((n for n in usable if n.usb), usable[0])


def detect_camera(cfg: CameraConfig, probes: CameraProbes | None = None) -> CameraDetection:
    p = probes or CameraProbes()
    if cfg.backend == "uvc" and cfg.device == "auto":
        # backend pinned (for example by `robot camera detect --save`) but the node is not:
        # /dev/video0 is the CSI front end on a Pi 5, so scan instead of assuming it
        chosen = pick_video_node(p.video_nodes())
        if chosen is None:
            return CameraDetection("null", "", ["camera.backend=uvc configured but no webcam node found"])
        return CameraDetection("uvc", chosen.device, [f"camera.backend=uvc configured; {_describe(chosen)}"])
    if cfg.backend != "auto":
        return CameraDetection(
            cfg.backend, cfg.device if cfg.device != "auto" else "", [f"camera.backend={cfg.backend} configured"]
        )
    if cfg.device != "auto" and cfg.device.startswith("rtsp"):
        return CameraDetection("rtsp", cfg.device, ["camera.device is an rtsp:// URL"])
    csi = p.csi_cameras()
    if csi:
        return CameraDetection("csi", "0", [f"picamera2 reports: {', '.join(csi)}"])
    chosen = pick_video_node(p.video_nodes(), cfg.device)
    if chosen is not None:
        reasons = [_describe(chosen)]
        if cfg.device not in ("auto", chosen.device):
            reasons.append(f"camera.device={cfg.device} is not a capture node; using {chosen.device}")
        return CameraDetection("uvc", chosen.device, reasons)
    return CameraDetection("null", "", ["no camera found: perception disabled"])


def _describe(n: VideoNode) -> str:
    return f"V4L2 capture device {n.device} ({n.name or 'unnamed'}{', USB' if n.usb else ''})"
