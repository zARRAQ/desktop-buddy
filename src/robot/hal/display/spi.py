"""Direct SPI panels: ST7789, ILI9341, ILI9488, GC9A01, via ``spidev`` plus three GPIOs.

Initialisation sequences are taken from Adafruit's CircuitPython RGB display drivers
(MIT; see THIRD_PARTY_NOTICES.md). Pixel format is RGB565 big-endian, the common case for
all four controllers. No kernel module is needed.

Not yet exercised on real hardware; ``robot display test --pattern`` is the acceptance test.
"""

from __future__ import annotations

import logging
import struct
import time
from collections.abc import Sequence
from typing import Any

import numpy as np
import pygame

from robot.hal.display.base import Display, PanelInfo
from robot.hal.gpio.base import DigitalOutput, GpioBackend

log = logging.getLogger(__name__)

SWRESET, SLPOUT, NORON, INVON, INVOFF, DISPON = 0x01, 0x11, 0x13, 0x21, 0x20, 0x29
CASET, RASET, RAMWR, MADCTL, COLMOD = 0x2A, 0x2B, 0x2C, 0x36, 0x3A

InitStep = tuple[int, bytes | None]

# Adafruit_CircuitPython_RGB_Display/adafruit_rgb_display/st7789.py
INIT_ST7789: tuple[InitStep, ...] = (
    (SWRESET, None),
    (SLPOUT, None),
    (COLMOD, b"\x55"),
    (MADCTL, b"\x08"),
    (INVON, None),  # most ST7789 breakouts are IPS panels that need inversion on
    (NORON, None),
    (DISPON, None),
)

# Adafruit_CircuitPython_RGB_Display/adafruit_rgb_display/ili9341.py
INIT_ILI9341: tuple[InitStep, ...] = (
    (0xEF, b"\x03\x80\x02"),
    (0xCF, b"\x00\xc1\x30"),
    (0xED, b"\x64\x03\x12\x81"),
    (0xE8, b"\x85\x00\x78"),
    (0xCB, b"\x39\x2c\x00\x34\x02"),
    (0xF7, b"\x20"),
    (0xEA, b"\x00\x00"),
    (0xC0, b"\x23"),
    (0xC1, b"\x10"),
    (0xC5, b"\x3e\x28"),
    (0xC7, b"\x86"),
    (MADCTL, b"\x48"),
    (COLMOD, b"\x55"),
    (0xB1, b"\x00\x18"),
    (0xB6, b"\x08\x82\x27"),
    (0xF2, b"\x00"),
    (0x26, b"\x01"),
    (0xE0, b"\x0f\x31\x2b\x0c\x0e\x08\x4e\xf1\x37\x07\x10\x03\x0e\x09\x00"),
    (0xE1, b"\x00\x0e\x14\x03\x11\x07\x31\xc1\x48\x08\x0f\x0c\x31\x36\x0f"),
    (SLPOUT, None),
    (DISPON, None),
)

# ILI9488 over 4-wire SPI only accepts 18-bit colour; we send RGB666 (3 bytes per pixel).
INIT_ILI9488: tuple[InitStep, ...] = (
    (SWRESET, None),
    (0xE0, b"\x00\x03\x09\x08\x16\x0a\x3f\x78\x4c\x09\x0a\x08\x16\x1a\x0f"),
    (0xE1, b"\x00\x16\x19\x03\x0f\x05\x32\x45\x46\x04\x0e\x0d\x35\x37\x0f"),
    (0xC0, b"\x17\x15"),
    (0xC1, b"\x41"),
    (0xC5, b"\x00\x12\x80"),
    (MADCTL, b"\x48"),
    (COLMOD, b"\x66"),
    (0xB0, b"\x00"),
    (0xB1, b"\xa0"),
    (0xB4, b"\x02"),
    (0xB6, b"\x02\x02"),
    (0xE9, b"\x00"),
    (0xF7, b"\xa9\x51\x2c\x82"),
    (SLPOUT, None),
    (DISPON, None),
)

# Adafruit_CircuitPython_RGB_Display/adafruit_rgb_display/gc9a01a.py
INIT_GC9A01: tuple[InitStep, ...] = (
    (SWRESET, None),
    (0xEF, None),
    (0xB6, b"\x00\x00"),
    (MADCTL, b"\x48"),
    (COLMOD, b"\x05"),
    (0xC3, b"\x13"),
    (0xC4, b"\x13"),
    (0xC9, b"\x22"),
    (0xF0, b"\x45\x09\x08\x08\x26\x2a"),
    (0xF1, b"\x43\x70\x72\x36\x37\x6f"),
    (0xF2, b"\x45\x09\x08\x08\x26\x2a"),
    (0xF3, b"\x43\x70\x72\x36\x37\x6f"),
    (0x66, b"\x3c\x00\xcd\x67\x45\x45\x10\x00\x00\x00"),
    (0x67, b"\x00\x3c\x00\x00\x00\x01\x54\x10\x32\x98"),
    (0x74, b"\x10\x85\x80\x00\x00\x4e\x00"),
    (0x98, b"\x3e\x07"),
    (0x35, None),
    (INVON, None),
    (SLPOUT, None),
    (NORON, None),
    (DISPON, None),
)

CONTROLLERS: dict[str, tuple[tuple[InitStep, ...], tuple[int, int], str, int]] = {
    # name: (init, default size, shape, bytes per pixel)
    "st7789": (INIT_ST7789, (240, 240), "rect", 2),
    "ili9341": (INIT_ILI9341, (240, 320), "rect", 2),
    "ili9488": (INIT_ILI9488, (320, 480), "rect", 3),
    "gc9a01": (INIT_GC9A01, (240, 240), "round", 2),
}

# MADCTL bits: MY 0x80, MX 0x40, MV 0x20, BGR 0x08
MADCTL_FOR_ROTATION = {0: 0x00, 90: 0x60, 180: 0xC0, 270: 0xA0}


def rgb565_be(surface: pygame.Surface, *, bgr: bool = False) -> bytes:
    rgb = pygame.surfarray.array3d(surface).transpose(1, 0, 2)
    if bgr:
        rgb = rgb[..., ::-1]
    r = (rgb[..., 0].astype(np.uint16) >> 3) << 11
    g = (rgb[..., 1].astype(np.uint16) >> 2) << 5
    b = rgb[..., 2].astype(np.uint16) >> 3
    return np.ascontiguousarray((r | g | b).astype(">u2")).tobytes()


def rgb666(surface: pygame.Surface, *, bgr: bool = False) -> bytes:
    rgb = pygame.surfarray.array3d(surface).transpose(1, 0, 2)
    if bgr:
        rgb = rgb[..., ::-1]
    return (rgb & 0xFC).astype(np.uint8).tobytes()


class SpiTransport:
    """Thin wrapper so tests can substitute a recorder."""

    def __init__(self, bus: int, device: int, speed_hz: int) -> None:
        import spidev

        self._spi: Any = spidev.SpiDev()
        self._spi.open(bus, device)
        self._spi.max_speed_hz = speed_hz
        self._spi.mode = 0
        self.chunk = 4096

    def write(self, data: bytes) -> None:
        for i in range(0, len(data), self.chunk):
            self._spi.writebytes2(data[i : i + self.chunk])

    def close(self) -> None:
        self._spi.close()


class SpiDisplay(Display):
    def __init__(
        self,
        controller: str,
        gpio: GpioBackend,
        transport: SpiTransport | Any,
        *,
        width: int | None = None,
        height: int | None = None,
        pin_dc: int = 25,
        pin_reset: int = 4,
        pin_backlight: int | None = 24,
        x_offset: int = 0,
        y_offset: int = 0,
        bgr: bool = False,
        invert: bool | None = None,
    ) -> None:
        if controller not in CONTROLLERS:
            raise ValueError(f"unsupported SPI controller {controller!r}")
        init, size, shape, bpp = CONTROLLERS[controller]
        w, h = (width or size[0]), (height or size[1])
        self.info = PanelInfo(
            backend="spi", width=w, height=h, shape=shape, color="bgr888" if bgr else "rgb888", device=controller
        )
        self.controller = controller
        self._init = init
        self._bpp = bpp
        self._bgr = bgr
        self._invert = invert
        self._x_off, self._y_off = x_offset, y_offset
        self._spi = transport
        self._dc: DigitalOutput = gpio.output(pin_dc, initial=False)
        self._rst: DigitalOutput = gpio.output(pin_reset, initial=True)
        self._bl: DigitalOutput | None = gpio.output(pin_backlight, initial=True) if pin_backlight is not None else None
        self._healthy = True

    # -- low level -------------------------------------------------------------------
    def command(self, cmd: int, data: bytes | None = None) -> None:
        self._dc.write(False)
        self._spi.write(bytes([cmd]))
        if data:
            self._dc.write(True)
            self._spi.write(data)

    def _window(self, x0: int, y0: int, x1: int, y1: int) -> None:
        self.command(CASET, struct.pack(">HH", x0 + self._x_off, x1 + self._x_off))
        self.command(RASET, struct.pack(">HH", y0 + self._y_off, y1 + self._y_off))

    def open(self) -> None:
        self._rst.write(True)
        time.sleep(0.01)
        self._rst.write(False)
        time.sleep(0.01)
        self._rst.write(True)
        time.sleep(0.12)
        for cmd, data in self._init:
            self.command(cmd, data)
            if cmd in (SWRESET, SLPOUT):
                time.sleep(0.12)
        if self._invert is not None:
            self.command(INVON if self._invert else INVOFF)
        if self._bgr:
            self.command(MADCTL, bytes([0x08]))
        self._window(0, 0, self.info.width - 1, self.info.height - 1)
        if self._bl is not None:
            self._bl.write(True)
        log.info("display: spi %s %dx%d", self.controller, self.info.width, self.info.height)

    def push(self, surface: pygame.Surface) -> None:
        if surface.get_size() != self.info.size:
            surface = pygame.transform.smoothscale(surface, self.info.size)
        data = rgb666(surface, bgr=self._bgr) if self._bpp == 3 else rgb565_be(surface, bgr=self._bgr)
        try:
            self._window(0, 0, self.info.width - 1, self.info.height - 1)
            self.command(RAMWR)
            self._dc.write(True)
            self._spi.write(data)
        except OSError as exc:
            self._healthy = False
            log.error("spi push failed: %s", exc)

    def set_brightness(self, value: float) -> None:
        if self._bl is not None:
            self._bl.write(value > 0.05)

    def close(self) -> None:
        if self._bl is not None:
            self._bl.write(False)
        for pin in (self._dc, self._rst, self._bl):
            if pin is not None:
                pin.close()
        self._spi.close()

    @property
    def healthy(self) -> bool:
        return self._healthy


class RecordingTransport:
    """Test double: records every write."""

    def __init__(self) -> None:
        self.writes: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.writes.append(bytes(data))

    def close(self) -> None:
        pass

    def total_bytes(self, min_len: int = 0) -> int:
        return sum(len(w) for w in self.writes if len(w) >= min_len)

    def commands(self) -> Sequence[int]:
        return [w[0] for w in self.writes if len(w) == 1]
