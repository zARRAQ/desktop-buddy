"""Protocols for pins. Implementations: :mod:`.mock`, :mod:`.gpiozero_backend`."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

log = logging.getLogger(__name__)


class DigitalOutput(ABC):
    pin: int

    @abstractmethod
    def write(self, high: bool) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    def on(self) -> None:
        self.write(True)

    def off(self) -> None:
        self.write(False)


class DigitalInput(ABC):
    pin: int

    @abstractmethod
    def read(self) -> bool: ...

    @abstractmethod
    def close(self) -> None: ...


class PwmOutput(ABC):
    """Duty cycle in [0, 1]. Frequency fixed at construction."""

    pin: int
    frequency_hz: float
    hardware: bool = False

    @abstractmethod
    def set_duty(self, duty: float) -> None: ...

    @abstractmethod
    def close(self) -> None: ...


class Encoder(ABC):
    """Quadrature encoder. ``steps`` is signed and monotonic until :meth:`reset`."""

    pin_a: int
    pin_b: int

    @property
    @abstractmethod
    def steps(self) -> int: ...

    @abstractmethod
    def reset(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...


class GpioBackend(ABC):
    name: str = "abstract"

    @abstractmethod
    def output(self, pin: int, *, initial: bool = False) -> DigitalOutput: ...

    @abstractmethod
    def input(self, pin: int, *, pull_up: bool | None = None) -> DigitalInput: ...

    @abstractmethod
    def pwm(self, pin: int, frequency_hz: float, *, hardware: bool = False) -> PwmOutput: ...

    @abstractmethod
    def encoder(self, pin_a: int, pin_b: int) -> Encoder: ...

    def close(self) -> None: ...


def open_gpio_backend(prefer: str = "auto") -> GpioBackend:
    """``auto`` picks gpiozero/lgpio on a Pi and the mock everywhere else."""
    if prefer in ("auto", "gpiozero"):
        try:
            from robot.hal.gpio.gpiozero_backend import GpiozeroBackend

            return GpiozeroBackend()
        except Exception as exc:
            if prefer == "gpiozero":
                raise
            log.info("no GPIO hardware available (%s); using mock backend", exc)
    from robot.hal.gpio.mock import MockGpioBackend

    return MockGpioBackend()
