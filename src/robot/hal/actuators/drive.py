"""Differential drive: velocity commands in, wheel speeds out, odometry from encoders."""

from __future__ import annotations

import math
from dataclasses import dataclass

from robot.core.config import ChannelConfig, GeometryConfig
from robot.hal.actuators.dc import HBridge
from robot.hal.gpio.base import Encoder


@dataclass
class Pose:
    x_mm: float = 0.0
    y_mm: float = 0.0
    theta_deg: float = 0.0
    v_mm_s: float = 0.0
    w_deg_s: float = 0.0


class Wheel:
    def __init__(self, name: str, cfg: ChannelConfig, bridge: HBridge, encoder: Encoder | None) -> None:
        assert cfg.motor is not None
        self.name = name
        self.cfg = cfg
        self.bridge = bridge
        self.motor = cfg.motor
        self.encoder = encoder
        self._last_steps = encoder.steps if encoder else 0
        self.command = 0.0

    def set(self, speed: float) -> None:
        self.command = max(-1.0, min(1.0, speed))
        self.bridge.set_speed(self.motor, -self.command if self.cfg.invert else self.command)

    def delta_steps(self) -> int:
        if self.encoder is None:
            return 0
        now = self.encoder.steps
        d = now - self._last_steps
        self._last_steps = now
        return -d if self.cfg.invert else d


class DifferentialDrive:
    def __init__(self, left: Wheel, right: Wheel, geometry: GeometryConfig, *, max_speed_mm_s: float = 250.0) -> None:
        self.left = left
        self.right = right
        self.geometry = geometry
        self.max_speed = max_speed_mm_s
        self.pose = Pose()
        self.target_v = 0.0
        self.target_w = 0.0

    @property
    def ticks_per_mm(self) -> float:
        if self.geometry.ticks_per_mm:
            return self.geometry.ticks_per_mm
        tpr = self.left.cfg.encoder.ticks_per_rev if self.left.cfg.encoder else 700
        return tpr / (math.pi * self.geometry.wheel_diameter_mm)

    def set_velocity(self, v_mm_s: float, w_deg_s: float) -> None:
        """Body velocity: forward mm/s and yaw deg/s (positive = counter-clockwise)."""
        self.target_v = v_mm_s
        self.target_w = w_deg_s
        w_rad = math.radians(w_deg_s)
        half_track = self.geometry.track_width_mm / 2.0
        vl = v_mm_s - w_rad * half_track
        vr = v_mm_s + w_rad * half_track
        scale = max(1.0, abs(vl) / self.max_speed, abs(vr) / self.max_speed)
        self.left.set(vl / scale / self.max_speed)
        self.right.set(vr / scale / self.max_speed)

    def stop(self) -> None:
        self.set_velocity(0.0, 0.0)

    def update(self, dt: float) -> Pose:
        """Integrate odometry from encoder deltas (or from the commanded speed if no encoders)."""
        if self.left.encoder is not None and self.right.encoder is not None:
            dl = self.left.delta_steps() / self.ticks_per_mm
            dr = self.right.delta_steps() / self.ticks_per_mm
        else:
            dl = self.left.command * self.max_speed * dt
            dr = self.right.command * self.max_speed * dt
        ds = (dl + dr) / 2.0
        dtheta = (dr - dl) / self.geometry.track_width_mm
        th = math.radians(self.pose.theta_deg) + dtheta / 2.0
        self.pose.x_mm += ds * math.cos(th)
        self.pose.y_mm += ds * math.sin(th)
        self.pose.theta_deg = (self.pose.theta_deg + math.degrees(dtheta) + 180.0) % 360.0 - 180.0
        if dt > 0:
            self.pose.v_mm_s = ds / dt
            self.pose.w_deg_s = math.degrees(dtheta) / dt
        return self.pose

    def reset_pose(self) -> None:
        self.pose = Pose()
