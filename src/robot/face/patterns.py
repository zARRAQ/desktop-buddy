"""Test patterns for ``robot display test --pattern``.

Colour bars tell you RGB from BGR, the grid shows scaling and alignment, the circle shows
whether a round panel's mask is centred, and the "F" in the top-left corner tells you the
rotation and whether the image is mirrored, in one glance.
"""

from __future__ import annotations

import pygame

BARS = [
    (255, 255, 255),
    (255, 255, 0),
    (0, 255, 255),
    (0, 255, 0),
    (255, 0, 255),
    (255, 0, 0),
    (0, 0, 255),
    (0, 0, 0),
]


def draw_pattern(surface: pygame.Surface, *, round_mask: bool = False) -> None:
    w, h = surface.get_size()
    surface.fill((0, 0, 0))
    bar_w = w / len(BARS)
    for i, color in enumerate(BARS):
        pygame.draw.rect(surface, color, pygame.Rect(int(i * bar_w), 0, int(bar_w) + 1, h // 3))
    # labelled red / green / blue squares so you can name the colour you actually see
    sq = max(8, min(w, h) // 8)
    for i, color in enumerate([(255, 0, 0), (0, 255, 0), (0, 0, 255)]):
        pygame.draw.rect(surface, color, pygame.Rect(w // 2 - sq * 3 // 2 + i * sq, h // 3 + sq // 2, sq, sq))
    # grid
    step = max(10, min(w, h) // 8)
    for x in range(0, w, step):
        pygame.draw.line(surface, (90, 90, 90), (x, h // 3), (x, h), 1)
    for y in range(h // 3, h, step):
        pygame.draw.line(surface, (90, 90, 90), (0, y), (w, y), 1)
    # border and centre marks
    pygame.draw.rect(surface, (255, 255, 255), pygame.Rect(0, 0, w, h), 2)
    pygame.draw.line(surface, (255, 255, 255), (w // 2, 0), (w // 2, h), 1)
    pygame.draw.line(surface, (255, 255, 255), (0, h // 2), (w, h // 2), 1)
    # inscribed circle: on a round panel this should touch the bezel all around
    r = min(w, h) // 2 - 2
    pygame.draw.circle(surface, (255, 200, 0), (w // 2, h // 2), r, 2)
    # orientation glyph: a block "F" in the top-left corner
    g = max(12, min(w, h) // 10)
    x0, y0 = 6, 6
    t = max(2, g // 6)
    pygame.draw.rect(surface, (255, 255, 255), pygame.Rect(x0, y0, t, g))
    pygame.draw.rect(surface, (255, 255, 255), pygame.Rect(x0, y0, g * 2 // 3, t))
    pygame.draw.rect(surface, (255, 255, 255), pygame.Rect(x0, y0 + g // 2, g // 2, t))
    if round_mask:
        pygame.draw.circle(surface, (255, 0, 0), (w // 2, h // 2), r - 4, 1)
