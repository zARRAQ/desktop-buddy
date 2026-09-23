"""Mock GPIO with a command log, used by the simulator and every actuator test."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from robot.hal.gpio.base import (
    DigitalInput,
    DigitalOutput,
    Encoder,
    GpioBackend,
    PwmOutput,
)


@dataclass
class LogEntry:
    t: float
    kind: str  # "out" | "pwm" | "enc_reset"
    pin: int
    value: float


@dataclass
class MockGpioBackend(GpioBackend):
    name: str = "mock"
    log: list[LogEntry] = field(default_factory=list)
    levels: dict[int, bool] = field(default_factory=dict)
    duties: dict[int, float] = field(default_factory=dict)
    inputs: dict[int, bool] = field(default_factory=dict)
    encoder_steps: dict[tuple[int, int], int] = field(default_factory=dict)
    frequencies: dict[int, float] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def _record(self, kind: str, pin: int, value: float) -> None:
        with self._lock:
            self.log.append(LogEntry(time.monotonic(), kind, pin, value))
            if len(self.log) > 100_000:
                del self.log[:50_000]

    def output(self, pin: int, *, initial: bool = False) -> DigitalOutput:
        self.levels[pin] = initial
        return _MockOut(self, pin)

    def input(self, pin: int, *, pull_up: bool | None = None) -> DigitalInput:
        self.inputs.setdefault(pin, bool(pull_up))
        return _MockIn(self, pin)

    def pwm(self, pin: int, frequency_hz: float, *, hardware: bool = False) -> PwmOutput:
        self.duties[pin] = 0.0
        self.frequencies[pin] = frequency_hz
        return _MockPwm(self, pin, frequency_hz, hardware)

    def encoder(self, pin_a: int, pin_b: int) -> Encoder:
        self.encoder_steps.setdefault((pin_a, pin_b), 0)
        return _MockEncoder(self, pin_a, pin_b)

    # -- test helpers ------------------------------------------------------------------
    def entries(self, pin: int, kind: str | None = None) -> list[LogEntry]:
        with self._lock:
            return [e for e in self.log if e.pin == pin and (kind is None or e.kind == kind)]

    def clear_log(self) -> None:
        with self._lock:
            self.log.clear()

    def simulate_encoder(self, pin_a: int, pin_b: int, delta: int) -> None:
        with self._lock:
            self.encoder_steps[(pin_a, pin_b)] = self.encoder_steps.get((pin_a, pin_b), 0) + delta


class _MockOut(DigitalOutput):
    def __init__(self, backend: MockGpioBackend, pin: int) -> None:
        self.backend = backend
        self.pin = pin

    def write(self, high: bool) -> None:
        self.backend.levels[self.pin] = high
        self.backend._record("out", self.pin, 1.0 if high else 0.0)

    def close(self) -> None:
        pass


class _MockIn(DigitalInput):
    def __init__(self, backend: MockGpioBackend, pin: int) -> None:
        self.backend = backend
        self.pin = pin

    def read(self) -> bool:
        return self.backend.inputs.get(self.pin, False)

    def close(self) -> None:
        pass


class _MockPwm(PwmOutput):
    def __init__(self, backend: MockGpioBackend, pin: int, frequency_hz: float, hardware: bool) -> None:
        self.backend = backend
        self.pin = pin
        self.frequency_hz = frequency_hz
        self.hardware = hardware

    def set_duty(self, duty: float) -> None:
        duty = min(1.0, max(0.0, duty))
        self.backend.duties[self.pin] = duty
        self.backend._record("pwm", self.pin, duty)

    def close(self) -> None:
        self.set_duty(0.0)


class _MockEncoder(Encoder):
    def __init__(self, backend: MockGpioBackend, pin_a: int, pin_b: int) -> None:
        self.backend = backend
        self.pin_a = pin_a
        self.pin_b = pin_b

    @property
    def steps(self) -> int:
        return self.backend.encoder_steps.get((self.pin_a, self.pin_b), 0)

    def reset(self) -> None:
        self.backend.encoder_steps[(self.pin_a, self.pin_b)] = 0
        self.backend._record("enc_reset", self.pin_a, 0.0)

    def close(self) -> None:
        pass
