"""Camera backends behind one interface, plus detection and letterboxing."""

from robot.hal.camera.base import Camera, CameraInfo, Frame
from robot.hal.camera.detect import CameraProbes, detect_camera
from robot.hal.camera.factory import open_camera
from robot.hal.camera.letterbox import Letterbox, letterbox

__all__ = [
    "Camera",
    "CameraInfo",
    "CameraProbes",
    "Frame",
    "Letterbox",
    "detect_camera",
    "letterbox",
    "open_camera",
]
