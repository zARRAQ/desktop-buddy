"""Display that publishes each frame as a 1-bit ``display.frame`` bus message for the OpenMV
bridge to forward to the board's LCD. Two-colour by nature, so it reports ``mono1`` and the
renderer draws crisp white-on-black; the eye colour travels alongside as RGB565."""

from __future__ import annotations

import time
from collections.abc import Callable

import pygame

from robot.core.messages import DisplayFrame, Payload
from robot.hal.display.base import Display, PanelInfo
from robot.hal.openmv import protocol as proto


class BusDisplay(Display):
    def __init__(
        self,
        width: int,
        height: int,
        *,
        publish: Callable[[Payload], None],
        eye_color: tuple[int, int, int] = (0x3E, 0xE0, 0xE6),
        background: tuple[int, int, int] = (0, 0, 0),
        max_fps: float = 12.0,
        shape: str = "rect",
    ) -> None:
        self.info = PanelInfo(backend="bus", width=width, height=height, shape=shape, color="mono1", device="openmv")
        self._publish = publish
        self.fg = proto.rgb565(eye_color)
        self.bg = proto.rgb565(background)
        self._min_period = 1.0 / max(max_fps, 0.5)
        self._last = 0.0
        self.sent = 0
        self.skipped = 0

    def open(self) -> None:
        return

    def push(self, surface: pygame.Surface) -> None:
        now = time.monotonic()
        if now - self._last < self._min_period:
            self.skipped += 1
            return
        self._last = now
        mask = proto.surface_to_mask(surface)
        self._publish(
            DisplayFrame(
                width=mask.shape[1], height=mask.shape[0], fg=self.fg, bg=self.bg, bits=proto.pack_bitmap(mask)
            )
        )
        self.sent += 1

    def close(self) -> None:
        return
