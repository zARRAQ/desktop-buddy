"""Motion service."""

from __future__ import annotations

import time

from robot.core.bus import BusClient
from robot.core.config import RobotConfig
from robot.core.messages import (
    Envelope,
    MotionCommand,
    MotionHeartbeat,
    MotionOdometry,
    MotionState,
    SafetyState,
)
from robot.core.service import Service
from robot.hal.actuators.map import ActuatorMap
from robot.hal.gpio.base import GpioBackend, open_gpio_backend
from robot.hal.i2c.base import I2cBus, open_i2c_bus
from robot.motion.primitives import CENTER, NOD, SHAKE, Gesture

SAFETY_STALE_S = 1.0


class MotionService(Service):
    name = "motion"
    subscriptions = ("motion.command", "safety.state")
    tick_hz = 50.0

    def __init__(
        self,
        config: RobotConfig,
        bus: BusClient,
        *,
        actuators: ActuatorMap | None = None,
        gpio: GpioBackend | None = None,
        i2c: I2cBus | None = None,
        mock: bool | None = None,
    ) -> None:
        super().__init__(config, bus)
        self._actuators = actuators
        self._gpio = gpio
        self._i2c = i2c
        self._mock = mock
        self.actuators: ActuatorMap | None = None
        self.safety_enabled = False
        self._safety_seen = 0.0
        self._gesture: Gesture | None = None
        self._drive_until: float | None = None
        self._drive_target: tuple[str, float] | None = None  # ("distance", mm) | ("angle", deg)
        self._drive_start_pose: tuple[float, float, float] | None = None
        self._hb_seq = 0
        self._last_hb = 0.0
        self._last_state = 0.0
        self._last_odom = 0.0
        self.refused = 0

    def setup(self) -> None:
        if self._actuators is not None:
            self.actuators = self._actuators
        else:
            gpio = self._gpio or open_gpio_backend()
            mock = self._mock if self._mock is not None else gpio.name == "mock"
            i2c = (
                self._i2c
                if self._i2c is not None
                else (None if mock else open_i2c_bus(self.config.actuators.drivers.servo.i2c_bus))
            )
            self.actuators = ActuatorMap.from_config(self.config.actuators, gpio, i2c, mock=mock)
        self.log.info(
            "layout %s roles=%s primitives=%s",
            self.actuators.layout,
            self.actuators.roles(),
            self.actuators.primitives(),
        )

    # -- messages -----------------------------------------------------------------------
    def on_message(self, env: Envelope) -> None:
        if env.topic == SafetyState.TOPIC:
            st = SafetyState.model_validate(env.data)
            self._safety_seen = time.monotonic()
            if self.safety_enabled and not st.enabled:
                self._halt_drive()
            self.safety_enabled = st.enabled
        elif env.topic == MotionCommand.TOPIC:
            self.handle(MotionCommand.model_validate(env.data))

    @property
    def drive_allowed(self) -> bool:
        return self.safety_enabled and (time.monotonic() - self._safety_seen) < SAFETY_STALE_S

    def handle(self, cmd: MotionCommand) -> None:
        a = self.actuators
        if a is None:
            return
        prims = a.primitives()
        if cmd.type not in prims:
            self.log.debug("primitive %s unavailable in layout %s", cmd.type, a.layout)
            return
        if cmd.type == "stop":
            self._halt_drive()
            self._gesture = None
        elif cmd.type == "relax":
            a.relax_all()
        elif cmd.type == "center":
            self._gesture = Gesture(CENTER)
        elif cmd.type == "look_at":
            if "pan" in a.servos and cmd.yaw_deg is not None:
                a.servos["pan"].set_target(cmd.yaw_deg)
            if "tilt" in a.servos and cmd.pitch_deg is not None:
                a.servos["tilt"].set_target(cmd.pitch_deg)
        elif cmd.type == "nod":
            self._gesture = Gesture(NOD)
        elif cmd.type == "shake":
            if "pan" in a.servos:
                self._gesture = Gesture(SHAKE)
            elif a.drive is not None:
                self._start_drive(0.0, 90.0, duration=0.6, target=None)
        elif cmd.type in ("drive", "turn"):
            self._start_drive(
                cmd.speed_mm_s if cmd.speed_mm_s is not None else (120.0 if cmd.type == "drive" else 0.0),
                cmd.turn_deg_s if cmd.turn_deg_s is not None else (0.0 if cmd.type == "drive" else 90.0),
                duration=cmd.duration_s,
                target=("distance", cmd.distance_mm)
                if cmd.distance_mm is not None
                else (("angle", cmd.angle_deg) if cmd.angle_deg is not None else None),
            )

    def _start_drive(self, v: float, w: float, *, duration: float | None, target: tuple[str, float] | None) -> None:
        a = self.actuators
        if a is None or a.drive is None:
            return
        if not self.drive_allowed:
            self.refused += 1
            self.log.warning("drive refused: safety not enabled (or stale)")
            return
        if target is not None and target[0] == "angle" and w == 0.0:
            w = 90.0 if target[1] >= 0 else -90.0
        if target is not None and target[0] == "distance" and v == 0.0:
            v = 120.0 if target[1] >= 0 else -120.0
        a.drive.set_velocity(v, w)
        now = time.monotonic()
        self._drive_until = now + (duration if duration is not None else (2.0 if target is None else 30.0))
        self._drive_target = target
        p = a.drive.pose
        self._drive_start_pose = (p.x_mm, p.y_mm, p.theta_deg)

    def _halt_drive(self) -> None:
        if self.actuators is not None and self.actuators.drive is not None:
            self.actuators.drive.stop()
        self._drive_until = None
        self._drive_target = None

    # -- loop ----------------------------------------------------------------------------
    def tick(self, dt: float) -> None:
        a = self.actuators
        if a is None:
            return
        now = time.monotonic()
        if self._gesture is not None:
            pan, tilt = self._gesture.sample(dt)
            if pan is not None and "pan" in a.servos:
                a.servos["pan"].set_target(pan)
            if tilt is not None and "tilt" in a.servos:
                a.servos["tilt"].set_target(tilt)
            if self._gesture.done:
                self._gesture = None
        if self._drive_until is not None and (
            not self.drive_allowed or now >= self._drive_until or self._target_reached()
        ):
            self._halt_drive()
        a.update(dt)
        if now - self._last_hb >= 0.1:
            self._last_hb = now
            self._hb_seq += 1
            self.bus.publish_payload(MotionHeartbeat(seq=self._hb_seq))
        want_odom = self._drive_until is not None or now - self._last_odom >= 1.0
        if a.drive is not None and want_odom and now - self._last_odom >= 0.1:
            self._last_odom = now
            p = a.drive.pose
            self.bus.publish_payload(
                MotionOdometry(x_mm=p.x_mm, y_mm=p.y_mm, theta_deg=p.theta_deg, v_mm_s=p.v_mm_s, w_deg_s=p.w_deg_s)
            )
        if now - self._last_state >= 1.0:
            self._last_state = now
            self.bus.publish_payload(
                MotionState(
                    primitives=a.primitives(),
                    pan_deg=a.servos["pan"].position if "pan" in a.servos else None,
                    tilt_deg=a.servos["tilt"].position if "tilt" in a.servos else None,
                    driving=self._drive_until is not None,
                )
            )

    def _target_reached(self) -> bool:
        a = self.actuators
        if a is None or a.drive is None or self._drive_target is None or self._drive_start_pose is None:
            return False
        kind, value = self._drive_target
        p = a.drive.pose
        x0, y0, th0 = self._drive_start_pose
        if kind == "distance":
            return ((p.x_mm - x0) ** 2 + (p.y_mm - y0) ** 2) ** 0.5 >= abs(value)
        d = (p.theta_deg - th0 + 180.0) % 360.0 - 180.0
        return abs(d) >= abs(value)

    def teardown(self) -> None:
        if self.actuators is not None:
            self.actuators.close()
