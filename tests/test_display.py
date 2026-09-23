from __future__ import annotations

from pathlib import Path

import pygame

from robot.core.config import DisplayConfig
from robot.face.patterns import draw_pattern
from robot.hal.display.detect import DrmConnector, FbInfo, Probes, detect_display
from robot.hal.display.fbdev import FbdevDisplay, surface_to_fb_bytes
from robot.hal.display.i2c_oled import I2cOledDisplay
from robot.hal.display.null import NullDisplay
from robot.hal.display.pipeline import DisplayPipeline
from robot.hal.display.spi import CASET, RAMWR, RecordingTransport, SpiDisplay
from robot.hal.display.transform import logical_size, to_physical
from robot.hal.gpio.mock import MockGpioBackend


def probes(**kw):
    base = dict(
        environ={},
        drm_connectors=lambda: [],
        framebuffers=lambda: [],
        spi_available=lambda b, d: False,
        i2c_scan=lambda bus: [],
    )
    base.update(kw)
    return Probes(**base)


def test_detect_forced_env():
    r = detect_display(DisplayConfig(), probes(environ={"ROBOT_DISPLAY": "null"}))
    assert r.backend == "null"


def test_detect_configured_backend_wins():
    r = detect_display(DisplayConfig(backend="spi", controller="gc9a01"), probes())
    assert r.backend == "spi" and (r.width, r.height) == (240, 240) and r.shape == "round"


def test_detect_desktop_window():
    r = detect_display(DisplayConfig(), probes(environ={"DISPLAY": ":0"}))
    assert r.backend == "window"


def test_detect_kms_prefers_connected_connector():
    conns = [
        DrmConnector("card0", "HDMI-A-1", "disconnected", (1920, 1080)),
        DrmConnector("card1", "DSI-1", "connected", (480, 480)),
    ]
    r = detect_display(DisplayConfig(), probes(drm_connectors=lambda: conns))
    assert r.backend == "kms" and r.device == "/dev/dri/card1" and (r.width, r.height) == (480, 480)


def test_detect_fbdev_prefers_fb1():
    fbs = [FbInfo("/dev/fb0", 1920, 1080, 32), FbInfo("/dev/fb1", 240, 240, 16, "fb_st7789v")]
    r = detect_display(DisplayConfig(), probes(framebuffers=lambda: fbs))
    assert r.backend == "fbdev" and r.device == "/dev/fb1" and r.color == "rgb565"


def test_detect_spi_needs_controller():
    r = detect_display(DisplayConfig(), probes(spi_available=lambda b, d: True))
    assert r.backend == "null"
    assert any("set display.controller" in s for s in r.reasons)
    r2 = detect_display(DisplayConfig(controller="st7789"), probes(spi_available=lambda b, d: True))
    assert r2.backend == "spi" and r2.controller == "st7789"


def test_detect_i2c_oled_and_headless():
    r = detect_display(DisplayConfig(), probes(i2c_scan=lambda bus: [0x3C]))
    assert r.backend == "i2c" and r.color == "mono1"
    assert detect_display(DisplayConfig(), probes()).backend == "null"


def test_transform_rotation_changes_orientation():
    pygame.init()
    s = pygame.Surface((100, 50))
    s.fill((0, 0, 0))
    s.set_at((0, 0), (255, 0, 0))  # top-left marker
    assert logical_size((100, 50), 90) == (50, 100)
    out = to_physical(s, 90, "none")
    assert out.get_size() == (50, 100)
    assert out.get_at((49, 0))[:3] == (255, 0, 0)  # rotated clockwise: TL -> TR
    out180 = to_physical(s, 180, "none")
    assert out180.get_at((99, 49))[:3] == (255, 0, 0)
    flipped = to_physical(s, 0, "h")
    assert flipped.get_at((99, 0))[:3] == (255, 0, 0)


def test_fb_bytes_565_and_32():
    pygame.init()
    s = pygame.Surface((2, 1))
    s.set_at((0, 0), (255, 0, 0))
    s.set_at((1, 0), (0, 0, 255))
    b565 = surface_to_fb_bytes(s, 16, 4)
    assert b565 == bytes([0x00, 0xF8, 0x1F, 0x00])  # little-endian RGB565: red, blue
    b32 = surface_to_fb_bytes(s, 32, 8)
    assert b32 == bytes([0, 0, 255, 0, 255, 0, 0, 0])  # BGRX
    padded = surface_to_fb_bytes(s, 16, 8)
    assert len(padded) == 8


def test_fbdev_display_writes_file(tmp_path: Path):
    pygame.init()
    fb = tmp_path / "fb0"
    fb.write_bytes(bytes(4 * 4 * 2))
    d = FbdevDisplay(str(fb), geometry=(4, 4, 16, 8))
    d.open()
    s = pygame.Surface((4, 4))
    s.fill((255, 255, 255))
    d.push(s)
    d.close()
    assert fb.read_bytes() == b"\xff\xff" * 16


def test_pipeline_falls_back_to_null_when_display_unhealthy():
    class Broken(NullDisplay):
        @property
        def healthy(self) -> bool:
            return False

    pygame.init()
    p = DisplayPipeline(DisplayConfig(backend="null", width=32, height=32), Broken(32, 32))
    p.present()
    assert type(p.display) is NullDisplay


def test_spi_display_init_and_push():
    pygame.init()
    gpio = MockGpioBackend()
    spi = RecordingTransport()
    d = SpiDisplay("st7789", gpio, spi, pin_dc=25, pin_reset=4, pin_backlight=24)
    d.open()
    assert CASET in spi.commands()
    assert gpio.levels[24] is True  # backlight on
    s = pygame.Surface((240, 240))
    s.fill((255, 0, 0))
    d.push(s)
    assert RAMWR in spi.commands()
    assert spi.total_bytes(min_len=1000) == 240 * 240 * 2
    frame = [w for w in spi.writes if len(w) >= 1000][-1]
    assert frame[:2] == bytes([0xF8, 0x00])  # red, big-endian RGB565


def test_gc9a01_is_round_and_ili9488_is_18bit():
    pygame.init()
    gpio = MockGpioBackend()
    d = SpiDisplay("gc9a01", gpio, RecordingTransport())
    assert d.info.shape == "round" and d.info.size == (240, 240)
    spi = RecordingTransport()
    d2 = SpiDisplay("ili9488", gpio, spi)
    d2.open()
    s = pygame.Surface((320, 480))
    d2.push(s)
    assert spi.total_bytes(min_len=1000) == 320 * 480 * 3


def test_i2c_oled_packs_pages():
    pygame.init()

    class Bus:
        def __init__(self):
            self.writes = []

        def write_i2c_block_data(self, addr, reg, data):
            self.writes.append((addr, reg, list(data)))

    bus = Bus()
    d = I2cOledDisplay(bus, 0x3C)
    d.open()
    s = pygame.Surface((128, 64))
    s.fill((0, 0, 0))
    pygame.draw.rect(s, (255, 255, 255), pygame.Rect(0, 0, 128, 8))  # top page fully lit
    d.push(s)
    data_writes = [w for w in bus.writes if w[1] == 0x40]
    assert len(data_writes) == 8 * 4
    assert all(v == 0xFF for v in data_writes[0][2])
    assert all(v == 0x00 for v in data_writes[4][2])


def test_pattern_draws_orientation_glyph():
    pygame.init()
    s = pygame.Surface((240, 240))
    draw_pattern(s)
    assert s.get_at((7, 7))[:3] == (255, 255, 255)
    assert s.get_at((5, 120))[:3] != (255, 200, 0)
