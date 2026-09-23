"""I2C bus access behind a small protocol, with a register-level mock."""

from robot.hal.i2c.base import I2cBus, MockI2cBus, open_i2c_bus

__all__ = ["I2cBus", "MockI2cBus", "open_i2c_bus"]
