"""The actuator map: roles -> channels, built from configuration, backed by real or mock drivers.

Roles: ``drive`` (exactly two channels for a drivetrain), ``pan``, ``tilt``, ``lift``,
``arm_left``, ``arm_right``. A role that is absent simply makes its primitives unavailable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from robot.core.config import ActuatorsConfig
from robot.hal.actuators.dc import Drv8833, HBridge, MockHBridge, Tb6612fng
from robot.hal.actuators.drive import DifferentialDrive, Wheel
from robot.hal.actuators.pca9685 import GpioServoDriver, MockServoDriver, Pca9685, ServoDriver
from robot.hal.actuators.servo import ServoChannel
from robot.hal.gpio.base import GpioBackend
from robot.hal.i2c.base import I2cBus

log = logging.getLogger(__name__)


@dataclass
class ActuatorMap:
    servos: dict[str, ServoChannel] = field(default_factory=dict)  # role -> channel
    drive: DifferentialDrive | None = None
    bridge: HBridge | None = None
    servo_driver: ServoDriver | None = None
    layout: str = "none"

    @classmethod
    def from_config(
        cls,
        cfg: ActuatorsConfig,
        gpio: GpioBackend,
        i2c: I2cBus | None,
        *,
        mock: bool = False,
    ) -> ActuatorMap:
        m = cls(layout=cfg.layout)
        servo_channels = {n: c for n, c in cfg.channels.items() if c.driver == "servo"}
        drive_channels = {n: c for n, c in cfg.channels.items() if c.driver == "dc"}

        if servo_channels and cfg.drivers.servo.type != "none":
            if mock or (i2c is None and cfg.drivers.servo.type == "pca9685"):
                m.servo_driver = MockServoDriver()
            elif cfg.drivers.servo.type == "pca9685":
                assert i2c is not None
                m.servo_driver = Pca9685(i2c, cfg.drivers.servo.i2c_address, cfg.drivers.servo.pwm_hz)
            else:
                pins = {c.channel: c.channel for c in servo_channels.values() if c.channel is not None}
                m.servo_driver = GpioServoDriver(gpio, pins, cfg.drivers.servo.pwm_hz)
            for name, c in servo_channels.items():
                if c.role in m.servos:
                    log.warning("duplicate role %s (%s); keeping the first", c.role, name)
                    continue
                m.servos[c.role] = ServoChannel(name, c, m.servo_driver)

        if len(drive_channels) == 2 and cfg.drivers.dc.type != "none":
            if mock:
                m.bridge = MockHBridge()
            elif cfg.drivers.dc.type == "tb6612fng":
                m.bridge = Tb6612fng(
                    gpio, cfg.drivers.dc.pins, cfg.drivers.dc.pwm_hz, hardware_pwm=cfg.drivers.dc.hardware_pwm
                )
            else:
                m.bridge = Drv8833(
                    gpio, cfg.drivers.dc.pins, cfg.drivers.dc.pwm_hz, hardware_pwm=cfg.drivers.dc.hardware_pwm
                )
            wheels = []
            for name, c in sorted(drive_channels.items(), key=lambda kv: kv[1].motor or ""):
                enc = gpio.encoder(c.encoder.pin_a, c.encoder.pin_b) if c.encoder else None
                wheels.append(Wheel(name, c, m.bridge, enc))
            # "left" is whichever channel name contains "left", else motor a
            left = next((w for w in wheels if "left" in w.name.lower()), wheels[0])
            right = next(w for w in wheels if w is not left)
            m.drive = DifferentialDrive(left, right, cfg.geometry, max_speed_mm_s=cfg.max_speed_mm_s)
        elif drive_channels:
            log.warning("drive needs exactly two dc channels, found %d; no drivetrain", len(drive_channels))
        return m

    # -- capabilities ------------------------------------------------------------------
    def roles(self) -> list[str]:
        out = list(self.servos)
        if self.drive is not None:
            out.append("drive")
        return sorted(out)

    def primitives(self) -> list[str]:
        prims: set[str] = {"stop", "relax"}
        if self.drive is not None:
            prims |= {"drive", "turn"}
        if "pan" in self.servos or "tilt" in self.servos:
            prims |= {"look_at", "center"}
        if "tilt" in self.servos:
            prims.add("nod")
        if "pan" in self.servos:
            prims.add("shake")
        elif self.drive is not None:
            prims.add("shake")  # layout B: shake by wiggling the treads
        if "lift" in self.servos:
            prims.add("lift")
        if "arm_left" in self.servos or "arm_right" in self.servos:
            prims.add("wave")
        return sorted(prims)

    def update(self, dt: float) -> None:
        for s in self.servos.values():
            s.update(dt)
        if self.drive is not None:
            self.drive.update(dt)

    def relax_all(self) -> None:
        for s in self.servos.values():
            s.relax()

    def stop_all(self) -> None:
        if self.drive is not None:
            self.drive.stop()

    def close(self) -> None:
        self.stop_all()
        self.relax_all()
        if self.bridge is not None:
            self.bridge.close()
        if self.servo_driver is not None:
            self.servo_driver.close()
