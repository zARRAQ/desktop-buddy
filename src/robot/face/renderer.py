"""Draws face polygons onto a pygame surface sized for the attached panel.

Resolution and shape independence: geometry lives in a 128 x 64 design space; the renderer
maps it into the largest inscribed square of the panel (``fit``), or covers the panel
(``fill``), or stretches. Round panels pull the eyes inward and mask the corners. Mono
panels get a high-contrast preset. Very small panels get a simplified face.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass

import pygame
import pygame.gfxdraw

from robot.core.config import FaceConfig
from robot.face.procedural import DESIGN_HEIGHT, DESIGN_WIDTH, FaceParams, Polygon, face_shapes

log = logging.getLogger(__name__)

Color = tuple[int, int, int]


def parse_color(text: str) -> Color:
    t = text.strip().lstrip("#")
    if len(t) == 3:
        t = "".join(c * 2 for c in t)
    if len(t) != 6:
        raise ValueError(f"colour must be #RRGGBB, got {text!r}")
    return int(t[0:2], 16), int(t[2:4], 16), int(t[4:6], 16)


@dataclass(frozen=True)
class PanelGeometry:
    width: int
    height: int
    shape: str = "rect"  # rect | round
    color: str = "rgb888"  # rgb888 | rgb565 | bgr888 | mono1
    scale_mode: str = "fit"

    @property
    def is_mono(self) -> bool:
        return self.color == "mono1"

    @property
    def is_tiny(self) -> bool:
        return min(self.width, self.height) < 160


@dataclass
class ViewTransform:
    """Design units -> panel pixels."""

    scale_x: float
    scale_y: float
    offset_x: float
    offset_y: float

    def apply(self, poly: Polygon) -> list[tuple[int, int]]:
        return [(round(x * self.scale_x + self.offset_x), round(y * self.scale_y + self.offset_y)) for x, y in poly]


def compute_transform(panel: PanelGeometry, design_scale: float) -> ViewTransform:
    """Fit the design width across the panel's inscribed square.

    A square design region of DESIGN_WIDTH x DESIGN_WIDTH is mapped to the panel so that a
    128-unit wide face spans the whole short edge. ``design_scale`` enlarges the eyes.
    """
    w, h = panel.width, panel.height
    if panel.scale_mode == "stretch":
        sx = w / DESIGN_WIDTH * design_scale
        sy = h / DESIGN_WIDTH * design_scale
    elif panel.scale_mode == "fill":
        s = max(w, h) / DESIGN_WIDTH * design_scale
        sx = sy = s
    else:
        s = min(w, h) / DESIGN_WIDTH * design_scale
        sx = sy = s
    return ViewTransform(sx, sy, w / 2.0, h / 2.0)


class QualityController:
    """Measures frame time and degrades rendering in fixed steps when the panel can't keep up.

    Levels: 0 full; 1 no idle drift; 2 no antialiasing; 3 half saccade rate; 4 lower fps.
    """

    MAX_LEVEL = 4

    def __init__(self, target_fps: int, min_fps: int, *, clock: object = time) -> None:
        self.target_fps = target_fps
        self.min_fps = min_fps
        self.level = 0
        self.effective_fps = target_fps
        self._frames: deque[float] = deque(maxlen=60)
        self._bad_since: float | None = None
        self._good_since: float | None = None
        self._clock = clock

    @property
    def budget_s(self) -> float:
        return 1.0 / max(self.effective_fps, 1)

    def record(self, frame_time_s: float, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self._frames.append(frame_time_s)
        if len(self._frames) < 10:
            return
        ordered = sorted(self._frames)
        p95 = ordered[int(0.95 * (len(ordered) - 1))]
        if p95 > self.budget_s:
            self._good_since = None
            if self._bad_since is None:
                self._bad_since = now
            elif now - self._bad_since >= 2.0:
                self._bad_since = now
                self.degrade()
        else:
            self._bad_since = None
            if self._good_since is None:
                self._good_since = now
            elif now - self._good_since >= 10.0:
                self._good_since = now
                self.recover()

    def degrade(self) -> None:
        if self.level >= self.MAX_LEVEL:
            if self.effective_fps > self.min_fps:
                self.effective_fps = max(self.min_fps, self.effective_fps - 5)
                log.warning("face: frame budget exceeded, fps now %d", self.effective_fps)
            return
        self.level += 1
        if self.level == self.MAX_LEVEL:
            self.effective_fps = max(self.min_fps, self.target_fps - 5)
        log.warning("face: frame budget exceeded, quality level %d", self.level)
        self._frames.clear()

    def recover(self) -> None:
        if self.effective_fps < self.target_fps:
            self.effective_fps = min(self.target_fps, self.effective_fps + 5)
            log.info("face: recovering, fps now %d", self.effective_fps)
            return
        if self.level > 0:
            self.level -= 1
            log.info("face: recovering, quality level %d", self.level)
        self._frames.clear()

    @property
    def idle_drift(self) -> bool:
        return self.level < 1

    @property
    def antialias(self) -> bool:
        return self.level < 2

    @property
    def saccade_rate(self) -> float:
        return 1.0 if self.level < 3 else 0.5


class FaceRenderer:
    def __init__(self, cfg: FaceConfig, panel: PanelGeometry) -> None:
        self.cfg = cfg
        self.panel = panel
        self.eye_color = parse_color(cfg.eye_color)
        self.bg_color = parse_color(cfg.background)
        if panel.is_mono:
            self.eye_color = (255, 255, 255)
            self.bg_color = (0, 0, 0)
        self.eye_separation = cfg.eye_separation * (0.88 if panel.shape == "round" else 1.0)
        self.design_scale = cfg.design_scale
        self.transform = compute_transform(panel, self.design_scale)
        self._mask: pygame.Surface | None = None
        if panel.shape == "round":
            self._mask = self._make_round_mask()

    def _make_round_mask(self) -> pygame.Surface:
        w, h = self.panel.width, self.panel.height
        mask = pygame.Surface((w, h), pygame.SRCALPHA)
        mask.fill((*self.bg_color, 255))
        r = min(w, h) // 2
        pygame.draw.circle(mask, (0, 0, 0, 0), (w // 2, h // 2), r)
        return mask

    def render(self, face: FaceParams, surface: pygame.Surface, *, antialias: bool | None = None) -> None:
        aa = self.cfg.antialias if antialias is None else antialias
        if self.panel.is_mono or self.panel.is_tiny:
            aa = False
        surface.fill(self.bg_color)
        shapes = face_shapes(face, eye_separation=self.eye_separation)
        for eye in (shapes.left, shapes.right):
            self._polygon(surface, self.transform.apply(eye.fill), self.eye_color, aa)
            for occ in eye.occluders:
                self._polygon(surface, self.transform.apply(occ), self.bg_color, aa)
        if self._mask is not None:
            surface.blit(self._mask, (0, 0))

    @staticmethod
    def _polygon(surface: pygame.Surface, pts: list[tuple[int, int]], color: Color, aa: bool) -> None:
        if len(pts) < 3:
            return
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        if max(xs) - min(xs) < 1 or max(ys) - min(ys) < 1:
            return
        try:
            pygame.gfxdraw.filled_polygon(surface, pts, color)
            if aa:
                pygame.gfxdraw.aapolygon(surface, pts, color)
        except (OverflowError, ValueError):
            # coordinates far outside the surface; clip by drawing with pygame.draw instead
            pygame.draw.polygon(surface, color, pts)


def design_bounds() -> tuple[float, float]:
    return DESIGN_WIDTH, DESIGN_HEIGHT
