"""MPU6050 accelerometer/gyro over I2C, plus a mock. Used for pickup and tilt detection."""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass

from robot.hal.i2c.base import I2cBus

PWR_MGMT_1, ACCEL_XOUT_H, GYRO_XOUT_H, WHO_AM_I = 0x6B, 0x3B, 0x43, 0x75
ACCEL_SCALE = 16384.0  # LSB/g at +-2 g
GYRO_SCALE = 131.0  # LSB/(deg/s) at +-250 deg/s


@dataclass
class ImuReading:
    ax: float
    ay: float
    az: float
    gx: float = 0.0
    gy: float = 0.0
    gz: float = 0.0

    @property
    def magnitude_g(self) -> float:
        return math.sqrt(self.ax**2 + self.ay**2 + self.az**2)

    @property
    def tilt_deg(self) -> float:
        """Angle between the sensor's z axis and gravity."""
        m = self.magnitude_g
        if m == 0:
            return 0.0
        return math.degrees(math.acos(max(-1.0, min(1.0, self.az / m))))


class Imu(ABC):
    @abstractmethod
    def read(self) -> ImuReading: ...

    def close(self) -> None: ...


class MockImu(Imu):
    def __init__(self) -> None:
        self.reading = ImuReading(0.0, 0.0, 1.0)

    def read(self) -> ImuReading:
        return self.reading


class Mpu6050(Imu):
    def __init__(self, bus: I2cBus, address: int = 0x68) -> None:
        self.bus = bus
        self.address = address
        who = bus.read_byte_data(address, WHO_AM_I)
        if who not in (0x68, 0x69, 0x70, 0x71, 0x72, 0x73):  # MPU6050 and its MPU9250/6500 siblings
            raise OSError(f"unexpected WHO_AM_I {hex(who)} at {hex(address)}")
        bus.write_byte_data(address, PWR_MGMT_1, 0x01)  # wake, PLL with X gyro reference

    @staticmethod
    def _s16(hi: int, lo: int) -> int:
        v = (hi << 8) | lo
        return v - 65536 if v & 0x8000 else v

    def read(self) -> ImuReading:
        a = self.bus.read_i2c_block_data(self.address, ACCEL_XOUT_H, 6)
        g = self.bus.read_i2c_block_data(self.address, GYRO_XOUT_H, 6)
        return ImuReading(
            self._s16(a[0], a[1]) / ACCEL_SCALE,
            self._s16(a[2], a[3]) / ACCEL_SCALE,
            self._s16(a[4], a[5]) / ACCEL_SCALE,
            self._s16(g[0], g[1]) / GYRO_SCALE,
            self._s16(g[2], g[3]) / GYRO_SCALE,
            self._s16(g[4], g[5]) / GYRO_SCALE,
        )
