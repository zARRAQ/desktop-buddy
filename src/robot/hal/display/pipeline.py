"""Glue: logical surface -> rotation/flip -> display, with frame timing and health."""

from __future__ import annotations

import logging
import time

import pygame

from robot.core.config import DisplayConfig
from robot.hal.display.base import Display
from robot.hal.display.null import NullDisplay
from robot.hal.display.transform import logical_size, to_physical

log = logging.getLogger(__name__)


class DisplayPipeline:
    def __init__(self, cfg: DisplayConfig, display: Display) -> None:
        self.cfg = cfg
        self.display = display
        self.logical_w, self.logical_h = logical_size(display.info.size, cfg.rotation)
        self.surface = pygame.Surface((self.logical_w, self.logical_h))
        self.frames = 0
        self.last_frame_time = 0.0
        self._fps_window: list[float] = []
        self.fps = 0.0
        self._fallen_back = False

    def present(self) -> float:
        """Push the current logical surface. Returns the time spent, in seconds."""
        t0 = time.perf_counter()
        physical = to_physical(self.surface, self.cfg.rotation, self.cfg.flip)
        self.display.push(physical)
        dt = time.perf_counter() - t0
        self.last_frame_time = dt
        self.frames += 1
        now = time.monotonic()
        self._fps_window.append(now)
        cutoff = now - 2.0
        self._fps_window = [t for t in self._fps_window if t >= cutoff]
        if len(self._fps_window) > 1:
            self.fps = (len(self._fps_window) - 1) / max(1e-6, self._fps_window[-1] - self._fps_window[0])
        if not self.display.healthy and not self._fallen_back:
            self._fall_back()
        return dt

    def _fall_back(self) -> None:
        """A yanked cable must not take the face service down."""
        self._fallen_back = True
        log.error("display %s unhealthy; falling back to null", self.display.info.backend)
        try:
            self.display.close()
        except Exception:
            log.debug("close after failure raised", exc_info=True)
        self.display = NullDisplay(self.display.info.width, self.display.info.height, self.display.info.shape)
        self.display.open()

    def pump_events(self) -> list[pygame.event.Event]:
        return self.display.pump_events()

    def close(self) -> None:
        self.display.close()
