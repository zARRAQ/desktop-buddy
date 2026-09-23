"""Linux framebuffer output (``/dev/fbN``).

Covers fbtft SPI panels and, on a Pi 5, the fbdev emulation of the KMS device (``/dev/fb0``),
which makes this the most robust path for HDMI and DSI when SDL's kmsdrm driver misbehaves.
Geometry comes from ``/sys/class/graphics/fbN`` unless given explicitly.
"""

from __future__ import annotations

import logging
import mmap
from pathlib import Path

import numpy as np
import pygame

from robot.hal.display.base import Display, PanelInfo

log = logging.getLogger(__name__)


def read_fb_geometry(device: str, sysfs_root: Path = Path("/sys/class/graphics")) -> tuple[int, int, int, int]:
    """(width, height, bits_per_pixel, stride_bytes)."""
    name = Path(device).name
    base = sysfs_root / name
    vs = (base / "virtual_size").read_text().strip().split(",")
    bpp = int((base / "bits_per_pixel").read_text().strip())
    width, height = int(vs[0]), int(vs[1])
    stride_file = base / "stride"
    stride = int(stride_file.read_text().strip()) if stride_file.exists() else width * bpp // 8
    return width, height, bpp, stride


def surface_to_fb_bytes(surface: pygame.Surface, bpp: int, stride: int, *, bgr: bool = False) -> bytes:
    """Convert an RGB surface to the framebuffer's pixel format, row-padded to ``stride``."""
    w, h = surface.get_size()
    rgb = pygame.surfarray.array3d(surface).transpose(1, 0, 2)  # h, w, 3
    if bgr:
        rgb = rgb[..., ::-1]
    if bpp == 16:
        r = (rgb[..., 0].astype(np.uint16) >> 3) << 11
        g = (rgb[..., 1].astype(np.uint16) >> 2) << 5
        b = rgb[..., 2].astype(np.uint16) >> 3
        px = np.ascontiguousarray((r | g | b).astype("<u2"))
        row_bytes = px.view(np.uint8).reshape(h, w * 2)
    elif bpp == 32:
        px = np.empty((h, w, 4), dtype=np.uint8)
        px[..., 0] = rgb[..., 2]  # B
        px[..., 1] = rgb[..., 1]  # G
        px[..., 2] = rgb[..., 0]  # R
        px[..., 3] = 0
        row_bytes = px.reshape(h, w * 4)
    elif bpp == 24:
        row_bytes = rgb[..., ::-1].reshape(h, w * 3)  # BGR byte order
    else:
        raise ValueError(f"unsupported framebuffer depth {bpp}")
    if stride > row_bytes.shape[1]:
        padded = np.zeros((h, stride), dtype=np.uint8)
        padded[:, : row_bytes.shape[1]] = row_bytes
        row_bytes = padded
    return row_bytes.tobytes()


class FbdevDisplay(Display):
    def __init__(
        self,
        device: str = "/dev/fb0",
        *,
        geometry: tuple[int, int, int, int] | None = None,
        shape: str = "rect",
        bgr: bool = False,
    ) -> None:
        w, h, bpp, stride = geometry or read_fb_geometry(device)
        color = "rgb565" if bpp == 16 else ("bgr888" if bgr else "rgb888")
        self.info = PanelInfo(backend="fbdev", width=w, height=h, shape=shape, color=color, device=device)
        self.bpp = bpp
        self.stride = stride
        self.bgr = bgr
        self._fh: object | None = None
        self._map: mmap.mmap | None = None
        self._healthy = True

    def open(self) -> None:
        size = self.stride * self.info.height
        fh = open(self.info.device, "r+b", buffering=0)  # noqa: PTH123, SIM115
        self._fh = fh
        try:
            self._map = mmap.mmap(fh.fileno(), size, mmap.MAP_SHARED, mmap.PROT_WRITE)
        except (OSError, ValueError) as exc:
            log.warning("fbdev: mmap failed (%s), falling back to write()", exc)
            self._map = None
        log.info("display: fbdev %s %dx%d %dbpp", self.info.device, self.info.width, self.info.height, self.bpp)

    def push(self, surface: pygame.Surface) -> None:
        if surface.get_size() != self.info.size:
            surface = pygame.transform.smoothscale(surface, self.info.size)
        data = surface_to_fb_bytes(surface, self.bpp, self.stride, bgr=self.bgr)
        try:
            if self._map is not None:
                self._map[: len(data)] = data
            elif self._fh is not None:
                fh = self._fh
                fh.seek(0)  # type: ignore[attr-defined]
                fh.write(data)  # type: ignore[attr-defined]
        except (OSError, ValueError) as exc:
            self._healthy = False
            log.error("fbdev write failed: %s", exc)

    def close(self) -> None:
        if self._map is not None:
            self._map.close()
            self._map = None
        if self._fh is not None:
            self._fh.close()  # type: ignore[attr-defined]
            self._fh = None

    @property
    def healthy(self) -> bool:
        return self._healthy
