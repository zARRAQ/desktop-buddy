"""SDL window (development) and KMS/DRM fullscreen (HDMI, DSI on the robot). Same code."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping

import pygame

from robot.hal.display.base import Display, PanelInfo

log = logging.getLogger(__name__)


def choose_sdl_driver(setting: str, environ: Mapping[str, str]) -> str | None:
    """Which SDL video driver to ask for, or None to leave SDL's choice alone.

    An explicit ``SDL_VIDEODRIVER`` in the environment always wins (tests set ``dummy``). With
    ``auto`` on a Wayland desktop that also offers X11 (XWayland), prefer x11: SDL's Wayland
    backend can block in the buffer swap while the window is covered, which froze the face.
    """
    if environ.get("SDL_VIDEODRIVER"):
        return None
    if setting != "auto":
        return setting
    if environ.get("WAYLAND_DISPLAY") and environ.get("DISPLAY"):
        return "x11"
    return None


class WindowDisplay(Display):
    def __init__(
        self,
        width: int,
        height: int,
        *,
        kms: bool = False,
        device_index: int | None = None,
        title: str = "robot face",
        shape: str = "rect",
        fullscreen: bool = False,
        sdl_driver: str = "auto",
    ) -> None:
        self.info = PanelInfo(backend="kms" if kms else "window", width=width, height=height, shape=shape)
        self._kms = kms
        self._fullscreen = fullscreen
        self._sdl_driver = sdl_driver
        self._device_index = device_index
        self._title = title
        self._screen: pygame.Surface | None = None
        self._healthy = True

    def open(self) -> None:
        if self._kms:
            os.environ.setdefault("SDL_VIDEODRIVER", "kmsdrm")
            if self._device_index is not None:
                os.environ["SDL_KMSDRM_DEVICE_INDEX"] = str(self._device_index)
        os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
        preferred = None if self._kms else choose_sdl_driver(self._sdl_driver, os.environ)
        if preferred is not None:
            os.environ["SDL_VIDEODRIVER"] = preferred
        try:
            pygame.display.init()
        except pygame.error as exc:
            if preferred is None:
                raise
            log.warning("SDL driver %s failed (%s); letting SDL choose", preferred, exc)
            os.environ.pop("SDL_VIDEODRIVER", None)
            pygame.display.init()
        pygame.font.init()
        cover = self._kms or self._fullscreen
        flags = pygame.FULLSCREEN if cover else 0
        if cover:
            # Ask for the native mode; SDL reports what we actually got.
            self._screen = pygame.display.set_mode((0, 0), flags)
            w, h = self._screen.get_size()
            self.info.width, self.info.height = w, h
        else:
            self._screen = pygame.display.set_mode((self.info.width, self.info.height), flags)
        pygame.display.set_caption(self._title)
        pygame.mouse.set_visible(not cover)
        self.info.device = pygame.display.get_driver()
        log.info("display: %s %dx%d via SDL %s", self.info.backend, self.info.width, self.info.height, self.info.device)

    def push(self, surface: pygame.Surface) -> None:
        if self._screen is None:
            return
        try:
            if surface.get_size() != self._screen.get_size():
                surface = pygame.transform.smoothscale(surface, self._screen.get_size())
            self._screen.blit(surface, (0, 0))
            pygame.display.flip()
        except pygame.error as exc:
            self._healthy = False
            log.error("display push failed: %s", exc)

    def pump_events(self) -> list[pygame.event.Event]:
        return pygame.event.get()

    def close(self) -> None:
        pygame.display.quit()
        self._screen = None

    @property
    def healthy(self) -> bool:
        return self._healthy
