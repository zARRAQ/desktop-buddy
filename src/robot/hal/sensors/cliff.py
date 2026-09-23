"""Downward-facing range sensors for cliff detection.

The real implementation talks to VL53L1X sensors behind a TCA9548A I2C mux. VL53L1X
register-level bring-up is long (ST's API is a few hundred register writes), so on the robot
this uses Pimoroni's ``vl53l1x`` package when installed, one instance re-used across mux
channels. Not yet exercised on real hardware.
"""

from __future__ import annotations

import contextlib
import logging
from abc import ABC, abstractmethod
from typing import Any

from robot.core.config import CliffConfig
from robot.hal.i2c.base import I2cBus

log = logging.getLogger(__name__)

OFF_EDGE_MM = 8190  # what VL53L1X reports when it sees nothing


class CliffSensors(ABC):
    @abstractmethod
    def read(self) -> dict[str, int]:
        """name -> millimetres to the surface. Large values mean 'no surface': a cliff."""

    def close(self) -> None: ...


class MockCliffSensors(CliffSensors):
    def __init__(self, names: list[str]) -> None:
        self.values: dict[str, int] = {n: 25 for n in names}

    def read(self) -> dict[str, int]:
        return dict(self.values)

    def set_off_edge(self, name: str, off: bool = True) -> None:
        self.values[name] = OFF_EDGE_MM if off else 25


class Tca9548a:
    def __init__(self, bus: I2cBus, address: int = 0x70) -> None:
        self.bus = bus
        self.address = address

    def select(self, channel: int) -> None:
        if not 0 <= channel <= 7:
            raise ValueError("mux channel 0..7")
        self.bus.write_byte(self.address, 1 << channel)

    def disable(self) -> None:
        self.bus.write_byte(self.address, 0)


class Vl53l1xArray(CliffSensors):
    def __init__(self, cfg: CliffConfig, bus: I2cBus, i2c_bus_number: int = 1) -> None:
        import VL53L1X  # Pimoroni package, optional

        self.cfg = cfg
        self.mux = Tca9548a(bus, cfg.mux_address)
        self._sensors: dict[str, Any] = {}
        for s in cfg.sensors:
            self.mux.select(s.mux_channel)
            tof = VL53L1X.VL53L1X(i2c_bus=i2c_bus_number, i2c_address=cfg.sensor_address)
            tof.open()
            tof.start_ranging(1)  # short range mode, fastest
            self._sensors[s.name] = tof
        self.mux.disable()

    def read(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for s in self.cfg.sensors:
            self.mux.select(s.mux_channel)
            try:
                out[s.name] = int(self._sensors[s.name].get_distance())
            except OSError as exc:
                log.warning("cliff sensor %s read failed: %s", s.name, exc)
                out[s.name] = OFF_EDGE_MM  # fail safe: treat as a cliff
        self.mux.disable()
        return out

    def close(self) -> None:
        for s in self.cfg.sensors:
            self.mux.select(s.mux_channel)
            with contextlib.suppress(OSError):
                self._sensors[s.name].stop_ranging()
        self.mux.disable()
