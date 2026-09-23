"""The Display interface every backend implements."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import pygame


@dataclass
class PanelInfo:
    backend: str
    width: int
    height: int
    shape: str = "rect"  # rect | round
    color: str = "rgb888"  # rgb888 | rgb565 | bgr888 | mono1
    device: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height


class Display(ABC):
    """Physical-orientation frames in, photons out.

    ``push`` receives a surface of exactly ``info.size`` in the panel's native orientation.
    Rotation and flipping are applied before this by :class:`robot.hal.display.pipeline`.
    """

    info: PanelInfo

    @abstractmethod
    def open(self) -> None: ...

    @abstractmethod
    def push(self, surface: pygame.Surface) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    def set_brightness(self, value: float) -> None:
        """0..1. Backends without a backlight control ignore it."""

    def pump_events(self) -> list[pygame.event.Event]:
        """Window backends return pending input events; everything else returns []."""
        return []

    @property
    def healthy(self) -> bool:
        return True
