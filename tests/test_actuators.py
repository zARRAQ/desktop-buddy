from __future__ import annotations

import math

import pytest

from robot.core.config import ChannelConfig, RobotConfig
from robot.hal.actuators.dc import MockHBridge, Tb6612fng
from robot.hal.actuators.drive import DifferentialDrive, Wheel
from robot.hal.actuators.map import ActuatorMap
from robot.hal.actuators.pca9685 import LED0_ON_L, MockServoDriver, Pca9685
from robot.hal.actuators.servo import ServoChannel
from robot.hal.gpio.mock import MockGpioBackend
from robot.hal.i2c.base import MockI2cBus


def servo_cfg(**kw) -> ChannelConfig:
    base = dict(role="pan", driver="servo", channel=0, min_deg=-70, max_deg=70, max_deg_s=180, relax_after_s=3.0)
    base.update(kw)
    return ChannelConfig(**base)


def test_servo_clamp_is_enforced_in_hal():
    drv = MockServoDriver()
    s = ServoChannel("pan", servo_cfg(), drv)
    assert s.set_target(500) == 70
    assert s.set_target(-500) == -70
    assert s.clamps == 2
    for _ in range(200):
        s.update(0.02)
    pulses = [p for _, ch, p in drv.log if ch == 0 and p > 0]
    assert pulses[-1] == pytest.approx(1500 - 70 * 10.0)  # settled exactly at the limit
    assert min(pulses) >= 800 - 1e-6 and max(pulses) <= 2200 + 1e-6  # never beyond it
    assert s.relaxed and drv.pulses[0] == 0.0  # and relaxed once idle


def test_servo_slew_limit_140_degrees_takes_at_least_140_over_rate():
    drv = MockServoDriver()
    s = ServoChannel("pan", servo_cfg(max_deg_s=180), drv)
    s.set_target(-70)
    for _ in range(1000):
        s.update(0.01)
    s.set_target(70)
    t = 0.0
    while s.moving:
        s.update(0.005)
        t += 0.005
    assert t >= 140 / 180 - 1e-9
    assert t < 140 / 180 + 0.05


def test_servo_relaxes_after_idle_and_wakes_on_command():
    drv = MockServoDriver()
    s = ServoChannel("tilt", servo_cfg(role="tilt", channel=1, relax_after_s=3.0), drv)
    s.set_target(10)
    for _ in range(100):
        s.update(0.01)  # 1 s: reaches target
    assert not s.relaxed
    n_before = len(drv.log)
    for _ in range(250):
        s.update(0.01)  # 2.5 s more idle -> relaxed at 3 s
    assert s.relaxed
    assert drv.pulses[1] == 0.0
    assert len(drv.log) == n_before + 1  # exactly one 'off' command, no pulses while idle
    s.set_target(12)
    s.update(0.01)
    assert not s.relaxed and drv.pulses[1] > 0


def test_pca9685_registers():
    bus = MockI2cBus(present=[0x40])
    p = Pca9685(bus, 0x40, 50.0)
    assert bus.regs[0x40][0xFE] == 121  # prescale for 50 Hz
    p.set_pulse_us(3, 1500)
    reg = LED0_ON_L + 12
    off = bus.regs[0x40][reg + 2] | (bus.regs[0x40][reg + 3] << 8)
    assert off == pytest.approx(1500 / 20000 * 4096, abs=1)
    p.set_pulse_us(3, 0)
    assert bus.regs[0x40][reg + 3] & 0x10  # full-off bit
    with pytest.raises(OSError):
        Pca9685(MockI2cBus(present=[]), 0x40)


def test_tb6612fng_direction_and_duty():
    gpio = MockGpioBackend()
    pins = {"pwma": 12, "ain1": 5, "ain2": 6, "pwmb": 13, "bin1": 14, "bin2": 15, "standby": 26}
    br = Tb6612fng(gpio, pins, 20000)
    br.set_speed("a", 0.5)
    assert gpio.levels[5] is True and gpio.levels[6] is False and gpio.duties[12] == 0.5
    br.set_speed("a", -0.25)
    assert gpio.levels[5] is False and gpio.levels[6] is True and gpio.duties[12] == 0.25
    br.set_speed("b", 2.0)
    assert gpio.duties[13] == 1.0
    br.stop()
    assert gpio.duties[12] == 0 and gpio.duties[13] == 0
    assert 26 not in gpio.levels  # the bridge never touches the safety line
    assert gpio.frequencies[12] == 20000


def test_invert_reverses_wheel_without_code_change():
    cfg_l = ChannelConfig(role="drive", driver="dc", motor="a", invert=False)
    cfg_r = ChannelConfig(role="drive", driver="dc", motor="b", invert=True)
    br = MockHBridge()
    left, right = Wheel("l", cfg_l, br, None), Wheel("r", cfg_r, br, None)
    left.set(0.5)
    right.set(0.5)
    assert br.speeds == {"a": 0.5, "b": -0.5}


def test_differential_drive_kinematics_and_odometry(config: RobotConfig):
    br = MockHBridge()
    cfg = config.actuators
    left = Wheel("left", cfg.channels["drive_left"], br, None)
    right = Wheel("right", cfg.channels["drive_right"], br, None)
    d = DifferentialDrive(left, right, cfg.geometry, max_speed_mm_s=250)
    d.set_velocity(250, 0)
    assert left.command == pytest.approx(1.0) and right.command == pytest.approx(1.0)
    for _ in range(100):
        d.update(0.01)
    assert d.pose.x_mm == pytest.approx(250, rel=0.02) and abs(d.pose.y_mm) < 1e-6
    d.reset_pose()
    d.set_velocity(0, 90)  # spin in place
    assert left.command == pytest.approx(-right.command)
    for _ in range(100):
        d.update(0.01)
    assert d.pose.theta_deg == pytest.approx(90, abs=3)
    # over-speed request is scaled, never clipped asymmetrically
    d.set_velocity(1000, 360)
    assert max(abs(left.command), abs(right.command)) == pytest.approx(1.0)
    assert left.command < right.command


def test_encoder_odometry(config: RobotConfig):
    gpio = MockGpioBackend()
    cfg = config.actuators
    br = MockHBridge()
    enc_l = gpio.encoder(17, 27)
    enc_r = gpio.encoder(22, 23)
    left = Wheel("left", cfg.channels["drive_left"], br, enc_l)
    right = Wheel("right", cfg.channels["drive_right"], br, enc_r)
    d = DifferentialDrive(left, right, cfg.geometry)
    one_rev = 700
    gpio.simulate_encoder(17, 27, one_rev)
    gpio.simulate_encoder(22, 23, -one_rev)  # right is inverted in the default map
    d.update(0.1)
    assert d.pose.x_mm == pytest.approx(math.pi * 40, rel=1e-3)


@pytest.mark.parametrize(
    ("layout_file", "expected_prims"),
    [
        ("A", {"center", "drive", "look_at", "nod", "relax", "shake", "stop", "turn"}),
        ("B", {"center", "drive", "lift", "look_at", "nod", "relax", "shake", "stop", "turn"}),
        ("C", {"center", "look_at", "nod", "relax", "shake", "stop", "wave"}),
    ],
)
def test_layouts_load_and_report_primitives(layout_file, expected_prims, tmp_path):
    import yaml

    from robot.core.config import RobotConfig

    layouts = yaml.safe_load(open("config/hardware.example.yaml"))  # noqa: SIM115, PTH123
    cfg = RobotConfig.model_validate({"actuators": layouts["layouts"][layout_file]})
    m = ActuatorMap.from_config(cfg.actuators, MockGpioBackend(), MockI2cBus(present=[0x40]), mock=True)
    assert set(m.primitives()) == expected_prims
    if layout_file == "C":
        assert m.drive is None


def test_default_config_layout_a(config: RobotConfig):
    m = ActuatorMap.from_config(config.actuators, MockGpioBackend(), None, mock=True)
    assert m.roles() == ["drive", "pan", "tilt"]
    m.servos["pan"].set_target(30)
    m.update(0.1)
    m.close()
