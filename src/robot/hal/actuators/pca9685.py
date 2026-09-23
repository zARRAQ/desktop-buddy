"""PCA9685 16-channel PWM driver (servos) over I2C. Register map from the NXP datasheet."""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod

from robot.hal.i2c.base import I2cBus

log = logging.getLogger(__name__)

MODE1, MODE2, PRESCALE = 0x00, 0x01, 0xFE
LED0_ON_L = 0x06
ALL_LED_ON_L = 0xFA
MODE1_SLEEP, MODE1_AI, MODE1_RESTART = 0x10, 0x20, 0x80
MODE2_OUTDRV = 0x04
OSC_HZ = 25_000_000


class ServoDriver(ABC):
    @abstractmethod
    def set_pulse_us(self, channel: int, pulse_us: float) -> None:
        """0 turns the channel off (no pulses): the servo relaxes."""

    @abstractmethod
    def all_off(self) -> None: ...

    def close(self) -> None:
        self.all_off()


class Pca9685(ServoDriver):
    def __init__(self, bus: I2cBus, address: int = 0x40, frequency_hz: float = 50.0) -> None:
        self.bus = bus
        self.address = address
        self.frequency_hz = frequency_hz
        self.bus.write_byte_data(address, MODE2, MODE2_OUTDRV)
        self.bus.write_byte_data(address, MODE1, MODE1_AI)
        time.sleep(0.005)
        self.set_frequency(frequency_hz)
        self.all_off()

    def set_frequency(self, hz: float) -> None:
        prescale = max(3, min(255, round(OSC_HZ / (4096.0 * hz)) - 1))
        old = self.bus.read_byte_data(self.address, MODE1)
        self.bus.write_byte_data(self.address, MODE1, (old & 0x7F) | MODE1_SLEEP)
        self.bus.write_byte_data(self.address, PRESCALE, prescale)
        self.bus.write_byte_data(self.address, MODE1, old & ~MODE1_SLEEP & 0xFF)
        time.sleep(0.005)
        self.bus.write_byte_data(self.address, MODE1, (old | MODE1_RESTART | MODE1_AI) & 0xFF)
        self.frequency_hz = OSC_HZ / (4096.0 * (prescale + 1))

    def set_pwm(self, channel: int, on: int, off: int) -> None:
        if not 0 <= channel <= 15:
            raise ValueError("channel 0..15")
        reg = LED0_ON_L + 4 * channel
        self.bus.write_i2c_block_data(self.address, reg, [on & 0xFF, (on >> 8) & 0x1F, off & 0xFF, (off >> 8) & 0x1F])

    def set_pulse_us(self, channel: int, pulse_us: float) -> None:
        if pulse_us <= 0:
            self.set_pwm(channel, 0, 0x1000)  # full off bit
            return
        period_us = 1_000_000.0 / self.frequency_hz
        ticks = round(pulse_us / period_us * 4096.0)
        self.set_pwm(channel, 0, max(0, min(4095, ticks)))

    def all_off(self) -> None:
        self.bus.write_i2c_block_data(self.address, ALL_LED_ON_L, [0, 0, 0, 0x10])


class GpioServoDriver(ServoDriver):
    """Servos directly on GPIO software PWM. Jittery; supported, not recommended."""

    def __init__(self, gpio_pwm_factory: object, pins: dict[int, int], frequency_hz: float = 50.0) -> None:
        from robot.hal.gpio.base import GpioBackend

        assert isinstance(gpio_pwm_factory, GpioBackend)
        self.frequency_hz = frequency_hz
        self._pwms = {ch: gpio_pwm_factory.pwm(pin, frequency_hz) for ch, pin in pins.items()}

    def set_pulse_us(self, channel: int, pulse_us: float) -> None:
        pwm = self._pwms[channel]
        pwm.set_duty(0.0 if pulse_us <= 0 else pulse_us * self.frequency_hz / 1_000_000.0)

    def all_off(self) -> None:
        for pwm in self._pwms.values():
            pwm.set_duty(0.0)


class MockServoDriver(ServoDriver):
    def __init__(self) -> None:
        self.pulses: dict[int, float] = {}
        self.log: list[tuple[float, int, float]] = []

    def set_pulse_us(self, channel: int, pulse_us: float) -> None:
        self.pulses[channel] = pulse_us
        self.log.append((time.monotonic(), channel, pulse_us))

    def all_off(self) -> None:
        for ch in list(self.pulses):
            self.set_pulse_us(ch, 0.0)
