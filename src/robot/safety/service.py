"""Safety service process."""

from __future__ import annotations

import time

from robot.core.bus import BusClient
from robot.core.config import RobotConfig
from robot.core.messages import Envelope, MotionHeartbeat, SafetyEstop, SafetyState
from robot.core.service import Service
from robot.hal.gpio.base import GpioBackend, open_gpio_backend
from robot.hal.i2c.base import I2cBus, MockI2cBus, open_i2c_bus
from robot.hal.sensors.cliff import CliffSensors, MockCliffSensors
from robot.hal.sensors.imu import Imu, MockImu
from robot.safety.supervisor import EnableLine, Supervisor


class SafetyService(Service):
    name = "safety"
    subscriptions = ("motion.heartbeat", "safety.command")

    def __init__(
        self,
        config: RobotConfig,
        bus: BusClient,
        *,
        gpio: GpioBackend | None = None,
        i2c: I2cBus | None = None,
        cliff: CliffSensors | None = None,
        imu: Imu | None = None,
        enable_mode: str = "level",
    ) -> None:
        super().__init__(config, bus)
        self.tick_hz = max(config.safety.cliff.poll_hz, 10.0)
        self._gpio = gpio
        self._i2c = i2c
        self._cliff = cliff
        self._imu = imu
        self._mode = enable_mode
        self.supervisor: Supervisor | None = None
        self._last_publish = 0.0
        self._was_enabled: bool | None = None

    def setup(self) -> None:
        gpio = self._gpio or open_gpio_backend()
        i2c = self._i2c
        if i2c is None and (self.config.safety.cliff.enabled or self.config.safety.imu.enabled):
            i2c = open_i2c_bus(1)
        cliff = self._cliff
        if cliff is None and self.config.safety.cliff.enabled:
            cliff = self._open_cliff(i2c)
        imu = self._imu
        if imu is None and self.config.safety.imu.enabled:
            imu = self._open_imu(i2c)
        line = EnableLine(
            gpio.output(self.config.safety.enable_pin, initial=False),
            mode=self._mode,
            heartbeat_hz=self.config.safety.heartbeat_hz,
        )
        self.supervisor = Supervisor(self.config.safety, line, cliff=cliff, imu=imu)
        self.log.info("enable line GPIO%d mode=%s gpio=%s", self.config.safety.enable_pin, self._mode, gpio.name)

    def _open_cliff(self, i2c: I2cBus | None) -> CliffSensors:
        names = [s.name for s in self.config.safety.cliff.sensors]
        if i2c is None or isinstance(i2c, MockI2cBus):
            self.log.warning("cliff sensors enabled but no I2C bus: using mock (always 'floor present')")
            return MockCliffSensors(names)
        try:
            from robot.hal.sensors.cliff import Vl53l1xArray

            return Vl53l1xArray(self.config.safety.cliff, i2c)
        except Exception as exc:
            self.log.error("cliff sensors failed (%s); motion will stay DISABLED", exc)
            self.request_estop_on_setup = True
            return MockCliffSensors(names)

    def _open_imu(self, i2c: I2cBus | None) -> Imu:
        if i2c is None or isinstance(i2c, MockI2cBus):
            self.log.warning("imu enabled but no I2C bus: using mock")
            return MockImu()
        try:
            from robot.hal.sensors.imu import Mpu6050

            return Mpu6050(i2c, self.config.safety.imu.i2c_address)
        except OSError as exc:
            self.log.error("imu init failed (%s); continuing without pickup detection", exc)
            return MockImu()

    request_estop_on_setup: bool = False

    def on_message(self, env: Envelope) -> None:
        if self.supervisor is None:
            return
        if env.topic == MotionHeartbeat.TOPIC:
            self.supervisor.logic.motion_heartbeat()
        elif env.topic == "safety.command":
            cmd = env.data.get("command")
            if cmd == "estop":
                self.supervisor.logic.estop(str(env.data.get("reason", "commanded")))
            elif cmd == "reset":
                self.supervisor.logic.reset()

    def tick(self, dt: float) -> None:
        if self.supervisor is None:
            return
        if self.request_estop_on_setup:
            self.request_estop_on_setup = False
            self.supervisor.logic.estop("cliff sensors unavailable")
        d = self.supervisor.step()
        now = time.monotonic()
        changed = self._was_enabled is None or d.enabled != self._was_enabled
        if changed and self._was_enabled and not d.enabled:
            self.bus.publish_payload(SafetyEstop(reason=d.reason))
        if changed or now - self._last_publish >= 0.5:
            self._last_publish = now
            self._was_enabled = d.enabled
            self.bus.publish_payload(
                SafetyState(
                    enabled=d.enabled, reason=d.reason, cliff_mm=d.cliff_mm, tilt_deg=d.tilt_deg, picked_up=d.picked_up
                )
            )

    def teardown(self) -> None:
        if self.supervisor is not None:
            self.supervisor.close()
