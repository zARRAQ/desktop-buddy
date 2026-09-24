"""Open the configured camera for a one-off command (test, bench, enrol, record).

The services get OpenMV frames through the bridge process. A single command has no bridge
running, so when the camera is the OpenMV it opens the board's serial port itself and feeds
a :class:`BusCamera` from it. The returned camera behaves like any other; close it when done.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from robot.core.config import RobotConfig
from robot.hal.camera.base import Camera
from robot.hal.camera.factory import open_camera

log = logging.getLogger(__name__)


class DirectCamera(Camera):
    """A camera plus, when needed, the OpenMV link that feeds it. Closing closes both."""

    def __init__(self, camera: Camera, link: object | None) -> None:
        self._camera = camera
        self._link = link
        self.info = camera.info

    def open(self) -> None:
        self._camera.open()

    def read(self, timeout: float = 1.0):  # type: ignore[no-untyped-def]
        return self._camera.read(timeout)

    def close(self) -> None:
        self._camera.close()
        if self._link is not None:
            self._link.close()  # type: ignore[attr-defined]

    @property
    def healthy(self) -> bool:
        return self._camera.healthy

    @property
    def via_openmv(self) -> bool:
        return self._link is not None


def open_camera_direct(cfg: RobotConfig, *, on_log: Callable[[str], None] | None = None) -> DirectCamera:
    """The configured camera; an OpenMV board is driven directly over its serial port."""
    cam = open_camera(cfg.camera)
    if not (cfg.openmv.enabled and cam.info.backend in ("null", "bus")):
        return DirectCamera(cam, None)
    from robot.hal.camera.bus import BusCamera
    from robot.hal.openmv.link import OpenMvLink

    cam.close()
    o = cfg.openmv
    bus_cam = BusCamera(o.width, o.height)
    bus_cam.open()
    link = OpenMvLink(
        None if o.port == "auto" else o.port,
        on_frame=lambda w, h, seq, jpeg: bus_cam.push({"width": w, "height": h, "seq": seq, "jpeg": jpeg}),
        on_log=on_log or (lambda text: log.info("board: %s", text)),
    )
    try:
        link.open()
    except Exception as exc:
        log.error("OpenMV: %s", exc)
        bus_cam.info.backend = "null"
        bus_cam.info.notes.append(str(exc))
        return DirectCamera(bus_cam, None)
    link.send_config(o.width, o.height, o.jpeg_quality, o.fps, o.leds)
    log.info("OpenMV on %s: %dx%d @ %d fps requested", link.port_name, o.width, o.height, o.fps)
    return DirectCamera(bus_cam, link)
