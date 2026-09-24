"""``openmv`` service: the one process that owns the board's USB serial port.

Camera JPEGs from the board are published as ``camera.frame`` for the perception service's
``bus`` camera; ``display.frame`` messages from the face service's ``bus`` display are sent
down to the board's LCD. If the board is missing or unplugged, the service keeps retrying
every few seconds and says so once.
"""

from __future__ import annotations

import contextlib
import queue
import time

from robot.core.bus import BusClient
from robot.core.config import RobotConfig
from robot.core.messages import CameraFrame, DisplayFrame, Envelope, OpenMvState
from robot.core.service import Service
from robot.hal.openmv.link import OpenMvLink

RETRY_S = 5.0


class OpenMvService(Service):
    name = "openmv"
    tick_hz = 60.0  # drains the frame queue; the board sets the real rate
    subscriptions = (DisplayFrame.TOPIC,)

    def __init__(self, config: RobotConfig, bus: BusClient, *, link: OpenMvLink | None = None) -> None:
        super().__init__(config, bus)
        self._link = link
        self.link: OpenMvLink | None = None
        self._frames: queue.Queue[tuple[int, int, int, bytes]] = queue.Queue(maxsize=8)
        self._next_retry = 0.0
        self._last_state = 0.0
        self._cam_count = 0
        self._lcd_count = 0
        self._window_start = time.monotonic()
        self.board = ""
        self.camera_fps = 0.0
        self.lcd_fps = 0.0
        self._warned = False

    # -- lifecycle ------------------------------------------------------------------------
    def setup(self) -> None:
        self.link = self._link or OpenMvLink(
            None if self.config.openmv.port == "auto" else self.config.openmv.port, on_frame=self._on_frame
        )
        self.link.bind(on_frame=self._on_frame, on_log=lambda s: self.log.info("board: %s", s), on_pong=self._on_pong)
        self._try_open()

    def _try_open(self) -> None:
        assert self.link is not None
        if self.link.open_:
            return
        try:
            self.link.open()
        except Exception as exc:
            if not self._warned:
                self.log.warning("OpenMV not available (%s); retrying every %.0fs", exc, RETRY_S)
                self._warned = True
            self._next_retry = time.monotonic() + RETRY_S
            return
        self._warned = False
        o = self.config.openmv
        self.link.send_config(o.width, o.height, o.jpeg_quality, o.fps, o.leds)
        self.link.ping()
        self.log.info(
            "OpenMV on %s: asked for %dx%d q%d @%d fps", self.link.port_name, o.width, o.height, o.jpeg_quality, o.fps
        )

    def _on_frame(self, w: int, h: int, seq: int, jpeg: bytes) -> None:
        # reader thread: hand over to the service thread, dropping the oldest when behind
        if self._frames.full():
            with contextlib.suppress(queue.Empty):
                self._frames.get_nowait()
        self._frames.put_nowait((w, h, seq, jpeg))

    def _on_pong(self, text: str) -> None:
        self.board = text
        self.log.info("board says: %s", text)

    # -- bus ------------------------------------------------------------------------------
    def on_message(self, env: Envelope) -> None:
        if env.topic == DisplayFrame.TOPIC and self.link is not None and self.link.open_:
            f = DisplayFrame.model_validate(env.data)
            if self.link.send_face(f.width, f.height, f.fg, f.bg, f.bits):
                self._lcd_count += 1

    def tick(self, dt: float) -> None:
        now = time.monotonic()
        if self.link is not None and not self.link.open_ and now >= self._next_retry:
            self._try_open()
        while True:
            try:
                w, h, seq, jpeg = self._frames.get_nowait()
            except queue.Empty:
                break
            self._cam_count += 1
            self.bus.publish_payload(CameraFrame(width=w, height=h, seq=seq, jpeg=jpeg))
        if now - self._last_state >= 1.0:
            span = max(now - self._window_start, 1e-6)
            self.camera_fps = self._cam_count / span
            self.lcd_fps = self._lcd_count / span
            self._cam_count = self._lcd_count = 0
            self._window_start = now
            self._last_state = now
            link = self.link
            self.bus.publish_payload(
                OpenMvState(
                    connected=bool(link and link.open_),
                    port=(link.port_name or "") if link else "",
                    camera_fps=round(self.camera_fps, 1),
                    lcd_fps=round(self.lcd_fps, 1),
                    dropped=link.dropped_out if link else 0,
                    board=self.board,
                )
            )

    def teardown(self) -> None:
        if self.link is not None:
            self.link.close()
