"""gpiozero (lgpio pin factory) backend for the Raspberry Pi 5.

Hardware PWM goes through sysfs (:mod:`.sysfs_pwm`); gpiozero's own PWM is software timed
and tops out near 800 Hz on a Pi 5, fine for an LED, not for a motor.

Not exercised on real hardware yet; the API calls follow the gpiozero 2.x documentation.
"""

from __future__ import annotations

import logging
from typing import Any

from robot.hal.gpio.base import (
    DigitalInput,
    DigitalOutput,
    Encoder,
    GpioBackend,
    PwmOutput,
)
from robot.hal.gpio.sysfs_pwm import PI5_CHANNELS, SysfsPwm

log = logging.getLogger(__name__)


class GpiozeroBackend(GpioBackend):
    name = "gpiozero"

    def __init__(self) -> None:
        import gpiozero  # noqa: F401 - probe that the library and a pin factory exist
        from gpiozero import Device

        factory = Device._default_pin_factory()  # raises if no supported chip is present
        self._factory_name = type(factory).__name__
        log.info("gpiozero pin factory: %s", self._factory_name)

    def output(self, pin: int, *, initial: bool = False) -> DigitalOutput:
        from gpiozero import DigitalOutputDevice

        return _Out(pin, DigitalOutputDevice(pin, initial_value=initial))

    def input(self, pin: int, *, pull_up: bool | None = None) -> DigitalInput:
        from gpiozero import DigitalInputDevice

        return _In(pin, DigitalInputDevice(pin, pull_up=pull_up))

    def pwm(self, pin: int, frequency_hz: float, *, hardware: bool = False) -> PwmOutput:
        if hardware and pin in PI5_CHANNELS:
            try:
                return SysfsPwm(pin, frequency_hz)
            except (OSError, RuntimeError) as exc:
                log.warning("hardware PWM on GPIO%d unavailable (%s); using software PWM", pin, exc)
        from gpiozero import PWMOutputDevice

        freq = min(frequency_hz, 1000.0)
        if freq != frequency_hz:
            log.warning("software PWM on GPIO%d limited to %.0f Hz (asked %.0f)", pin, freq, frequency_hz)
        return _SoftPwm(pin, PWMOutputDevice(pin, frequency=int(freq)), freq)

    def encoder(self, pin_a: int, pin_b: int) -> Encoder:
        from gpiozero import RotaryEncoder

        return _Enc(pin_a, pin_b, RotaryEncoder(pin_a, pin_b, max_steps=0, wrap=False))


class _Out(DigitalOutput):
    def __init__(self, pin: int, dev: Any) -> None:
        self.pin = pin
        self._dev = dev

    def write(self, high: bool) -> None:
        if high:
            self._dev.on()
        else:
            self._dev.off()

    def close(self) -> None:
        self._dev.close()


class _In(DigitalInput):
    def __init__(self, pin: int, dev: Any) -> None:
        self.pin = pin
        self._dev = dev

    def read(self) -> bool:
        return bool(self._dev.value)

    def close(self) -> None:
        self._dev.close()


class _SoftPwm(PwmOutput):
    hardware = False

    def __init__(self, pin: int, dev: Any, frequency_hz: float) -> None:
        self.pin = pin
        self._dev = dev
        self.frequency_hz = frequency_hz

    def set_duty(self, duty: float) -> None:
        self._dev.value = min(1.0, max(0.0, duty))

    def close(self) -> None:
        self._dev.close()


class _Enc(Encoder):
    def __init__(self, pin_a: int, pin_b: int, dev: Any) -> None:
        self.pin_a = pin_a
        self.pin_b = pin_b
        self._dev = dev

    @property
    def steps(self) -> int:
        return int(self._dev.steps)

    def reset(self) -> None:
        self._dev.steps = 0

    def close(self) -> None:
        self._dev.close()
