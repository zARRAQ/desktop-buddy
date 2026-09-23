"""A desk, a robot, a person. Millimetres; x right, y up; heading 0 along +x, counter-clockwise."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from robot.core.messages import FaceObs, PerceptionFaces
from robot.hal.sensors.cliff import OFF_EDGE_MM

SENSOR_OFFSETS = {  # relative to robot centre, (forward_mm, left_mm)
    "front": (70.0, 0.0),
    "left": (50.0, 50.0),
    "right": (50.0, -50.0),
}


@dataclass
class World:
    desk_w_mm: float = 900.0
    desk_h_mm: float = 600.0
    robot_x: float = 0.0
    robot_y: float = -100.0
    robot_theta: float = 90.0  # facing +y, toward the person side of the desk
    person_x: float = 0.0
    person_y: float = 650.0  # standing at the edge of the desk
    person_visible: bool = True
    person_name: str | None = "Ann"  # None = stranger
    hfov_deg: float = 66.0
    max_see_mm: float = 1800.0
    track_id: int = 1
    events: list[str] = field(default_factory=list)

    # -- geometry ------------------------------------------------------------------------
    def set_robot_pose(self, x: float, y: float, theta: float) -> None:
        self.robot_x, self.robot_y, self.robot_theta = x, y, theta

    def sensor_world_pos(self, name: str) -> tuple[float, float]:
        fwd, left = SENSOR_OFFSETS[name]
        th = math.radians(self.robot_theta)
        x = self.robot_x + fwd * math.cos(th) - left * math.sin(th)
        y = self.robot_y + fwd * math.sin(th) + left * math.cos(th)
        return x, y

    def on_desk(self, x: float, y: float) -> bool:
        return abs(x) <= self.desk_w_mm / 2 and abs(y) <= self.desk_h_mm / 2

    def cliff_readings(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for name in SENSOR_OFFSETS:
            x, y = self.sensor_world_pos(name)
            out[name] = 25 if self.on_desk(x, y) else OFF_EDGE_MM
        return out

    def robot_on_desk(self) -> bool:
        return self.on_desk(self.robot_x, self.robot_y)

    def person_relative(self) -> tuple[float, float]:
        """(bearing_deg, distance_mm): bearing positive = person on the robot's left."""
        dx, dy = self.person_x - self.robot_x, self.person_y - self.robot_y
        dist = math.hypot(dx, dy)
        bearing = (math.degrees(math.atan2(dy, dx)) - self.robot_theta + 180.0) % 360.0 - 180.0
        return bearing, dist

    def person_in_view(self) -> bool:
        if not self.person_visible:
            return False
        bearing, dist = self.person_relative()
        return abs(bearing) <= self.hfov_deg / 2 and dist <= self.max_see_mm

    def faces(self, *, recognized: bool) -> PerceptionFaces:
        if not self.person_in_view():
            return PerceptionFaces(frame_ts=0.0, faces=[], backend="sim")
        bearing, dist = self.person_relative()
        cx = 0.5 - bearing / self.hfov_deg  # person on the robot's left appears on the image's left
        size = max(0.06, min(0.6, 140.0 / max(dist, 50.0)))
        cy = 0.42
        face = FaceObs(
            track_id=self.track_id,
            bbox=(cx - size / 2, cy - size / 2, cx + size / 2, cy + size / 2),
            center=(cx, cy),
            score=0.95,
            name=self.person_name if recognized else None,
            match_score=0.72 if recognized and self.person_name else None,
        )
        return PerceptionFaces(frame_ts=0.0, faces=[face], backend="sim", fps=10.0)

    def move_person(self, dx: float, dy: float) -> None:
        self.person_x = max(-1200.0, min(1200.0, self.person_x + dx))
        self.person_y = max(-800.0, min(1200.0, self.person_y + dy))

    def new_track(self) -> None:
        self.track_id += 1

    def reset(self) -> None:
        self.__init__(person_name=self.person_name)  # type: ignore[misc]
