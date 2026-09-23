"""Rotation and flip between the logical (upright) face and the panel's physical orientation."""

from __future__ import annotations

import pygame


def logical_size(physical: tuple[int, int], rotation: int) -> tuple[int, int]:
    w, h = physical
    return (h, w) if rotation in (90, 270) else (w, h)


def to_physical(logical: pygame.Surface, rotation: int, flip: str) -> pygame.Surface:
    """Apply flip then rotation. ``rotation`` is the clockwise angle the panel is mounted at."""
    out = logical
    if flip in ("h", "hv"):
        out = pygame.transform.flip(out, True, False)
    if flip in ("v", "hv"):
        out = pygame.transform.flip(out, False, True)
    if rotation:
        # pygame rotates counter-clockwise; the panel mounted 90 degrees clockwise needs
        # the image rotated 90 degrees counter-clockwise... which is what the user sees as
        # "upright" after mounting. We document rotation as "the value that makes it upright".
        out = pygame.transform.rotate(out, -rotation)
    return out
