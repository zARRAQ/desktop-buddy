"""INA219 bus voltage / current monitor. Strap A0 so it lives at 0x41 (0x40 is the PCA9685)."""

from __future__ import annotations

import itertools
from abc import ABC, abstractmethod
from dataclasses import dataclass

from robot.hal.i2c.base import I2cBus

REG_CONFIG, REG_SHUNT, REG_BUS, REG_POWER, REG_CURRENT, REG_CAL = 0x00, 0x01, 0x02, 0x03, 0x04, 0x05


@dataclass
class PowerReading:
    voltage: float
    current_a: float

    @property
    def watts(self) -> float:
        return self.voltage * self.current_a


class PowerMonitor(ABC):
    @abstractmethod
    def read(self) -> PowerReading: ...


class MockPowerMonitor(PowerMonitor):
    def __init__(self, voltage: float = 7.8, current_a: float = 1.2) -> None:
        self.reading = PowerReading(voltage, current_a)

    def read(self) -> PowerReading:
        return self.reading


class Ina219(PowerMonitor):
    """32 V, 3.2 A range with the common 0.1 ohm shunt: current LSB 0.1 mA, cal = 4096."""

    def __init__(self, bus: I2cBus, address: int = 0x41, shunt_ohms: float = 0.1) -> None:
        self.bus = bus
        self.address = address
        self.current_lsb = 0.0001  # A per bit
        cal = int(0.04096 / (self.current_lsb * shunt_ohms))
        self._write16(REG_CAL, cal)
        # 32 V range, gain /8 (320 mV), 12-bit, continuous shunt+bus
        self._write16(REG_CONFIG, 0x399F)

    def _write16(self, reg: int, value: int) -> None:
        self.bus.write_i2c_block_data(self.address, reg, [(value >> 8) & 0xFF, value & 0xFF])

    def _read16(self, reg: int) -> int:
        hi, lo = self.bus.read_i2c_block_data(self.address, reg, 2)
        return (hi << 8) | lo

    def read(self) -> PowerReading:
        raw_bus = self._read16(REG_BUS)
        voltage = (raw_bus >> 3) * 0.004
        raw_cur = self._read16(REG_CURRENT)
        if raw_cur & 0x8000:
            raw_cur -= 65536
        return PowerReading(voltage, raw_cur * self.current_lsb)


def li_ion_percent(voltage: float, cells: int = 2) -> float:
    """Rough open-circuit estimate for Li-ion; good enough for a face icon."""
    v = voltage / cells
    points = [(3.3, 0.0), (3.5, 10.0), (3.6, 25.0), (3.7, 50.0), (3.85, 75.0), (4.0, 90.0), (4.15, 100.0)]
    if v <= points[0][0]:
        return 0.0
    for (v0, p0), (v1, p1) in itertools.pairwise(points):
        if v <= v1:
            return p0 + (p1 - p0) * (v - v0) / (v1 - v0)
    return 100.0
