"""Build a Display from configuration plus detection."""

from __future__ import annotations

import logging

from robot.core.config import DisplayConfig
from robot.hal.display.base import Display
from robot.hal.display.detect import DetectionResult, Probes, detect_display
from robot.hal.display.null import NullDisplay
from robot.hal.display.window import WindowDisplay

log = logging.getLogger(__name__)


def open_display(
    cfg: DisplayConfig, *, probes: Probes | None = None, detection: DetectionResult | None = None
) -> Display:
    """Returns an opened display. Falls back to :class:`NullDisplay` on any failure."""
    det = detection or detect_display(cfg, probes)
    for r in det.reasons:
        log.info("display detect: %s", r)
    width = cfg.width or det.width or 480
    height = cfg.height or det.height or 480
    shape = cfg.shape if cfg.shape != "auto" else (det.shape if det.shape != "auto" else "rect")
    try:
        disp = _build(cfg, det, width, height, shape)
        disp.open()
        return disp
    except Exception as exc:
        log.error("display backend %s failed (%s); running headless", det.backend, exc)
        null = NullDisplay(width, height, shape)
        null.open()
        return null


def _build(cfg: DisplayConfig, det: DetectionResult, width: int, height: int, shape: str) -> Display:
    backend = det.backend
    if backend == "window":
        return WindowDisplay(width, height, shape=shape)
    if backend == "kms":
        idx = cfg.kms_device_index
        if idx is None and det.device.startswith("/dev/dri/card"):
            try:
                idx = int(det.device.removeprefix("/dev/dri/card"))
            except ValueError:
                idx = None
        return WindowDisplay(width, height, kms=True, device_index=idx, shape=shape)
    if backend == "fbdev":
        from robot.hal.display.fbdev import FbdevDisplay

        device = det.device or (cfg.device if cfg.device != "auto" else "/dev/fb0")
        return FbdevDisplay(device, shape=shape, bgr=(cfg.color == "bgr888"))
    if backend == "spi":
        from robot.hal.display.spi import SpiDisplay, SpiTransport
        from robot.hal.gpio.base import open_gpio_backend

        controller = cfg.controller if cfg.controller != "auto" else det.controller
        if controller in ("auto", "ssd1306", "sh1106"):
            raise ValueError("display.controller must name an SPI controller (st7789, ili9341, ili9488, gc9a01)")
        gpio = open_gpio_backend("gpiozero")
        transport = SpiTransport(cfg.spi.bus, cfg.spi.device, cfg.spi.speed_hz)
        return SpiDisplay(
            controller,
            gpio,
            transport,
            width=cfg.width,
            height=cfg.height,
            pin_dc=cfg.spi.pin_dc,
            pin_reset=cfg.spi.pin_reset,
            pin_backlight=cfg.spi.pin_backlight,
            x_offset=cfg.spi.x_offset,
            y_offset=cfg.spi.y_offset,
            bgr=(cfg.color == "bgr888"),
        )
    if backend == "i2c":
        from smbus2 import SMBus

        from robot.hal.display.i2c_oled import I2cOledDisplay

        controller = cfg.controller if cfg.controller in ("ssd1306", "sh1106") else "ssd1306"
        addr = int(det.device, 16) if det.device.startswith("0x") else cfg.i2c_address
        return I2cOledDisplay(SMBus(1), addr, controller=controller)
    return NullDisplay(width, height, shape)
