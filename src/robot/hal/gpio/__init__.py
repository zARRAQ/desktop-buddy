"""GPIO, PWM and quadrature encoder access behind small protocols."""

from robot.hal.gpio.base import (
    DigitalInput,
    DigitalOutput,
    Encoder,
    GpioBackend,
    PwmOutput,
    open_gpio_backend,
)
from robot.hal.gpio.mock import MockGpioBackend

__all__ = [
    "DigitalInput",
    "DigitalOutput",
    "Encoder",
    "GpioBackend",
    "MockGpioBackend",
    "PwmOutput",
    "open_gpio_backend",
]
