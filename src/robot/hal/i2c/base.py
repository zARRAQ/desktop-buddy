"""I2C protocol matching the subset of smbus2 that the drivers use."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Sequence

log = logging.getLogger(__name__)


class I2cBus(ABC):
    @abstractmethod
    def write_byte(self, addr: int, value: int) -> None: ...

    @abstractmethod
    def read_byte(self, addr: int) -> int: ...

    @abstractmethod
    def write_byte_data(self, addr: int, reg: int, value: int) -> None: ...

    @abstractmethod
    def read_byte_data(self, addr: int, reg: int) -> int: ...

    @abstractmethod
    def write_i2c_block_data(self, addr: int, reg: int, data: Sequence[int]) -> None: ...

    @abstractmethod
    def read_i2c_block_data(self, addr: int, reg: int, length: int) -> list[int]: ...

    def read_word_data(self, addr: int, reg: int) -> int:
        lo, hi = self.read_i2c_block_data(addr, reg, 2)
        return lo | (hi << 8)

    def scan(self, addresses: Sequence[int] | None = None) -> list[int]:
        found: list[int] = []
        for a in addresses if addresses is not None else range(0x03, 0x78):
            try:
                self.read_byte(a)
                found.append(a)
            except OSError:
                continue
        return found

    def close(self) -> None: ...


class MockI2cBus(I2cBus):
    """Registers per device; unknown devices raise OSError like a real bus with nothing there."""

    def __init__(self, present: Sequence[int] = ()) -> None:
        self.present: set[int] = set(present)
        self.regs: dict[int, dict[int, int]] = defaultdict(dict)
        self.log: list[tuple[str, int, int, list[int]]] = []

    def _check(self, addr: int) -> None:
        if addr not in self.present:
            raise OSError(121, f"no device at {hex(addr)}")

    def write_byte(self, addr: int, value: int) -> None:
        self._check(addr)
        self.log.append(("wb", addr, -1, [value]))

    def read_byte(self, addr: int) -> int:
        self._check(addr)
        return 0

    def write_byte_data(self, addr: int, reg: int, value: int) -> None:
        self._check(addr)
        self.regs[addr][reg] = value & 0xFF
        self.log.append(("wbd", addr, reg, [value & 0xFF]))

    def read_byte_data(self, addr: int, reg: int) -> int:
        self._check(addr)
        return self.regs[addr].get(reg, 0)

    def write_i2c_block_data(self, addr: int, reg: int, data: Sequence[int]) -> None:
        self._check(addr)
        for i, v in enumerate(data):
            self.regs[addr][reg + i] = v & 0xFF
        self.log.append(("wblk", addr, reg, [v & 0xFF for v in data]))

    def read_i2c_block_data(self, addr: int, reg: int, length: int) -> list[int]:
        self._check(addr)
        return [self.regs[addr].get(reg + i, 0) for i in range(length)]


class SmbusI2c(I2cBus):
    def __init__(self, bus: int = 1) -> None:
        from smbus2 import SMBus

        self._bus = SMBus(bus)

    def write_byte(self, addr: int, value: int) -> None:
        self._bus.write_byte(addr, value)

    def read_byte(self, addr: int) -> int:
        return int(self._bus.read_byte(addr))

    def write_byte_data(self, addr: int, reg: int, value: int) -> None:
        self._bus.write_byte_data(addr, reg, value)

    def read_byte_data(self, addr: int, reg: int) -> int:
        return int(self._bus.read_byte_data(addr, reg))

    def write_i2c_block_data(self, addr: int, reg: int, data: Sequence[int]) -> None:
        self._bus.write_i2c_block_data(addr, reg, list(data))

    def read_i2c_block_data(self, addr: int, reg: int, length: int) -> list[int]:
        return [int(v) for v in self._bus.read_i2c_block_data(addr, reg, length)]

    def close(self) -> None:
        self._bus.close()


def open_i2c_bus(bus: int = 1, prefer: str = "auto") -> I2cBus:
    if prefer in ("auto", "smbus"):
        try:
            return SmbusI2c(bus)
        except Exception as exc:
            if prefer == "smbus":
                raise
            log.info("no I2C bus %d available (%s); using mock", bus, exc)
    return MockI2cBus()
