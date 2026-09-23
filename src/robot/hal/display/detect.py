"""Display detection cascade.

Order: explicit config, then a desktop session (window), then DRM/KMS with a connected
connector (HDMI, DSI), then a framebuffer (fbtft SPI panels, legacy fb), then a direct SPI
panel if a controller is configured, then an I2C OLED, then headless.

All system probes are injectable so the cascade is unit-tested without hardware.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from robot.core.config import DisplayConfig

KNOWN_ROUND_PANELS = ("gc9a01", "round", "hyperpixel2r", "st7701")


@dataclass
class DrmConnector:
    card: str  # e.g. "card1"
    name: str  # e.g. "HDMI-A-1", "DSI-1"
    status: str  # "connected" | "disconnected" | "unknown"
    mode: tuple[int, int] | None  # first listed mode


@dataclass
class FbInfo:
    device: str  # /dev/fb0
    width: int
    height: int
    bits_per_pixel: int
    name: str = ""


@dataclass
class Probes:
    environ: Mapping[str, str] = field(default_factory=lambda: os.environ)
    drm_connectors: Callable[[], list[DrmConnector]] = field(default_factory=lambda: real_drm_connectors)
    framebuffers: Callable[[], list[FbInfo]] = field(default_factory=lambda: real_framebuffers)
    spi_available: Callable[[int, int], bool] = lambda bus, dev: Path(f"/dev/spidev{bus}.{dev}").exists()
    i2c_scan: Callable[[int], list[int]] = field(default_factory=lambda: real_i2c_scan)


@dataclass
class DetectionResult:
    backend: str
    device: str = ""
    width: int | None = None
    height: int | None = None
    shape: str = "auto"
    color: str = "auto"
    controller: str = "auto"
    reasons: list[str] = field(default_factory=list)

    @property
    def session_bound(self) -> bool:
        """A desktop window exists only inside the login session that detected it. Saved
        into local.yaml it would break the systemd units, which have no DISPLAY."""
        return self.backend == "window"

    def as_local_config(self) -> dict[str, object]:
        if self.session_bound:
            return {"backend": "auto"}
        out: dict[str, object] = {"backend": self.backend}
        if self.device:
            out["device"] = self.device
        if self.width and self.height:
            out["width"] = self.width
            out["height"] = self.height
        if self.shape != "auto":
            out["shape"] = self.shape
        if self.color != "auto":
            out["color"] = self.color
        if self.controller != "auto":
            out["controller"] = self.controller
        return out


# ---------------------------------------------------------------------------
# Real probes
# ---------------------------------------------------------------------------


def real_drm_connectors(root: Path = Path("/sys/class/drm")) -> list[DrmConnector]:
    out: list[DrmConnector] = []
    if not root.exists():
        return out
    for path in sorted(root.glob("card*-*")):
        m = re.match(r"(card\d+)-(.+)", path.name)
        if not m:
            continue
        try:
            status = (path / "status").read_text().strip()
        except OSError:
            status = "unknown"
        mode: tuple[int, int] | None = None
        try:
            first = (path / "modes").read_text().split()
            if first:
                mm = re.match(r"(\d+)x(\d+)", first[0])
                if mm:
                    mode = (int(mm.group(1)), int(mm.group(2)))
        except OSError:
            pass
        out.append(DrmConnector(card=m.group(1), name=m.group(2), status=status, mode=mode))
    return out


def real_framebuffers(root: Path = Path("/sys/class/graphics")) -> list[FbInfo]:
    out: list[FbInfo] = []
    if not root.exists():
        return out
    for path in sorted(root.glob("fb*")):
        dev = Path("/dev") / path.name
        if not dev.exists():
            continue
        try:
            vs = (path / "virtual_size").read_text().strip().split(",")
            bpp = int((path / "bits_per_pixel").read_text().strip())
            name = (path / "name").read_text().strip() if (path / "name").exists() else ""
            out.append(FbInfo(str(dev), int(vs[0]), int(vs[1]), bpp, name))
        except (OSError, ValueError, IndexError):
            continue
    return out


def real_i2c_scan(bus: int) -> list[int]:
    try:
        from smbus2 import SMBus
    except ImportError:
        return []
    found: list[int] = []
    try:
        with SMBus(bus) as b:
            for addr in (0x3C, 0x3D):
                try:
                    b.read_byte(addr)
                    found.append(addr)
                except OSError:
                    continue
    except OSError:
        return []
    return found


# ---------------------------------------------------------------------------
# Cascade
# ---------------------------------------------------------------------------


def detect_display(cfg: DisplayConfig, probes: Probes | None = None) -> DetectionResult:
    p = probes or Probes()
    env = p.environ

    forced = env.get("ROBOT_DISPLAY")
    if forced:
        return DetectionResult(backend=forced, reasons=[f"ROBOT_DISPLAY={forced}"])
    if cfg.backend != "auto":
        res = DetectionResult(backend=cfg.backend, device=cfg.device if cfg.device != "auto" else "")
        res.reasons.append(f"display.backend={cfg.backend} configured")
        _fill_known_sizes(cfg, res, p)
        return res

    if env.get("DISPLAY") or env.get("WAYLAND_DISPLAY") or env.get("SDL_VIDEODRIVER") in ("dummy", "x11", "wayland"):
        return DetectionResult(
            backend="window",
            width=cfg.width or 480,
            height=cfg.height or 480,
            reasons=["desktop session detected (DISPLAY/WAYLAND_DISPLAY/SDL_VIDEODRIVER)"],
        )

    connectors = [c for c in p.drm_connectors() if c.status == "connected"]
    if connectors:
        c = connectors[0]
        res = DetectionResult(backend="kms", device=f"/dev/dri/{c.card}")
        if c.mode:
            res.width, res.height = c.mode
        res.reasons.append(f"DRM connector {c.name} connected on {c.card}")
        if c.mode and c.mode[0] == c.mode[1] and "DSI" in c.name.upper():
            res.shape = "round" if any(k in c.name.lower() for k in KNOWN_ROUND_PANELS) else "auto"
            res.reasons.append("square DSI panel: set display.shape=round if the bezel is round")
        return res

    fbs = p.framebuffers()
    for want in ("/dev/fb1", "/dev/fb0"):
        for fb in fbs:
            if fb.device == want:
                res = DetectionResult(backend="fbdev", device=fb.device, width=fb.width, height=fb.height)
                res.color = "rgb565" if fb.bits_per_pixel == 16 else "rgb888"
                res.reasons.append(f"framebuffer {fb.device} {fb.width}x{fb.height} {fb.bits_per_pixel}bpp {fb.name}")
                if fb.width == fb.height and any(k in fb.name.lower() for k in KNOWN_ROUND_PANELS):
                    res.shape = "round"
                return res

    if cfg.controller != "auto" and p.spi_available(cfg.spi.bus, cfg.spi.device):
        res = DetectionResult(backend="spi", device=f"spi{cfg.spi.bus}.{cfg.spi.device}", controller=cfg.controller)
        res.reasons.append(f"SPI device present and controller={cfg.controller} configured")
        _fill_known_sizes(cfg, res, p)
        return res

    addrs = p.i2c_scan(1)
    if addrs:
        res = DetectionResult(
            backend="i2c", device=hex(addrs[0]), width=128, height=64, color="mono1", controller="ssd1306"
        )
        res.reasons.append(f"I2C OLED found at {hex(addrs[0])}")
        return res

    reasons = [
        "no display found: no desktop, no connected DRM connector, no framebuffer, no SPI controller configured, no I2C OLED"
    ]
    if cfg.controller == "auto" and p.spi_available(cfg.spi.bus, cfg.spi.device):
        reasons.append(
            "SPI is enabled: if a bare SPI panel is wired, set display.controller (its ID register is unreadable without MISO)"
        )
    return DetectionResult(backend="null", reasons=reasons)


def _fill_known_sizes(cfg: DisplayConfig, res: DetectionResult, p: Probes) -> None:
    if cfg.width and cfg.height:
        res.width, res.height = cfg.width, cfg.height
        return
    defaults = {
        "st7789": (240, 240),
        "ili9341": (240, 320),
        "ili9488": (320, 480),
        "gc9a01": (240, 240),
        "ssd1306": (128, 64),
        "sh1106": (128, 64),
    }
    ctrl = res.controller if res.controller != "auto" else cfg.controller
    if ctrl in defaults:
        res.width, res.height = defaults[ctrl]
        if ctrl == "gc9a01":
            res.shape = "round"
        if ctrl in ("ssd1306", "sh1106"):
            res.color = "mono1"
    if res.backend == "fbdev" and res.device:
        for fb in p.framebuffers():
            if fb.device == res.device:
                res.width, res.height = fb.width, fb.height
                res.color = "rgb565" if fb.bits_per_pixel == 16 else "rgb888"
