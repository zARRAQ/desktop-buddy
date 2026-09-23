"""Turns expression requests and gaze targets into a continuous stream of face parameters.

Handles transitions, blinks, saccades, idle drift and temporary "hold then return"
expressions. Pure logic, no rendering, so it is fully unit-testable with a fake clock.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from robot.core.config import FaceConfig
from robot.face import expressions
from robot.face.procedural import FaceParams, closed_eyes, ease_in_out, lerp

LOOK_RANGE_X = 45.0  # design units at look x = +-1 (before X_FACTOR)
LOOK_RANGE_Y = 60.0


@dataclass
class QualityFlags:
    idle_drift: bool = True
    saccade_rate: float = 1.0  # 1.0 normal, 0.5 half rate


class FaceAnimator:
    def __init__(self, cfg: FaceConfig, *, rng: random.Random | None = None) -> None:
        self.cfg = cfg
        self.rng = rng or random.Random()
        self.quality = QualityFlags(idle_drift=cfg.idle_drift)
        self.expression = "neutral"
        self._from = expressions.get("neutral")
        self._to = self._from.copy()
        self._transition_t = 1.0  # 1 = finished
        self._hold_return: str | None = None
        self._hold_until: float | None = None
        self.time = 0.0
        # gaze
        self.look_x = 0.0
        self.look_y = 0.0
        self._gaze_x = 0.0
        self._gaze_y = 0.0
        # blink
        self._blink_t: float | None = None
        self._next_blink = self._schedule(cfg.blink_interval_s)
        # saccade
        self._sacc_x = 0.0
        self._sacc_y = 0.0
        self._sacc_target = (0.0, 0.0)
        self._next_saccade = self._schedule(cfg.saccade_interval_s)
        self.blinks = 0

    # -- inputs ----------------------------------------------------------------------
    def set_expression(self, name: str, *, hold_ms: int | None = None) -> None:
        key = expressions.resolve(name)
        if hold_ms is not None:
            if self._hold_return is None:
                self._hold_return = self.expression
            self._hold_until = self.time + hold_ms / 1000.0
        else:
            self._hold_return = None
            self._hold_until = None
        if key == self.expression and self._transition_t >= 1.0:
            return
        self._from = self.current_target()
        self._to = expressions.get(key)
        self.expression = key
        self._transition_t = 0.0

    def look(self, x: float, y: float) -> None:
        self.look_x = max(-1.0, min(1.0, x))
        self.look_y = max(-1.0, min(1.0, y))

    def blink(self) -> None:
        if self._blink_t is None:
            self._blink_t = 0.0

    # -- update ----------------------------------------------------------------------
    def current_target(self) -> FaceParams:
        if self._transition_t >= 1.0:
            return self._to.copy()
        return lerp(self._from, self._to, ease_in_out(self._transition_t))

    def update(self, dt: float) -> FaceParams:
        self.time += dt
        if self._transition_t < 1.0:
            self._transition_t = min(1.0, self._transition_t + dt * 1000.0 / max(self.cfg.transition_ms, 1))
        if self._hold_until is not None and self.time >= self._hold_until and self._hold_return:
            back = self._hold_return
            self._hold_return = None
            self._hold_until = None
            self.set_expression(back)

        # gaze follows the requested look with a short lag
        k = min(1.0, dt * 12.0)
        self._gaze_x += (self.look_x - self._gaze_x) * k
        self._gaze_y += (self.look_y - self._gaze_y) * k

        # saccades
        if self.time >= self._next_saccade:
            amp = 6.0 if self.quality.saccade_rate >= 1.0 else 3.0
            self._sacc_target = (self.rng.uniform(-amp, amp), self.rng.uniform(-amp, amp))
            self._next_saccade = self.time + self._schedule(self.cfg.saccade_interval_s) / max(
                self.quality.saccade_rate, 0.25
            )
        ks = min(1.0, dt * 25.0)
        self._sacc_x += (self._sacc_target[0] - self._sacc_x) * ks
        self._sacc_y += (self._sacc_target[1] - self._sacc_y) * ks

        # blinks
        if self._blink_t is None and self.time >= self._next_blink:
            self._blink_t = 0.0
        blink_amount = 0.0
        if self._blink_t is not None:
            self._blink_t += dt * 1000.0 / max(self.cfg.blink_duration_ms, 1)
            if self._blink_t >= 1.0:
                self._blink_t = None
                self.blinks += 1
                self._next_blink = self.time + self._schedule(self.cfg.blink_interval_s)
            else:
                blink_amount = math.sin(self._blink_t * math.pi)

        face = self.current_target()
        drift_x = drift_y = 0.0
        if self.quality.idle_drift:
            drift_x = 1.5 * math.sin(self.time * 0.7) + 0.8 * math.sin(self.time * 1.9 + 1.0)
            drift_y = 1.2 * math.sin(self.time * 0.5 + 2.0)
        face.center_x += self._gaze_x * LOOK_RANGE_X + self._sacc_x + drift_x
        face.center_y += self._gaze_y * LOOK_RANGE_Y + self._sacc_y + drift_y
        # Looking sideways brings the near eye slightly forward, like Cozmo
        squint = 0.08 * abs(self._gaze_x)
        if self._gaze_x > 0:
            face.right.scale_x *= 1.0 + squint
            face.left.scale_x *= 1.0 - squint
        else:
            face.left.scale_x *= 1.0 + squint
            face.right.scale_x *= 1.0 - squint
        if blink_amount > 0.0:
            face = closed_eyes(face, blink_amount)
        return face

    def _schedule(self, interval: tuple[float, float]) -> float:
        lo, hi = interval
        return self.rng.uniform(min(lo, hi), max(lo, hi))
