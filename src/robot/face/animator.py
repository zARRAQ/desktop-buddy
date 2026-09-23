"""Turns expression requests and gaze targets into a continuous stream of face parameters.

Handles transitions, blinks, saccades, idle drift and temporary "hold then return"
expressions. Pure logic, no rendering, so it is fully unit-testable with a fake clock.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from robot.core.config import FaceConfig
from robot.face import expressions
from robot.face.procedural import (
    BAR_COUNT,
    OVERLAY_EYE_LIFT,
    Y_FACTOR,
    FaceParams,
    closed_eyes,
    ease_in_out,
    lerp,
)

LOOK_RANGE_X = 45.0  # design units at look x = +-1 (before X_FACTOR)
LOOK_RANGE_Y = 60.0

MODES: tuple[str, ...] = ("none", "listening", "thinking", "speaking")

# Bars bob at these rates (Hz) with these phases so the meter looks alive without a microphone
_BAR_RATES = (1.9, 2.7, 3.3, 2.3, 1.6)
_BAR_PHASES = (0.0, 1.3, 2.1, 0.7, 2.9)


@dataclass
class QualityFlags:
    idle_drift: bool = True
    saccade_rate: float = 1.0  # 1.0 normal, 0.5 half rate


@dataclass
class Overlay:
    """What to draw below the eyes this frame. ``blend`` fades the overlay in and out."""

    mode: str = "none"
    blend: float = 0.0
    mouth_open: float = 0.0  # speaking: 0 closed .. 1 wide open
    levels: list[float] = field(default_factory=list)  # listening: one per bar
    phase: float = 0.0  # thinking: cycles

    @property
    def visible(self) -> bool:
        return self.mode != "none" and self.blend > 0.02


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
        # overlays
        self.mode = "none"
        self._mode_blend = 0.0
        self._mode_since = 0.0
        self.speech_level: float | None = None  # set from real TTS amplitude when available
        self.mic_level: float | None = None

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

    def set_mode(self, mode: str) -> None:
        """Show the listening bars, thinking dots or mouth; ``none`` hides them."""
        if mode not in MODES:
            raise KeyError(mode)
        if mode == self.mode:
            return
        self.mode = mode
        self._mode_since = self.time
        if mode == "none":
            self.speech_level = None
            self.mic_level = None

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

        # overlays fade in and out over the same time as an expression transition
        step = dt * 1000.0 / max(self.cfg.transition_ms, 1)
        target = 0.0 if self.mode == "none" else 1.0
        self._mode_blend += max(-step, min(step, target - self._mode_blend))
        if self._mode_blend > 0.0:
            face.center_y -= OVERLAY_EYE_LIFT * ease_in_out(self._mode_blend) / Y_FACTOR
        return face

    def overlay(self) -> Overlay:
        """The overlay for the frame produced by the last :meth:`update`."""
        ov = Overlay(mode=self.mode, blend=ease_in_out(self._mode_blend))
        if not ov.visible:
            return ov
        t = self.time - self._mode_since
        if self.mode == "speaking":
            if self.speech_level is not None:
                ov.mouth_open = min(1.0, max(0.0, self.speech_level))
            else:
                # syllables at about 4 Hz inside a slower loudness envelope, with brief pauses
                syllable = max(0.0, math.sin(2.0 * math.pi * 4.1 * t))
                envelope = 0.55 + 0.45 * math.sin(2.0 * math.pi * 0.9 * t + 1.0)
                pause = 0.0 if math.sin(2.0 * math.pi * 0.37 * t) < -0.85 else 1.0
                ov.mouth_open = syllable * envelope * pause
        elif self.mode == "listening":
            if self.mic_level is not None:
                lvl = min(1.0, max(0.0, self.mic_level))
                ov.levels = [
                    lvl * (0.6 + 0.4 * math.sin(2.0 * math.pi * r * t + p))
                    for r, p in zip(_BAR_RATES, _BAR_PHASES, strict=True)
                ][:BAR_COUNT]
            else:
                ov.levels = [
                    0.5 + 0.5 * math.sin(2.0 * math.pi * r * t + p)
                    for r, p in zip(_BAR_RATES, _BAR_PHASES, strict=True)
                ][:BAR_COUNT]
        elif self.mode == "thinking":
            ov.phase = t * 0.8
        return ov

    def _schedule(self, interval: tuple[float, float]) -> float:
        lo, hi = interval
        return self.rng.uniform(min(lo, hi), max(lo, hi))
