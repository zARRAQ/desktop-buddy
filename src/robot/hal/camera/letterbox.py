"""Aspect-preserving resize with grey padding, and the inverse mapping for detections."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

PAD_VALUE = 114


@dataclass(frozen=True)
class Letterbox:
    in_w: int
    in_h: int
    out_w: int
    out_h: int
    scale: float
    pad_x: float
    pad_y: float

    def to_original(self, x: float, y: float) -> tuple[float, float]:
        return (x - self.pad_x) / self.scale, (y - self.pad_y) / self.scale

    def to_model(self, x: float, y: float) -> tuple[float, float]:
        return x * self.scale + self.pad_x, y * self.scale + self.pad_y

    def bbox_to_original(self, x1: float, y1: float, x2: float, y2: float) -> tuple[float, float, float, float]:
        ax, ay = self.to_original(x1, y1)
        bx, by = self.to_original(x2, y2)
        return (
            min(max(ax, 0.0), self.in_w),
            min(max(ay, 0.0), self.in_h),
            min(max(bx, 0.0), self.in_w),
            min(max(by, 0.0), self.in_h),
        )


def compute_letterbox(in_w: int, in_h: int, out_w: int, out_h: int) -> Letterbox:
    scale = min(out_w / in_w, out_h / in_h)
    new_w, new_h = in_w * scale, in_h * scale
    return Letterbox(in_w, in_h, out_w, out_h, scale, (out_w - new_w) / 2.0, (out_h - new_h) / 2.0)


def letterbox(image: np.ndarray, out_w: int, out_h: int) -> tuple[np.ndarray, Letterbox]:
    """Returns (out_h x out_w x 3 uint8, transform). Never stretches."""
    h, w = image.shape[:2]
    lb = compute_letterbox(w, h, out_w, out_h)
    new_w = max(1, round(w * lb.scale))
    new_h = max(1, round(h * lb.scale))
    resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((out_h, out_w, 3), PAD_VALUE, dtype=np.uint8)
    x0 = round(lb.pad_x)
    y0 = round(lb.pad_y)
    canvas[y0 : y0 + new_h, x0 : x0 + new_w] = resized
    return canvas, lb
