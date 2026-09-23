"""Gesture timelines: (time_s, pan_deg | None, tilt_deg | None) keyframes, linearly interpolated."""

from __future__ import annotations

import itertools
from dataclasses import dataclass

Keyframe = tuple[float, float | None, float | None]

NOD: list[Keyframe] = [(0.0, None, 0.0), (0.25, None, 18.0), (0.5, None, -8.0), (0.75, None, 12.0), (1.0, None, 0.0)]
SHAKE: list[Keyframe] = [(0.0, 0.0, None), (0.2, -25.0, None), (0.5, 25.0, None), (0.8, -15.0, None), (1.0, 0.0, None)]
CENTER: list[Keyframe] = [(0.0, 0.0, 0.0)]


@dataclass
class Gesture:
    frames: list[Keyframe]
    t: float = 0.0

    @property
    def duration(self) -> float:
        return self.frames[-1][0] if self.frames else 0.0

    @property
    def done(self) -> bool:
        return self.t >= self.duration

    def sample(self, dt: float) -> tuple[float | None, float | None]:
        self.t = min(self.duration, self.t + dt)
        pan = self._interp(1)
        tilt = self._interp(2)
        return pan, tilt

    def _interp(self, idx: int) -> float | None:
        pts = [(f[0], f[idx]) for f in self.frames if f[idx] is not None]
        if not pts:
            return None
        if self.t <= pts[0][0]:
            return float(pts[0][1])  # type: ignore[arg-type]
        for (t0, v0), (t1, v1) in itertools.pairwise(pts):
            if t0 <= self.t <= t1:
                assert v0 is not None and v1 is not None
                if t1 == t0:
                    return float(v1)
                return float(v0 + (v1 - v0) * (self.t - t0) / (t1 - t0))
        return float(pts[-1][1])  # type: ignore[arg-type]
