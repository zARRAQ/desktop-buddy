"""Orchestrator logic. No bus, no threads: events in, payloads out, so it is fully testable."""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

from robot.core.config import RobotConfig
from robot.core.messages import (
    BrainRequest,
    BrainResponse,
    FaceExpression,
    FaceLook,
    MotionCommand,
    OrchestratorState,
    Payload,
    PerceptionFaces,
    PersonEvent,
    SafetyState,
    VoiceSay,
)
from robot.core.vocabulary import resolve_expression
from robot.memory import Memory


class State(str, Enum):
    BOOT = "boot"
    IDLE = "idle"
    ATTENDING = "attending"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    SLEEP = "sleep"


NAME_RE = [
    re.compile(r"\bmy name is ([a-z]+)", re.IGNORECASE),
    re.compile(r"\b(?:i am|i'm) ([a-z]+)\b", re.IGNORECASE),
    re.compile(r"\bcall me ([a-z]+)", re.IGNORECASE),
    re.compile(r"^([a-z]+)[.!]?$", re.IGNORECASE),
]
NOT_NAMES = {
    "Fine",
    "Good",
    "Great",
    "Here",
    "Back",
    "Sorry",
    "Hungry",
    "Tired",
    "Busy",
    "Not",
    "Just",
    "Ok",
    "Okay",
    "Yes",
    "No",
    "Hello",
    "Hi",
    "Hey",
    "Thanks",
    "Please",
    "What",
    "Who",
    "Why",
    "Nothing",
    "Nobody",
    "Well",
    "Home",
    "Done",
    "Ready",
    "Late",
    "Early",
    "Cold",
    "Hot",
    "Sure",
    "Maybe",
    "Stop",
    "Wait",
    "Listening",
}


def extract_name(text: str) -> str | None:
    t = text.strip()
    for pat in NAME_RE:
        m = pat.search(t)
        if m:
            name = m.group(1).capitalize()
            if name not in NOT_NAMES and len(name) > 1:
                return name
    return None


@dataclass
class Attention:
    track_id: int | None = None
    name: str | None = None
    center: tuple[float, float] | None = None
    last_seen: float = 0.0
    greeted: bool = False
    asked_name: bool = False
    appeared_at: float = 0.0


@dataclass
class Orchestrator:
    config: RobotConfig
    memory: Memory
    publish: Callable[[Payload], None]
    clock: Callable[[], float] = time.monotonic
    state: State = State.BOOT
    attention: Attention = field(default_factory=Attention)
    last_activity: float = 0.0
    pending_request: str | None = None
    history: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    faces_last: float = 0.0
    safety_enabled: bool = False
    awaiting_name: bool = False
    _state_since: float = 0.0
    _pending_speech: list[VoiceSay] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.last_activity = self.clock()
        self._state_since = self.clock()

    # -- helpers --------------------------------------------------------------------------
    def _set_state(self, new: State) -> None:
        if new == self.state:
            return
        self.state = new
        self._state_since = self.clock()
        self.publish(OrchestratorState(state=new.value, person=self.attention.name, since=time.time()))

    def _express(self, name: str, hold_ms: int | None = None) -> None:
        try:
            self.publish(FaceExpression(name=resolve_expression(name), hold_ms=hold_ms))
        except KeyError:
            self.publish(FaceExpression(name="neutral", hold_ms=hold_ms))

    def _say(self, text: str, *, expression: str | None = None, gesture: str | None = None) -> None:
        if expression:
            self._express(expression)
        if gesture == "nod":
            self.publish(MotionCommand(type="nod"))
        elif gesture == "shake":
            self.publish(MotionCommand(type="shake"))
        self.publish(VoiceSay(text=text, request_id=uuid.uuid4().hex[:8]))
        self._set_state(State.SPEAKING)
        self.last_activity = self.clock()

    def _wake_up(self) -> None:
        if self.state == State.SLEEP:
            self._express("surprise", hold_ms=900)
            self.publish(MotionCommand(type="center"))
        self._set_state(State.ATTENDING if self.attention.track_id is not None else State.IDLE)

    # -- events ---------------------------------------------------------------------------
    def on_faces(self, msg: PerceptionFaces) -> None:
        now = self.clock()
        if not msg.faces:
            return
        self.faces_last = now
        self.last_activity = now
        target = next((f for f in msg.faces if f.track_id == self.attention.track_id), None)
        if target is None:
            target = max(msg.faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
            self.attention = Attention(track_id=target.track_id, appeared_at=now)
        self.attention.center = target.center
        self.attention.last_seen = now
        if target.name and self.attention.name != target.name:
            self.attention.name = target.name
        # gaze: eyes follow, head follows if it can
        cx, cy = target.center
        sign = -1.0 if self.config.orchestrator.mirror_gaze else 1.0
        self.publish(FaceLook(x=sign * (cx - 0.5) * 2.0, y=(cy - 0.5) * 2.0))
        if self.config.orchestrator.track_with_head and self.state not in (State.SLEEP,):
            yaw = sign * (cx - 0.5) * self.config.orchestrator.camera_hfov_deg
            pitch = -(cy - 0.5) * self.config.orchestrator.camera_vfov_deg
            self.publish(MotionCommand(type="look_at", yaw_deg=yaw, pitch_deg=pitch))
        if self.state in (State.IDLE, State.SLEEP, State.BOOT):
            self._wake_up()
            self._set_state(State.ATTENDING)
        self._maybe_greet_unknown(now)

    def on_person(self, msg: PersonEvent) -> None:
        now = self.clock()
        self.last_activity = now
        if msg.event == "recognized" and msg.name:
            if self.attention.track_id in (None, msg.track_id):
                self.attention.track_id = msg.track_id
                self.attention.name = msg.name
                self.awaiting_name = False
            if self.state in (State.IDLE, State.SLEEP, State.BOOT):
                self._wake_up()
            self._greet_known(msg.name)
        elif msg.event == "left" and msg.track_id == self.attention.track_id:
            self.attention = Attention()
            if self.state == State.ATTENDING:
                self._express("neutral")
                self._set_state(State.IDLE)

    def _greet_known(self, name: str) -> None:
        if not self.config.orchestrator.greet or self.attention.greeted:
            return
        person = self.memory.get_person(name)
        cooldown = self.config.memory.greet_cooldown_s
        recently = (
            person is not None
            and person.last_seen is not None
            and (time.time() - person.last_seen) < cooldown
            and person.seen_count > 1
        )
        self.attention.greeted = True
        if recently or self.state in (State.LISTENING, State.THINKING, State.SPEAKING):
            self._express("happiness", hold_ms=1500)
            return
        self._say(f"Hi {name}!", expression="happiness", gesture="nod")
        self.memory.log_event("greet", name)

    def _maybe_greet_unknown(self, now: float) -> None:
        a = self.attention
        if (
            not self.config.orchestrator.greet_unknown
            or a.name is not None
            or a.asked_name
            or a.track_id is None
            or now - a.appeared_at < 2.5  # give recognition a chance first
            or self.state != State.ATTENDING
        ):
            return
        a.asked_name = True
        self.awaiting_name = True
        self._say(
            f"Hello! I don't think we've met. I'm {self.config.system.name.capitalize()}. What's your name?",
            expression="confusion",
        )

    def on_wake(self) -> None:
        self.last_activity = self.clock()
        if self.state == State.SLEEP:
            self._wake_up()
        if self.state in (State.IDLE, State.ATTENDING):
            self._express("listening")
            self._set_state(State.LISTENING)

    def on_listening(self, state: str) -> None:
        if state == "start" and self.state not in (State.LISTENING, State.SPEAKING):
            self._express("listening")
            self._set_state(State.LISTENING)
        elif state == "timeout" and self.state == State.LISTENING:
            self._express("confusion", hold_ms=1200)
            self._set_state(State.ATTENDING if self.attention.track_id is not None else State.IDLE)

    def on_transcript(self, text: str) -> None:
        self.last_activity = self.clock()
        text = text.strip()
        if not text:
            self._express("confusion", hold_ms=1200)
            self._set_state(State.ATTENDING if self.attention.track_id is not None else State.IDLE)
            return
        if self.awaiting_name:
            name = extract_name(text)
            if name:
                self.awaiting_name = False
                self.attention.name = name
                self.memory.add_person(name)
                self.publish_raw("perception.enroll", {"name": name, "track_id": self.attention.track_id, "samples": 5})
                self._say(
                    f"Nice to meet you, {name}! Look at me for a moment so I remember your face.",
                    expression="excitement",
                    gesture="nod",
                )
                self.memory.log_event("met", name)
                return
        person = self.attention.name
        facts = self.memory.facts(person) if person else []
        req_id = uuid.uuid4().hex[:8]
        self.pending_request = req_id
        hist = self.history.setdefault(person or "_", [])
        self.publish(BrainRequest(request_id=req_id, text=text, person=person, facts=facts, history=list(hist)))
        hist.append({"role": "user", "content": text})
        self._express("thinking")
        self._set_state(State.THINKING)

    def on_brain_response(self, resp: BrainResponse) -> None:
        if resp.request_id != self.pending_request:
            return
        self.pending_request = None
        person = self.attention.name
        hist = self.history.setdefault(person or "_", [])
        hist.append({"role": "assistant", "content": resp.say})
        max_turns = self.config.orchestrator.history_turns
        if len(hist) > max_turns:
            del hist[: len(hist) - max_turns]
        for fact in resp.remember:
            self.memory.add_fact(fact, person, limit=self.config.memory.max_facts_per_person)
        self._say(resp.say, expression=resp.expression, gesture=resp.gesture)

    def on_speaking(self, state: str) -> None:
        if state in ("end", "error") and self.state == State.SPEAKING:
            new = State.ATTENDING if self.attention.track_id is not None else State.IDLE
            self._set_state(new)
            if new == State.ATTENDING:
                self._express("neutral")

    def on_safety(self, msg: SafetyState) -> None:
        self.safety_enabled = msg.enabled

    def tick(self) -> None:
        now = self.clock()
        if self.state == State.BOOT:
            self._express("neutral")
            self._set_state(State.IDLE)
            return
        if (
            self.state == State.ATTENDING
            and now - self.attention.last_seen > self.config.orchestrator.attention_timeout_s
        ):
            self.attention = Attention()
            self._express("neutral")
            self._set_state(State.IDLE)
        if self.state == State.THINKING and now - self._state_since > self.config.brain.timeout_s + 5:
            self.pending_request = None
            self._say("Sorry, I lost my train of thought.", expression="embarrassment")
        if self.state == State.IDLE and now - self.last_activity > self.config.orchestrator.sleep_after_s:
            self._express("asleep")
            self.publish(MotionCommand(type="relax"))
            self._set_state(State.SLEEP)

    # raw publish hook for topics without a typed payload
    publish_raw: Callable[[str, dict[str, object]], None] = lambda topic, data: None  # noqa: ARG005
