"""Simulator-side stand-ins: perception from the world, cliff sensors from the desk edge."""

from __future__ import annotations

import time

from robot.core.bus import BusClient
from robot.core.config import RobotConfig
from robot.core.messages import Envelope, MotionOdometry, PersonEvent
from robot.core.service import Service
from robot.hal.sensors.cliff import CliffSensors
from robot.sim.world import World


class WorldCliffSensors(CliffSensors):
    def __init__(self, world: World) -> None:
        self.world = world

    def read(self) -> dict[str, int]:
        return self.world.cliff_readings()


class SimPerception(Service):
    """Publishes perception.faces / perception.person from the world instead of a camera."""

    name = "perception"
    subscriptions = ("perception.enroll", "motion.odometry")
    tick_hz = 10.0

    def __init__(self, config: RobotConfig, bus: BusClient, world: World) -> None:
        super().__init__(config, bus)
        self.world = world
        self._visible_track: int | None = None
        self._appeared_at = 0.0
        self._recognized = False
        self._enrolling: tuple[str, int] | None = None

    def on_message(self, env: Envelope) -> None:
        if env.topic == MotionOdometry.TOPIC:
            o = MotionOdometry.model_validate(env.data)
            # odometry starts at the robot's initial pose
            self.world.set_robot_pose(o.x_mm, -100.0 + o.y_mm, 90.0 + o.theta_deg)
        elif env.topic == "perception.enroll":
            name = str(env.data.get("name", "")).strip()
            if name:
                self._enrolling = (name, int(env.data.get("samples", 5)))

    def tick(self, dt: float) -> None:
        now = time.monotonic()
        in_view = self.world.person_in_view()
        tid = self.world.track_id
        if in_view and self._visible_track != tid:
            if self._visible_track is not None:
                self.bus.publish_payload(PersonEvent(event="left", track_id=self._visible_track))
            self._visible_track = tid
            self._appeared_at = now
            self._recognized = False
            self.bus.publish_payload(PersonEvent(event="appeared", track_id=tid))
        elif not in_view and self._visible_track is not None:
            self.bus.publish_payload(
                PersonEvent(
                    event="left",
                    track_id=self._visible_track,
                    name=self.world.person_name if self._recognized else None,
                )
            )
            self._visible_track = None
            self._recognized = False
        if in_view and self._enrolling is not None:
            name, remaining = self._enrolling
            remaining -= 1
            if remaining <= 0:
                self._enrolling = None
                self.world.person_name = name
                self._recognized = True
                self.bus.publish("perception.enrolled", {"name": name, "backend": "sim"})
                self.bus.publish("memory.changed", {"what": "embeddings", "name": name})
                self.world.events.append(f"enrolled {name}")
            else:
                self._enrolling = (name, remaining)
        if in_view and not self._recognized and self.world.person_name and now - self._appeared_at >= 0.6:
            self._recognized = True
            self.bus.publish_payload(
                PersonEvent(event="recognized", track_id=tid, name=self.world.person_name, score=0.72)
            )
        self.bus.publish_payload(self.world.faces(recognized=self._recognized))
