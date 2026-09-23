"""The simulator application."""

from __future__ import annotations

import logging
import math
import os
import time
from collections import Counter
from dataclasses import dataclass, field

import pygame

from robot.brain.service import BrainService
from robot.core import paths
from robot.core.bus import LocalHub
from robot.core.config import RobotConfig
from robot.core.messages import Envelope, FaceExpression, MotionCommand
from robot.core.service import Service, ServiceThread
from robot.face import expressions
from robot.face.service import FaceService
from robot.hal.actuators.map import ActuatorMap
from robot.hal.display.null import NullDisplay
from robot.hal.gpio.mock import MockGpioBackend
from robot.hal.sensors.imu import MockImu
from robot.memory import Memory
from robot.motion.service import MotionService
from robot.orchestrator.service import OrchestratorService
from robot.safety.service import SafetyService
from robot.sim.services import SimPerception, WorldCliffSensors
from robot.sim.world import World
from robot.voice.factory import build_engines
from robot.voice.fake import FakeStt
from robot.voice.service import VoiceService

log = logging.getLogger(__name__)

FACE_PX = 400
DESK_PX = 480
LOG_PX = 330
HEIGHT = 480

UTTERANCES = [
    "hello there",
    "what is your name",
    "remember that I like tea",
    "what do you know about me",
    "tell me a joke",
    "what time is it",
]

QUIET_TOPICS = {
    "service.heartbeat",
    "motion.heartbeat",
    "perception.faces",
    "face.look",
    "safety.state",
    "motion.odometry",
    "motion.command",
    "face.state",
    "motion.state",
}

Color = tuple[int, int, int]


@dataclass
class SimStats:
    topics: Counter[str] = field(default_factory=Counter)
    last: list[Envelope] = field(default_factory=list)
    brain_responses: int = 0
    transcripts: int = 0

    def note(self, env: Envelope) -> None:
        self.topics[env.topic] += 1
        if env.topic == "brain.response":
            self.brain_responses += 1
        if env.topic == "voice.transcript":
            self.transcripts += 1
        if env.topic not in QUIET_TOPICS:
            self.last.append(env)
            if len(self.last) > 24:
                del self.last[0]


class Simulator:
    def __init__(self, config: RobotConfig, *, headless: bool = False, seconds: float | None = None) -> None:
        self.config = config
        self.headless = headless
        self.seconds = seconds
        self.hub = LocalHub(record=0)
        self.world = World()
        self.stats = SimStats()
        self.gpio = MockGpioBackend()
        shape = "round" if config.display.shape == "round" else "rect"
        self.face_display = NullDisplay(FACE_PX, FACE_PX, shape=shape)
        self.threads: dict[str, ServiceThread] = {}
        self.services: dict[str, Service] = {}
        self._utter = 0
        self.memory = Memory(config.memory_path().with_name("sim-memory.sqlite"))
        self.fake_stt: FakeStt | None = None
        self.probe = self.hub.client("sim")
        self.motion: MotionService | None = None

    # -- lifecycle -------------------------------------------------------------------------
    def start(self) -> None:
        cfg = self.config
        self.probe.subscribe("")
        face = FaceService(cfg, self.hub.client("face"), display=self.face_display)
        actuators = ActuatorMap.from_config(cfg.actuators, self.gpio, None, mock=True)
        motion = MotionService(cfg, self.hub.client("motion"), actuators=actuators)
        self.motion = motion
        engines = build_engines(cfg.voice, fake=True)
        self.fake_stt = engines.stt if isinstance(engines.stt, FakeStt) else None
        voice = VoiceService(cfg, self.hub.client("voice"), engines=engines)
        brain = BrainService(cfg, self.hub.client("brain"))
        orch = OrchestratorService(cfg, self.hub.client("orchestrator"), memory=self.memory)
        perception = SimPerception(cfg, self.hub.client("perception"), self.world)
        for svc in (face, motion, voice, brain, orch, perception):
            self._start(svc)
        self.start_safety()

    def _start(self, svc: Service) -> None:
        t = ServiceThread(svc)
        self.threads[svc.name] = t
        self.services[svc.name] = svc
        t.start()

    def start_safety(self) -> None:
        safety = SafetyService(
            self.config,
            self.hub.client("safety"),
            gpio=self.gpio,
            cliff=WorldCliffSensors(self.world),
            imu=MockImu(),
        )
        self._start(safety)

    def kill_safety(self) -> None:
        """Simulates the supervisor process dying: the enable line must fall and motion must stop."""
        t = self.threads.pop("safety", None)
        if t is not None:
            t.stop()
            self.world.events.append("safety supervisor killed")

    def stop(self) -> None:
        for t in list(self.threads.values()):
            t.stop(timeout=3)
        self.memory.close()

    # -- inputs ---------------------------------------------------------------------------
    def wake(self) -> None:
        self.probe.publish("voice.wake", {"word": "space", "score": 1.0})

    def next_utterance(self) -> None:
        if self.fake_stt is not None:
            text = UTTERANCES[self._utter % len(UTTERANCES)]
            self._utter += 1
            self.fake_stt.say_next(text)
            self.world.events.append(f"you will say: {text}")
        self.wake()

    def expression(self, name: str) -> None:
        self.probe.publish_payload(FaceExpression(name=name))

    def nudge(self, key: str) -> None:
        cmd = {
            "w": MotionCommand(type="drive", speed_mm_s=120, distance_mm=40),
            "s": MotionCommand(type="drive", speed_mm_s=-120, distance_mm=40),
            "a": MotionCommand(type="turn", turn_deg_s=90, angle_deg=15),
            "d": MotionCommand(type="turn", turn_deg_s=-90, angle_deg=15),
        }[key]
        self.probe.publish_payload(cmd)

    def reset(self) -> None:
        self.world.reset()
        self.world.new_track()
        if self.motion is not None and self.motion.actuators is not None and self.motion.actuators.drive is not None:
            self.motion.actuators.drive.reset_pose()
        if "safety" not in self.threads:
            self.start_safety()
        self.probe.publish("safety.command", {"command": "reset"})
        self.world.events.append("world reset")

    def toggle_known(self) -> None:
        self.world.person_name = None if self.world.person_name else "Ann"
        self.world.new_track()

    def toggle_visible(self) -> None:
        self.world.person_visible = not self.world.person_visible

    # -- main loops -------------------------------------------------------------------------
    def pump_bus(self) -> None:
        while True:
            env = self.probe.recv(timeout=0)
            if env is None:
                break
            self.stats.note(env)

    def run_headless(self) -> int:
        """Scripted scenario for CI: a person appears, we wake, talk, get answered, drive a bit."""
        duration = self.seconds or 3.0
        t0 = time.monotonic()
        scripted = [(0.4, self.next_utterance), (min(2.0, duration * 0.6), lambda: self.nudge("w"))]
        while time.monotonic() - t0 < duration:
            self.pump_bus()
            elapsed = time.monotonic() - t0
            while scripted and elapsed >= scripted[0][0]:
                scripted.pop(0)[1]()
            time.sleep(0.02)
        self.pump_bus()
        errors = [n for n, t in self.threads.items() if t.error is not None]
        ok = self.face_display.frames > 0 and self.stats.brain_responses >= 1 and not errors
        print(
            f"sim headless: {duration:.0f}s, {self.face_display.frames} face frames, "
            f"{self.stats.transcripts} transcripts, {self.stats.brain_responses} brain responses"
        )
        for topic, n in sorted(self.stats.topics.items()):
            print(f"  {topic:24s} {n}")
        if errors:
            print(f"service errors: {errors}")
        return 0 if ok else 1

    def run_window(self) -> int:
        pygame.init()
        screen = pygame.display.set_mode((FACE_PX + DESK_PX + LOG_PX, HEIGHT))
        pygame.display.set_caption("desktop-buddy simulator")
        font = pygame.font.SysFont(None, 16)
        big = pygame.font.SysFont(None, 20)
        clock = pygame.time.Clock()
        running = True
        t0 = time.monotonic()
        while running:
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    running = False
                elif ev.type == pygame.KEYDOWN:
                    running = self._key(ev.key)
            keys = pygame.key.get_pressed()
            step = 6.0
            if keys[pygame.K_LEFT]:
                self.world.move_person(-step, 0)
            if keys[pygame.K_RIGHT]:
                self.world.move_person(step, 0)
            if keys[pygame.K_UP]:
                self.world.move_person(0, step)
            if keys[pygame.K_DOWN]:
                self.world.move_person(0, -step)
            self.pump_bus()
            self._draw(screen, font, big)
            pygame.display.flip()
            clock.tick(30)
            if self.seconds is not None and time.monotonic() - t0 >= self.seconds:
                running = False
        pygame.quit()
        return 0

    def _key(self, key: int) -> bool:
        if key in (pygame.K_ESCAPE, pygame.K_q):
            return False
        if key == pygame.K_SPACE:
            self.wake()
        elif key == pygame.K_t:
            self.next_utterance()
        elif key == pygame.K_k:
            self.kill_safety()
        elif key == pygame.K_r:
            self.reset()
        elif key == pygame.K_n:
            self.toggle_known()
        elif key == pygame.K_e:
            self.toggle_visible()
        elif key in (pygame.K_w, pygame.K_a, pygame.K_s, pygame.K_d):
            self.nudge(chr(key))
        elif pygame.K_1 <= key <= pygame.K_9:
            self.expression(expressions.NUMBER_KEYS[key - pygame.K_0])
        return True

    # -- drawing ----------------------------------------------------------------------------
    def _draw(self, scr: pygame.Surface, font: pygame.font.Font, big: pygame.font.Font) -> None:
        scr.fill((18, 18, 22))
        if self.face_display.last is not None:
            scr.blit(self.face_display.last, (0, 40))
        _text(scr, big, "FACE  (1-9 expressions)", (8, 10))
        ox, oy = FACE_PX, 0
        pygame.draw.rect(scr, (30, 30, 36), pygame.Rect(ox, oy, DESK_PX, HEIGHT))
        scale = 0.42
        cx, cy = ox + DESK_PX // 2, oy + HEIGHT // 2 + 40

        def wp(x: float, y: float) -> tuple[int, int]:
            return int(cx + x * scale), int(cy - y * scale)

        w = self.world
        rect = pygame.Rect(0, 0, int(w.desk_w_mm * scale), int(w.desk_h_mm * scale))
        rect.center = (cx, cy)
        pygame.draw.rect(scr, (92, 64, 40), rect)
        pygame.draw.rect(scr, (140, 100, 60), rect, 2)
        th = math.radians(w.robot_theta)
        for s in (-1, 1):
            a = th + s * math.radians(w.hfov_deg / 2)
            end = wp(w.robot_x + 600 * math.cos(a), w.robot_y + 600 * math.sin(a))
            pygame.draw.line(scr, (60, 90, 110), wp(w.robot_x, w.robot_y), end, 1)
        tri = [
            wp(w.robot_x + 45 * math.cos(th), w.robot_y + 45 * math.sin(th)),
            wp(w.robot_x + 35 * math.cos(th + 2.4), w.robot_y + 35 * math.sin(th + 2.4)),
            wp(w.robot_x + 35 * math.cos(th - 2.4), w.robot_y + 35 * math.sin(th - 2.4)),
        ]
        enabled = self.gpio.levels.get(self.config.safety.enable_pin, False)
        pygame.draw.polygon(scr, (80, 200, 120) if enabled else (200, 70, 70), tri)
        for name, mm in w.cliff_readings().items():
            sx, sy = w.sensor_world_pos(name)
            pygame.draw.circle(scr, (255, 80, 80) if mm > 60 else (120, 255, 120), wp(sx, sy), 4)
        if w.person_visible:
            color: Color = (120, 170, 255) if w.person_in_view() else (90, 90, 120)
            px, py = wp(w.person_x, w.person_y)
            pygame.draw.circle(scr, color, (px, py), 14)
            _text(scr, font, w.person_name or "stranger", (px - 20, py + 16))
        safety_txt = "SAFETY: ENABLED" if enabled else "SAFETY: DISABLED"
        if "safety" not in self.threads:
            safety_txt += " (supervisor dead)"
        _text(scr, big, safety_txt, (ox + 8, 10), color=(120, 255, 120) if enabled else (255, 110, 110))
        orch = self.services.get("orchestrator")
        logic = getattr(orch, "logic", None)
        state = getattr(logic, "state", None)
        state_txt = state.value if state is not None else "-"
        _text(scr, font, f"orchestrator: {state_txt}   robot on desk: {w.robot_on_desk()}", (ox + 8, 34))
        help_txt = (
            "arrows: person  WASD: robot  space: wake  T: talk  N: known/stranger  E: hide  K: kill safety  R: reset"
        )
        _text(scr, font, help_txt, (ox + 8, HEIGHT - 20), color=(150, 150, 160))
        for line, ev_text in enumerate(w.events[-4:]):
            _text(scr, font, ev_text, (ox + 8, 52 + 16 * line), color=(220, 200, 120))
        lx = FACE_PX + DESK_PX
        pygame.draw.rect(scr, (14, 14, 18), pygame.Rect(lx, 0, LOG_PX, HEIGHT))
        _text(scr, big, "BUS", (lx + 8, 10))
        for i, env in enumerate(self.stats.last[-26:]):
            _text(scr, font, _describe(env), (lx + 8, 34 + i * 17), color=(200, 200, 210))


def _text(
    scr: pygame.Surface, font: pygame.font.Font, text: str, pos: tuple[int, int], color: Color = (230, 230, 230)
) -> None:
    scr.blit(font.render(text[:64], True, color), pos)


def _describe(env: Envelope) -> str:
    d = env.data
    if env.topic == "voice.say":
        return f"say: {d.get('text', '')}"
    if env.topic == "voice.transcript":
        return f"heard: {d.get('text', '')}"
    if env.topic == "brain.response":
        return f"brain[{d.get('backend', '')}]: {d.get('expression', '')}"
    if env.topic == "face.expression":
        return f"face: {d.get('name', '')}"
    if env.topic == "perception.person":
        return f"person {d.get('event')} {d.get('name') or ''}"
    if env.topic == "orchestrator.state":
        return f"state -> {d.get('state')}"
    if env.topic == "safety.estop":
        return f"ESTOP: {d.get('reason')}"
    return f"{env.topic} <- {env.src}"


def run_simulator(config: RobotConfig, *, headless: bool = False, seconds: float | None = None) -> int:
    if headless:
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    paths.data_dir().mkdir(parents=True, exist_ok=True)
    sim = Simulator(config, headless=headless, seconds=seconds)
    sim.start()
    try:
        return sim.run_headless() if headless else sim.run_window()
    finally:
        sim.stop()
