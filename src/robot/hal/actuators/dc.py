"""H-bridges. Speed is -1..1. The standby/enable line is NOT driven here: the safety
supervisor owns it. Motion can ask for speed all day; without the enable line nothing moves."""

from __future__ import annotations

from abc import ABC, abstractmethod

from robot.hal.gpio.base import DigitalOutput, GpioBackend, PwmOutput


class HBridge(ABC):
    @abstractmethod
    def set_speed(self, motor: str, speed: float) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...


class Tb6612fng(HBridge):
    """PWMA/PWMB carry the duty (hardware PWM on GPIO12/13), IN1/IN2 select direction."""

    def __init__(
        self, gpio: GpioBackend, pins: dict[str, int], pwm_hz: int = 20_000, *, hardware_pwm: bool = True
    ) -> None:
        self._pwm: dict[str, PwmOutput] = {
            "a": gpio.pwm(pins["pwma"], pwm_hz, hardware=hardware_pwm),
            "b": gpio.pwm(pins["pwmb"], pwm_hz, hardware=hardware_pwm),
        }
        self._in: dict[str, tuple[DigitalOutput, DigitalOutput]] = {
            "a": (gpio.output(pins["ain1"]), gpio.output(pins["ain2"])),
            "b": (gpio.output(pins["bin1"]), gpio.output(pins["bin2"])),
        }
        self.stop()

    def set_speed(self, motor: str, speed: float) -> None:
        speed = max(-1.0, min(1.0, speed))
        in1, in2 = self._in[motor]
        if speed > 0:
            in1.write(True)
            in2.write(False)
        elif speed < 0:
            in1.write(False)
            in2.write(True)
        else:
            in1.write(False)
            in2.write(False)  # coast
        self._pwm[motor].set_duty(abs(speed))

    def brake(self, motor: str) -> None:
        in1, in2 = self._in[motor]
        in1.write(True)
        in2.write(True)
        self._pwm[motor].set_duty(1.0)

    def stop(self) -> None:
        for m in ("a", "b"):
            self.set_speed(m, 0.0)

    def close(self) -> None:
        self.stop()
        for pwm in self._pwm.values():
            pwm.close()
        for a, b in self._in.values():
            a.close()
            b.close()


class Drv8833(HBridge):
    """Needs PWM on all four inputs (forward: IN1=PWM, IN2=0). On a Pi 5 with I2S in use that
    means software PWM at ~800 Hz: audible and jittery. Supported; see docs for why TB6612FNG
    is the default."""

    def __init__(
        self, gpio: GpioBackend, pins: dict[str, int], pwm_hz: int = 20_000, *, hardware_pwm: bool = False
    ) -> None:
        self._pins: dict[str, tuple[PwmOutput, PwmOutput]] = {
            "a": (
                gpio.pwm(pins["ain1"], pwm_hz, hardware=hardware_pwm),
                gpio.pwm(pins["ain2"], pwm_hz, hardware=hardware_pwm),
            ),
            "b": (
                gpio.pwm(pins["bin1"], pwm_hz, hardware=hardware_pwm),
                gpio.pwm(pins["bin2"], pwm_hz, hardware=hardware_pwm),
            ),
        }
        self.stop()

    def set_speed(self, motor: str, speed: float) -> None:
        speed = max(-1.0, min(1.0, speed))
        in1, in2 = self._pins[motor]
        if speed >= 0:
            in1.set_duty(speed)
            in2.set_duty(0.0)
        else:
            in1.set_duty(0.0)
            in2.set_duty(-speed)

    def stop(self) -> None:
        for m in ("a", "b"):
            self.set_speed(m, 0.0)

    def close(self) -> None:
        self.stop()
        for a, b in self._pins.values():
            a.close()
            b.close()


class MockHBridge(HBridge):
    def __init__(self) -> None:
        self.speeds: dict[str, float] = {"a": 0.0, "b": 0.0}
        self.log: list[tuple[str, float]] = []

    def set_speed(self, motor: str, speed: float) -> None:
        self.speeds[motor] = max(-1.0, min(1.0, speed))
        self.log.append((motor, self.speeds[motor]))

    def stop(self) -> None:
        for m in ("a", "b"):
            self.set_speed(m, 0.0)

    def close(self) -> None:
        self.stop()
