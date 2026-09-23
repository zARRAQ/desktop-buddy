"""Bus topics and typed payloads.

Topics are dotted strings; subscribers match on prefix. Payloads are plain dicts on the
wire (msgpack), validated at the edges with these pydantic models when a consumer cares.
"""

from __future__ import annotations

import time
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field


class Envelope(BaseModel):
    """What actually travels on the wire."""

    topic: str
    ts: float
    src: str
    seq: int
    data: dict[str, Any]


class Payload(BaseModel):
    """Base for typed payloads. ``TOPIC`` is the topic they are published on."""

    TOPIC: ClassVar[str] = ""

    def to_data(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


# --- perception -----------------------------------------------------------------------

Bbox = tuple[float, float, float, float]  # x1, y1, x2, y2 normalised to [0, 1]


class FaceObs(BaseModel):
    track_id: int
    bbox: Bbox
    center: tuple[float, float]  # normalised, (0.5, 0.5) is frame centre
    score: float
    name: str | None = None
    match_score: float | None = None


class PerceptionFaces(Payload):
    TOPIC = "perception.faces"
    frame_ts: float
    faces: list[FaceObs] = Field(default_factory=list)
    backend: str = ""
    fps: float = 0.0


class PersonEvent(Payload):
    """Edge-triggered: someone appeared, was recognised, or left."""

    TOPIC = "perception.person"
    event: Literal["appeared", "recognized", "left"]
    track_id: int
    name: str | None = None
    score: float | None = None


# --- voice ---------------------------------------------------------------------------


class VoiceWake(Payload):
    TOPIC = "voice.wake"
    word: str
    score: float = 1.0


class VoiceTranscript(Payload):
    TOPIC = "voice.transcript"
    text: str
    final: bool = True
    duration_s: float = 0.0


class VoiceSay(Payload):
    """Request speech. The voice service answers with VoiceSpeaking start/end."""

    TOPIC = "voice.say"
    text: str
    request_id: str = ""


class VoiceSpeaking(Payload):
    TOPIC = "voice.speaking"
    state: Literal["start", "end", "error"]
    text: str = ""
    request_id: str = ""


class VoiceListening(Payload):
    TOPIC = "voice.listening"
    state: Literal["start", "end", "timeout"]


# --- brain ---------------------------------------------------------------------------


class BrainRequest(Payload):
    TOPIC = "brain.request"
    request_id: str
    text: str
    person: str | None = None
    facts: list[str] = Field(default_factory=list)
    history: list[dict[str, str]] = Field(default_factory=list)


class BrainResponse(Payload):
    TOPIC = "brain.response"
    request_id: str
    say: str
    expression: str = "neutral"
    gesture: str | None = None
    remember: list[str] = Field(default_factory=list)
    backend: str = ""
    latency_s: float = 0.0
    error: str | None = None


# --- face ----------------------------------------------------------------------------


class FaceExpression(Payload):
    TOPIC = "face.expression"
    name: str
    hold_ms: int | None = Field(default=None, description="Return to previous after this")


class FaceLook(Payload):
    """Where the eyes point, in [-1, 1] screen coordinates. (0, 0) is straight ahead."""

    TOPIC = "face.look"
    x: float
    y: float


class FaceMode(Payload):
    """Force an overlay: listening bars, thinking dots or the speaking mouth. The face
    service normally derives this from ``orchestrator.state`` and ``voice.speaking``."""

    TOPIC = "face.mode"
    mode: Literal["none", "listening", "thinking", "speaking"]


class FaceState(Payload):
    TOPIC = "face.state"
    expression: str
    fps: float
    quality_level: int
    backend: str
    mode: str = "none"


# --- motion --------------------------------------------------------------------------


class MotionCommand(Payload):
    TOPIC = "motion.command"
    type: Literal["look_at", "nod", "shake", "drive", "turn", "stop", "relax", "center"]
    yaw_deg: float | None = None
    pitch_deg: float | None = None
    speed_mm_s: float | None = None
    turn_deg_s: float | None = None
    distance_mm: float | None = None
    angle_deg: float | None = None
    duration_s: float | None = None


class MotionOdometry(Payload):
    TOPIC = "motion.odometry"
    x_mm: float
    y_mm: float
    theta_deg: float
    v_mm_s: float
    w_deg_s: float


class MotionState(Payload):
    TOPIC = "motion.state"
    primitives: list[str]
    pan_deg: float | None = None
    tilt_deg: float | None = None
    driving: bool = False


class MotionHeartbeat(Payload):
    TOPIC = "motion.heartbeat"
    seq: int


# --- safety --------------------------------------------------------------------------


class SafetyState(Payload):
    TOPIC = "safety.state"
    enabled: bool
    reason: str = "ok"
    cliff_mm: dict[str, int] = Field(default_factory=dict)
    tilt_deg: float | None = None
    picked_up: bool = False


class SafetyEstop(Payload):
    TOPIC = "safety.estop"
    reason: str


# --- system --------------------------------------------------------------------------


class ServiceHeartbeat(Payload):
    TOPIC = "service.heartbeat"
    name: str
    pid: int
    uptime_s: float
    rss_mb: float | None = None


class SystemBattery(Payload):
    TOPIC = "system.battery"
    voltage: float
    current_a: float
    percent: float | None = None


class SystemHealth(Payload):
    TOPIC = "system.health"
    temp_c: float | None = None
    throttled: bool = False


class OrchestratorState(Payload):
    TOPIC = "orchestrator.state"
    state: str
    person: str | None = None
    since: float = Field(default_factory=time.time)


ALL_TOPICS: tuple[str, ...] = tuple(sorted(cls.TOPIC for cls in Payload.__subclasses__() if cls.TOPIC))
