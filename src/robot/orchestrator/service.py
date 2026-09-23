"""Orchestrator service: bus wiring around :class:`robot.orchestrator.state.Orchestrator`."""

from __future__ import annotations

from robot.core.bus import BusClient
from robot.core.config import RobotConfig
from robot.core.messages import (
    BrainResponse,
    Envelope,
    PerceptionFaces,
    PersonEvent,
    SafetyState,
    VoiceListening,
    VoiceSpeaking,
    VoiceTranscript,
    VoiceWake,
)
from robot.core.service import Service
from robot.memory import Memory
from robot.orchestrator.state import Orchestrator


class OrchestratorService(Service):
    name = "orchestrator"
    subscriptions = ("perception.faces", "perception.person", "voice.", "brain.response", "safety.state")

    def __init__(self, config: RobotConfig, bus: BusClient, *, memory: Memory | None = None) -> None:
        super().__init__(config, bus)
        self.tick_hz = config.orchestrator.tick_hz
        self._memory = memory
        self.logic: Orchestrator | None = None

    def setup(self) -> None:
        memory = self._memory or Memory(self.config.memory_path())
        self.logic = Orchestrator(self.config, memory, self.bus.publish_payload)
        self.logic.publish_raw = self.bus.publish
        self.log.info("memory at %s with %d people", memory.path, len(memory.list_people()))

    def on_message(self, env: Envelope) -> None:
        o = self.logic
        if o is None:
            return
        t = env.topic
        if t == PerceptionFaces.TOPIC:
            o.on_faces(PerceptionFaces.model_validate(env.data))
        elif t == PersonEvent.TOPIC:
            o.on_person(PersonEvent.model_validate(env.data))
        elif t == VoiceWake.TOPIC:
            o.on_wake()
        elif t == VoiceListening.TOPIC:
            o.on_listening(VoiceListening.model_validate(env.data).state)
        elif t == VoiceTranscript.TOPIC:
            msg = VoiceTranscript.model_validate(env.data)
            if msg.final:
                o.on_transcript(msg.text)
        elif t == VoiceSpeaking.TOPIC:
            o.on_speaking(VoiceSpeaking.model_validate(env.data).state)
        elif t == BrainResponse.TOPIC:
            o.on_brain_response(BrainResponse.model_validate(env.data))
        elif t == SafetyState.TOPIC:
            o.on_safety(SafetyState.model_validate(env.data))

    def tick(self, dt: float) -> None:
        if self.logic is not None:
            self.logic.tick()
