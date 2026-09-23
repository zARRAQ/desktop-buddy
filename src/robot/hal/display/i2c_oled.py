"""SSD1306 / SH1106 128x64 monochrome OLED over I2C. Ugly but functional fallback.

Frames are thresholded (not dithered) because a dithered soft eye on 1-bit looks like
static; the renderer's mono preset draws solid shapes so thresholding is exact.
Not yet exercised on real hardware.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any

import numpy as np
import pygame

from robot.hal.display.base import Display, PanelInfo

log = logging.getLogger(__name__)

SSD1306_INIT: tuple[int, ...] = (
    0xAE,  # display off
    0xD5,
    0x80,  # clock divide
    0xA8,
    0x3F,  # multiplex 64
    0xD3,
    0x00,  # display offset
    0x40,  # start line 0
    0x8D,
    0x14,  # charge pump on
    0x20,
    0x00,  # horizontal addressing mode
    0xA1,  # segment remap
    0xC8,  # COM scan direction
    0xDA,
    0x12,  # COM pins
    0x81,
    0xCF,  # contrast
    0xD9,
    0xF1,  # precharge
    0xDB,
    0x40,  # VCOM detect
    0xA4,  # resume RAM content
    0xA6,  # normal (not inverted)
    0xAF,  # display on
)

SH1106_INIT: tuple[int, ...] = (
    0xAE,
    0xD5,
    0x80,
    0xA8,
    0x3F,
    0xD3,
    0x00,
    0x40,
    0xAD,
    0x8B,
    0xA1,
    0xC8,
    0xDA,
    0x12,
    0x81,
    0xCF,
    0xD9,
    0xF1,
    0xDB,
    0x40,
    0xA4,
    0xA6,
    0xAF,
)


class I2cOledDisplay(Display):
    def __init__(
        self, bus: Any, address: int = 0x3C, *, controller: str = "ssd1306", width: int = 128, height: int = 64
    ) -> None:
        self.info = PanelInfo(backend="i2c", width=width, height=height, color="mono1", device=hex(address))
        self._bus = bus  # smbus2.SMBus or a test double with write_i2c_block_data
        self._addr = address
        self._controller = controller
        self._pages = height // 8

    def _cmd(self, *cmds: int) -> None:
        for c in cmds:
            self._bus.write_i2c_block_data(self._addr, 0x00, [c])

    def open(self) -> None:
        self._cmd(*(SH1106_INIT if self._controller == "sh1106" else SSD1306_INIT))
        log.info("display: i2c %s at %s", self._controller, self.info.device)

    def push(self, surface: pygame.Surface) -> None:
        if surface.get_size() != self.info.size:
            surface = pygame.transform.smoothscale(surface, self.info.size)
        gray = pygame.surfarray.array3d(surface).transpose(1, 0, 2).mean(axis=2)
        bits = (gray > 127).astype(np.uint8)  # h, w
        h, w = bits.shape
        pages = bits.reshape(h // 8, 8, w)
        weights = (1 << np.arange(8, dtype=np.uint8)).reshape(1, 8, 1)
        columns = (pages * weights).sum(axis=1).astype(np.uint8)  # pages, w
        for page in range(self._pages):
            if self._controller == "sh1106":
                self._cmd(0xB0 + page, 0x02, 0x10)  # SH1106 has a 2-column offset
            else:
                self._cmd(0xB0 + page, 0x00, 0x10)
            row = columns[page].tolist()
            for i in range(0, w, 32):
                self._bus.write_i2c_block_data(self._addr, 0x40, row[i : i + 32])

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self._cmd(0xAE)
