"""Display backends behind one interface, plus detection."""

from robot.hal.display.base import Display, PanelInfo
from robot.hal.display.detect import DetectionResult, Probes, detect_display
from robot.hal.display.factory import open_display

__all__ = ["DetectionResult", "Display", "PanelInfo", "Probes", "detect_display", "open_display"]
