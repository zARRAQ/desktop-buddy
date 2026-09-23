"""Pure decision logic plus the enable-line driver. Testable with a fake clock and mocks.

The supervisor allows motion only while every condition holds:

* the motion service's heartbeat is fresh (a dead or hung motion process cannot leave a
  speed command latched in the H-bridge),
* no cliff sensor sees a drop,
* the IMU does not report pickup or excessive tilt,
* no external emergency stop is latched,
* the supervisor itself is alive: the enable line is driven from this process and only
  this process.

**About the enable line.** ``level`` mode holds GPIO26 high while allowed. Note that on a
Raspberry Pi a GPIO output keeps its last level when the process that drove it is killed
with SIGKILL; only an orderly shutdown resets it. ``pulse`` mode instead emits a square wave
that a small hardware pulse watchdog (diode, capacitor, resistor, MOSFET; see HARDWARE.md)
turns into the STBY level, so a dead process really does stop the motors within about
100 ms. ``robot safety killtest`` measures which of the two your wiring actually gives you.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from robot.core.config import SafetyConfig
from robot.hal.gpio.base import DigitalOutput
from robot.hal.sensors.cliff import CliffSensors
from robot.hal.sensors.imu import Imu

log = logging.getLogger(__name__)


@dataclass
class SafetyDecision:
    enabled: bool
    reason: str
    cliff_mm: dict[str, int] = field(default_factory=dict)
    tilt_deg: float | None = None
    picked_up: bool = False


class SafetyLogic:
    def __init__(self, cfg: SafetyConfig, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.cfg = cfg
        self.clock = clock
        self.started = clock()
        self.last_motion_heartbeat: float | None = None
        self.estop_reason: str | None = None
        self.cliff_latched: str | None = None

    def motion_heartbeat(self) -> None:
        self.last_motion_heartbeat = self.clock()

    def estop(self, reason: str) -> None:
        self.estop_reason = reason

    def reset(self) -> None:
        self.estop_reason = None
        self.cliff_latched = None

    def evaluate(
        self, cliff: dict[str, int] | None, imu_tilt_deg: float | None, imu_magnitude_g: float | None
    ) -> SafetyDecision:
        now = self.clock()
        cliff = cliff or {}
        picked_up = False
        if self.estop_reason:
            return SafetyDecision(False, f"estop: {self.estop_reason}", cliff, imu_tilt_deg)
        if self.last_motion_heartbeat is None:
            if now - self.started < self.cfg.startup_grace_s:
                return SafetyDecision(False, "waiting for motion service", cliff, imu_tilt_deg)
            return SafetyDecision(False, "motion service not running", cliff, imu_tilt_deg)
        if (now - self.last_motion_heartbeat) * 1000.0 > self.cfg.motion_heartbeat_timeout_ms:
            return SafetyDecision(False, "motion heartbeat lost", cliff, imu_tilt_deg)
        thresholds = {s.name: s.threshold_mm for s in self.cfg.cliff.sensors}
        for name, mm in cliff.items():
            if mm > thresholds.get(name, 60):
                self.cliff_latched = name
        if self.cliff_latched:
            # latch until every sensor sees floor again, then release automatically
            if all(mm <= thresholds.get(n, 60) for n, mm in cliff.items()) and cliff:
                self.cliff_latched = None
            else:
                return SafetyDecision(False, f"cliff: {self.cliff_latched}", cliff, imu_tilt_deg)
        if imu_magnitude_g is not None and abs(imu_magnitude_g - 1.0) > (self.cfg.imu.pickup_accel_g - 1.0):
            picked_up = True
            return SafetyDecision(False, "picked up", cliff, imu_tilt_deg, picked_up=True)
        if imu_tilt_deg is not None and imu_tilt_deg > self.cfg.imu.tilt_deg:
            return SafetyDecision(False, f"tilted {imu_tilt_deg:.0f} deg", cliff, imu_tilt_deg)
        return SafetyDecision(True, "ok", cliff, imu_tilt_deg, picked_up)


class EnableLine:
    """Drives the enable GPIO. In pulse mode a thread toggles it at ``heartbeat_hz`` only
    while allowed; in level mode it is simply high while allowed."""

    def __init__(self, pin: DigitalOutput, *, mode: str = "level", heartbeat_hz: float = 50.0) -> None:
        self.pin = pin
        self.mode = mode
        self.period = 1.0 / (2.0 * max(1.0, heartbeat_hz))
        self._allowed = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.pin.write(False)
        if mode == "pulse":
            self._thread = threading.Thread(target=self._pulse_loop, name="enable-pulse", daemon=True)
            self._thread.start()

    def set_allowed(self, allowed: bool) -> None:
        self._allowed = allowed
        if self.mode == "level":
            self.pin.write(allowed)
        elif not allowed:
            self.pin.write(False)

    @property
    def allowed(self) -> bool:
        return self._allowed

    def _pulse_loop(self) -> None:
        level = False
        while not self._stop.is_set():
            if self._allowed:
                level = not level
                self.pin.write(level)
            time.sleep(self.period)

    def close(self) -> None:
        self._stop.set()
        self._allowed = False
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        self.pin.write(False)
        self.pin.close()


class Supervisor:
    """Ties logic, sensors and the enable line together. Call :meth:`step` regularly."""

    def __init__(
        self,
        cfg: SafetyConfig,
        line: EnableLine,
        *,
        cliff: CliffSensors | None = None,
        imu: Imu | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.cfg = cfg
        self.logic = SafetyLogic(cfg, clock=clock)
        self.line = line
        self.cliff = cliff
        self.imu = imu
        self.last: SafetyDecision | None = None

    def step(self) -> SafetyDecision:
        cliff = self.cliff.read() if self.cliff is not None else None
        tilt = mag = None
        if self.imu is not None:
            try:
                r = self.imu.read()
                tilt, mag = r.tilt_deg, r.magnitude_g
            except OSError as exc:
                log.warning("imu read failed: %s", exc)
                self.logic.estop("imu failure")
        decision = self.logic.evaluate(cliff, tilt, mag)
        self.line.set_allowed(decision.enabled)
        changed = self.last is None or decision.enabled != self.last.enabled or decision.reason != self.last.reason
        if changed:
            (log.info if decision.enabled else log.warning)(
                "safety: %s (%s)", "ENABLED" if decision.enabled else "DISABLED", decision.reason
            )
        self.last = decision
        return decision

    def close(self) -> None:
        self.line.close()
        if self.cliff is not None:
            self.cliff.close()
        if self.imu is not None:
            self.imu.close()
