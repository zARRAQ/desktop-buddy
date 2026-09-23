"""Power monitor service (INA219). Optional; enabled by ``power.enabled``."""

from __future__ import annotations

import time

from robot.core.bus import BusClient
from robot.core.config import RobotConfig
from robot.core.messages import SystemBattery
from robot.core.service import Service
from robot.hal.i2c.base import I2cBus, open_i2c_bus
from robot.hal.sensors.power import Ina219, MockPowerMonitor, PowerMonitor, li_ion_percent


class PowerService(Service):
    name = "power"
    tick_hz = 1.0

    def __init__(
        self, config: RobotConfig, bus: BusClient, *, monitor: PowerMonitor | None = None, i2c: I2cBus | None = None
    ) -> None:
        super().__init__(config, bus)
        self.monitor = monitor
        self._i2c = i2c
        self._last = 0.0
        self._warned = False

    def setup(self) -> None:
        if self.monitor is not None:
            return
        if not self.config.power.enabled:
            self.log.info("power monitor disabled; publishing nothing")
            return
        try:
            bus = self._i2c or open_i2c_bus(self.config.power.i2c_bus, prefer="smbus")
            self.monitor = Ina219(bus, self.config.power.ina219_address)
        except Exception as exc:
            self.log.error("INA219 unavailable (%s); using mock values", exc)
            self.monitor = MockPowerMonitor()

    def tick(self, dt: float) -> None:
        if self.monitor is None:
            return
        now = time.monotonic()
        if now - self._last < self.config.power.poll_s:
            return
        self._last = now
        r = self.monitor.read()
        pct = li_ion_percent(r.voltage, self.config.power.cells)
        self.bus.publish_payload(
            SystemBattery(voltage=round(r.voltage, 2), current_a=round(r.current_a, 3), percent=round(pct, 1))
        )
        if r.voltage < self.config.power.critical_voltage:
            self.bus.publish("safety.command", {"command": "estop", "reason": f"battery critical {r.voltage:.2f} V"})
        elif r.voltage < self.config.power.low_voltage and not self._warned:
            self._warned = True
            self.bus.publish("voice.say", {"text": "My battery is getting low.", "request_id": "power"})
