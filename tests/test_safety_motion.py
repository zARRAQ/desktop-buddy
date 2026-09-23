from __future__ import annotations

import time

import pytest

from robot.core.bus import LocalHub
from robot.core.config import RobotConfig, SafetyConfig
from robot.core.messages import MotionCommand
from robot.core.service import ServiceThread
from robot.hal.actuators.map import ActuatorMap
from robot.hal.gpio.mock import MockGpioBackend
from robot.hal.sensors.cliff import MockCliffSensors
from robot.hal.sensors.imu import ImuReading, MockImu
from robot.motion.service import MotionService
from robot.safety.service import SafetyService
from robot.safety.supervisor import EnableLine, SafetyLogic, Supervisor


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def test_logic_requires_fresh_motion_heartbeat():
    clk = Clock()
    cfg = SafetyConfig(startup_grace_s=2.0, motion_heartbeat_timeout_ms=500)
    logic = SafetyLogic(cfg, clock=clk)
    d = logic.evaluate(None, None, None)
    assert not d.enabled and "waiting" in d.reason
    clk.t += 3
    assert "not running" in logic.evaluate(None, None, None).reason
    logic.motion_heartbeat()
    assert logic.evaluate(None, None, None).enabled
    clk.t += 0.6
    d = logic.evaluate(None, None, None)
    assert not d.enabled and "heartbeat lost" in d.reason


def test_logic_cliff_latches_until_all_sensors_see_floor():
    clk = Clock()
    logic = SafetyLogic(SafetyConfig(), clock=clk)
    logic.motion_heartbeat()
    assert logic.evaluate({"front": 30, "left": 28, "right": 31}, None, None).enabled
    d = logic.evaluate({"front": 8190, "left": 28, "right": 31}, None, None)
    assert not d.enabled and d.reason == "cliff: front"
    # sensor sees floor again on the very next sample: released
    assert logic.evaluate({"front": 30, "left": 28, "right": 31}, None, None).enabled


def test_logic_imu_pickup_tilt_and_estop():
    clk = Clock()
    logic = SafetyLogic(SafetyConfig(), clock=clk)
    logic.motion_heartbeat()
    assert logic.evaluate({}, 5.0, 1.0).enabled
    assert logic.evaluate({}, 5.0, 1.6).picked_up
    assert "tilted" in logic.evaluate({}, 50.0, 1.0).reason
    logic.estop("button")
    assert logic.evaluate({}, 0.0, 1.0).reason == "estop: button"
    logic.reset()
    assert logic.evaluate({}, 0.0, 1.0).enabled


def test_enable_line_level_and_pulse_modes():
    gpio = MockGpioBackend()
    line = EnableLine(gpio.output(26), mode="level")
    assert gpio.levels[26] is False
    line.set_allowed(True)
    assert gpio.levels[26] is True
    line.set_allowed(False)
    assert gpio.levels[26] is False
    line.close()

    pulse = EnableLine(gpio.output(26), mode="pulse", heartbeat_hz=100)
    time.sleep(0.1)
    n0 = len(gpio.entries(26, "out"))
    pulse.set_allowed(True)
    time.sleep(0.2)
    toggles = len(gpio.entries(26, "out")) - n0
    assert toggles >= 10  # a square wave while allowed
    pulse.set_allowed(False)
    time.sleep(0.05)
    n1 = len(gpio.entries(26, "out"))
    time.sleep(0.1)
    assert len(gpio.entries(26, "out")) == n1  # silent when not allowed
    assert gpio.levels[26] is False
    pulse.close()


def test_supervisor_drives_line_from_sensors():
    clk = Clock()
    gpio = MockGpioBackend()
    cfg = SafetyConfig()
    cliff = MockCliffSensors(["front", "left", "right"])
    imu = MockImu()
    sup = Supervisor(cfg, EnableLine(gpio.output(26)), cliff=cliff, imu=imu, clock=clk)
    sup.logic.motion_heartbeat()
    assert sup.step().enabled and gpio.levels[26] is True
    cliff.set_off_edge("left")
    assert not sup.step().enabled and gpio.levels[26] is False
    cliff.set_off_edge("left", False)
    imu.reading = ImuReading(0.0, 0.0, 1.9)
    assert sup.step().picked_up
    imu.reading = ImuReading(0.0, 0.0, 1.0)
    assert sup.step().enabled
    sup.close()
    assert gpio.levels[26] is False


def _wait(pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return False


@pytest.mark.timeout(60)
def test_motion_and_safety_services_interlock(config: RobotConfig, hub: LocalHub):
    gpio = MockGpioBackend()
    cliff = MockCliffSensors(["front", "left", "right"])
    safety = SafetyService(config, hub.client("safety"), gpio=gpio, cliff=cliff, imu=MockImu())
    motion = MotionService(
        config, hub.client("motion"), actuators=ActuatorMap.from_config(config.actuators, gpio, None, mock=True)
    )
    ts, tm = ServiceThread(safety), ServiceThread(motion)
    probe = hub.client("probe")
    probe.subscribe("safety.", "motion.")
    ts.start()
    tm.start()
    try:
        # both alive -> enabled
        assert _wait(lambda: gpio.levels.get(26) is True, timeout=10), "enable line never went high"
        # drive command executes while enabled
        probe.publish_payload(MotionCommand(type="drive", speed_mm_s=100, duration_s=5))
        assert _wait(
            lambda: (
                motion.actuators is not None
                and motion.actuators.drive is not None
                and motion.actuators.drive.left.command > 0
            )
        )
        # cliff -> line low within a few polls and the drive halts
        cliff.set_off_edge("front")
        assert _wait(lambda: gpio.levels.get(26) is False, timeout=3)
        assert _wait(lambda: motion.actuators.drive.left.command == 0, timeout=3)
        # drive refused while disabled
        before = motion.refused
        probe.publish_payload(MotionCommand(type="drive", speed_mm_s=100, duration_s=1))
        assert _wait(lambda: motion.refused > before, timeout=3)
        cliff.set_off_edge("front", False)
        assert _wait(lambda: gpio.levels.get(26) is True, timeout=3)
        # killing the supervisor drops the line and motion stops trusting stale state
        ts.stop()
        assert gpio.levels[26] is False
        time.sleep(1.2)
        assert motion.drive_allowed is False
        before = motion.refused
        probe.publish_payload(MotionCommand(type="drive", speed_mm_s=100, duration_s=1))
        assert _wait(lambda: motion.refused > before, timeout=3)
    finally:
        ts.stop()
        tm.stop()
    assert ts.error is None and tm.error is None


def test_motion_gestures_and_layout_gating(config: RobotConfig, hub: LocalHub):
    gpio = MockGpioBackend()
    m = ActuatorMap.from_config(config.actuators, gpio, None, mock=True)
    motion = MotionService(config, hub.client("motion"), actuators=m)
    motion.setup()
    motion.handle(MotionCommand(type="look_at", yaw_deg=40, pitch_deg=-10))
    for _ in range(60):
        motion.tick(0.02)
    assert m.servos["pan"].position == pytest.approx(40, abs=1)
    assert m.servos["tilt"].position == pytest.approx(-10, abs=1)
    motion.handle(MotionCommand(type="nod"))
    peak = 0.0
    for _ in range(80):
        motion.tick(0.02)
        peak = max(peak, m.servos["tilt"].target)
    assert peak > 10
    # a drive request without safety is refused and logged, not executed
    motion.handle(MotionCommand(type="drive", speed_mm_s=100))
    assert motion.refused == 1
    assert m.drive is not None and m.drive.left.command == 0
    motion.teardown()
