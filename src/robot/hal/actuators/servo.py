"""One servo channel with the three protections that keep MG90S gears and coils alive.

* hard clamp to ``min_deg..max_deg`` below any caller,
* slew-rate limit (``max_deg_s``) so no command is an instant full-travel step,
* auto-relax after ``relax_after_s`` idle: pulses stop, the servo stops fighting the stop.
"""

from __future__ import annotations

import logging

from robot.core.config import ChannelConfig
from robot.hal.actuators.pca9685 import ServoDriver

log = logging.getLogger(__name__)


class ServoChannel:
    def __init__(self, name: str, cfg: ChannelConfig, driver: ServoDriver) -> None:
        assert cfg.channel is not None
        self.name = name
        self.cfg = cfg
        self.driver = driver
        self.channel = cfg.channel
        self.position = 0.0  # last commanded (slewed) angle, degrees
        self.target = 0.0
        self.relaxed = True
        self._idle_s = 0.0
        self.clamps = 0

    def set_target(self, degrees: float) -> float:
        """Clamp and set; returns the clamped target. Wakes a relaxed servo."""
        clamped = max(self.cfg.min_deg, min(self.cfg.max_deg, degrees))
        if clamped != degrees:
            self.clamps += 1
            log.warning(
                "%s: %.1f deg clamped to %.1f (limits %.0f..%.0f)",
                self.name,
                degrees,
                clamped,
                self.cfg.min_deg,
                self.cfg.max_deg,
            )
        if abs(clamped - self.target) > 1e-6 or self.relaxed:
            self._idle_s = 0.0
        self.target = clamped
        if self.relaxed:
            # a relaxed servo's real position is unknown; assume it stayed where we left it
            self.relaxed = False
        return clamped

    def relax(self) -> None:
        if not self.relaxed:
            self.driver.set_pulse_us(self.channel, 0.0)
            self.relaxed = True

    def pulse_for(self, degrees: float) -> float:
        sign = -1.0 if self.cfg.invert else 1.0
        return self.cfg.center_us + sign * degrees * self.cfg.us_per_deg

    def update(self, dt: float) -> None:
        if self.relaxed:
            return
        max_step = self.cfg.max_deg_s * dt
        delta = self.target - self.position
        if abs(delta) > 1e-3:
            step = max(-max_step, min(max_step, delta))
            self.position += step
            self.driver.set_pulse_us(self.channel, self.pulse_for(self.position))
            self._idle_s = 0.0
        else:
            self._idle_s += dt
            if self._idle_s >= self.cfg.relax_after_s:
                self.relax()

    @property
    def moving(self) -> bool:
        return not self.relaxed and abs(self.target - self.position) > 1e-3
