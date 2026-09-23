"""Headless display: discards frames, keeps a copy of the last one for tests and snapshots."""

from __future__ import annotations

import pygame

from robot.hal.display.base import Display, PanelInfo


class NullDisplay(Display):
    def __init__(self, width: int = 480, height: int = 480, shape: str = "rect") -> None:
        self.info = PanelInfo(backend="null", width=width, height=height, shape=shape)
        self.last: pygame.Surface | None = None
        self.frames = 0

    def open(self) -> None:
        pass

    def push(self, surface: pygame.Surface) -> None:
        self.last = surface.copy()
        self.frames += 1

    def close(self) -> None:
        pass
