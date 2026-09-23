"""Hardware PWM through the Linux sysfs PWM interface (``/sys/class/pwm``).

On a Raspberry Pi 5 the RP1 exposes four channels: GPIO12 (ch 0), GPIO13 (ch 1), GPIO18
(ch 2), GPIO19 (ch 3). Enable them in ``/boot/firmware/config.txt``::

    dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4

GPIO18/19 are used for I2S audio in this robot, so only 12 and 13 are available, which is
why the TB6612FNG (one PWM pin per motor) is the recommended H-bridge.

This is a deliberately small re-implementation of the sysfs protocol rather than a
dependency on ``rpi-hardware-pwm`` (GPL-3). It is MIT like the rest of the project.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from robot.hal.gpio.base import PwmOutput

log = logging.getLogger(__name__)

PI5_CHANNELS: dict[int, int] = {12: 0, 13: 1, 18: 2, 19: 3}


def find_pwmchip(root: Path = Path("/sys/class/pwm")) -> Path | None:
    """Pick the chip that belongs to the RP1 PWM block (4 channels). Kernel numbering moved
    between releases (pwmchip2 on 6.6, pwmchip0 on some 6.12 builds), so probe by npwm."""
    if not root.exists():
        return None
    chips = sorted(root.glob("pwmchip*"))
    for chip in chips:
        try:
            npwm = int((chip / "npwm").read_text().strip())
        except (OSError, ValueError):
            continue
        if npwm >= 4:
            return chip
    return chips[0] if chips else None


class SysfsPwm(PwmOutput):
    hardware = True

    def __init__(self, pin: int, frequency_hz: float, *, chip: Path | None = None) -> None:
        if pin not in PI5_CHANNELS:
            raise ValueError(f"GPIO{pin} has no hardware PWM on a Pi 5 (use 12, 13, 18 or 19)")
        self.pin = pin
        self.frequency_hz = frequency_hz
        self.channel = PI5_CHANNELS[pin]
        chip_path = chip or find_pwmchip()
        if chip_path is None:
            raise RuntimeError("no /sys/class/pwm chip; add the pwm-2chan overlay to config.txt")
        self.chip = chip_path
        self.path = self.chip / f"pwm{self.channel}"
        self._period_ns = int(1e9 / frequency_hz)
        self._export()
        self._write("period", self._period_ns)
        self._write("duty_cycle", 0)
        self._write("enable", 1)

    def _export(self) -> None:
        if self.path.exists():
            return
        (self.chip / "export").write_text(str(self.channel))
        for _ in range(50):  # udev takes a moment to make the files writable
            if (self.path / "enable").exists():
                break
            time.sleep(0.01)

    def _write(self, name: str, value: int) -> None:
        (self.path / name).write_text(str(value))

    def set_duty(self, duty: float) -> None:
        duty = min(1.0, max(0.0, duty))
        self._write("duty_cycle", int(self._period_ns * duty))

    def close(self) -> None:
        try:
            self._write("duty_cycle", 0)
            self._write("enable", 0)
            (self.chip / "unexport").write_text(str(self.channel))
        except OSError as exc:
            log.debug("pwm close: %s", exc)
