from __future__ import annotations

import time

import numpy as np
import pytest

from robot.core.bus import LocalHub
from robot.core.config import RobotConfig
from robot.core.messages import (
    BrainRequest,
    BrainResponse,
    FaceExpression,
    FaceLook,
    FaceObs,
    MotionCommand,
    OrchestratorState,
    PerceptionFaces,
    PersonEvent,
    VoiceSay,
    VoiceSpeaking,
    VoiceTranscript,
)
from robot.core.service import ServiceThread
from robot.memory import Memory
from robot.orchestrator.state import Orchestrator, State, extract_name
from robot.voice.base import EnergyVad
from robot.voice.factory import VoiceEngines
from robot.voice.fake import FakeStt, FakeTts, FakeWake, NullSink, SilentSource
from robot.voice.service import VoiceService, VoiceState


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make(config: RobotConfig, tmp_path):
    published = []
    raw = []
    clk = Clock()
    mem = Memory(tmp_path / "m.sqlite")
    o = Orchestrator(config, mem, published.append, clock=clk)
    o.publish_raw = lambda topic, data: raw.append((topic, data))
    return o, published, raw, clk, mem


def faces(track_id=1, name=None, cx=0.75, cy=0.5):
    return PerceptionFaces(
        frame_ts=0.0,
        faces=[
            FaceObs(
                track_id=track_id, bbox=(cx - 0.1, cy - 0.1, cx + 0.1, cy + 0.1), center=(cx, cy), score=0.9, name=name
            )
        ],
    )


def of_type(published, cls):
    return [p for p in published if isinstance(p, cls)]


def test_boot_idle_attend_and_gaze(config, tmp_path):
    o, pub, _raw, clk, _mem = make(config, tmp_path)
    o.tick()
    assert o.state == State.IDLE
    o.on_faces(faces(cx=0.75))
    assert o.state == State.ATTENDING
    look = of_type(pub, FaceLook)[-1]
    assert look.x < 0  # face on the image's right is on the robot's right: the viewer's left
    cmd = of_type(pub, MotionCommand)[-1]
    assert cmd.type == "look_at" and cmd.yaw_deg is not None and cmd.yaw_deg < 0
    # attention times out
    clk.t += config.orchestrator.attention_timeout_s + 1
    o.tick()
    assert o.state == State.IDLE
    clk.t += config.orchestrator.sleep_after_s + 1
    o.tick()
    assert o.state == State.SLEEP
    assert of_type(pub, FaceExpression)[-1].name == "asleep"
    o.on_faces(faces())
    assert o.state == State.ATTENDING


def test_greet_known_person_with_cooldown(config, tmp_path):
    o, pub, _raw, _clk, mem = make(config, tmp_path)
    o.tick()
    mem.add_person("Ann")
    o.on_faces(faces(name="Ann"))
    o.on_person(PersonEvent(event="recognized", track_id=1, name="Ann", score=0.7))
    says = of_type(pub, VoiceSay)
    assert says and "Ann" in says[-1].text
    assert o.state == State.SPEAKING
    o.on_speaking("end")
    assert o.state == State.ATTENDING
    # seen again soon after: no spoken greeting, just a smile
    mem.touch_seen("Ann")
    mem.touch_seen("Ann")
    o.on_person(PersonEvent(event="left", track_id=1, name="Ann"))
    n = len(of_type(pub, VoiceSay))
    o.on_faces(faces(track_id=2, name="Ann"))
    o.on_person(PersonEvent(event="recognized", track_id=2, name="Ann", score=0.7))
    assert len(of_type(pub, VoiceSay)) == n
    assert of_type(pub, FaceExpression)[-1].name == "happiness"


def test_unknown_person_learns_name_and_enrolls(config, tmp_path):
    o, pub, raw, clk, mem = make(config, tmp_path)
    o.tick()
    o.on_faces(faces())
    clk.t += 3
    o.on_faces(faces())
    says = of_type(pub, VoiceSay)
    assert says and "What's your name" in says[-1].text
    assert o.awaiting_name
    o.on_speaking("end")
    o.on_wake()
    assert o.state == State.LISTENING
    o.on_transcript("My name is Bob.")
    assert raw and raw[-1][0] == "perception.enroll" and raw[-1][1]["name"] == "Bob"
    assert "Bob" in of_type(pub, VoiceSay)[-1].text
    assert mem.get_person("Bob") is not None
    assert not o.awaiting_name and o.attention.name == "Bob"


def test_conversation_round_trip_with_memory(config, tmp_path):
    o, pub, _raw, clk, mem = make(config, tmp_path)
    o.tick()
    o.on_faces(faces(name="Ann"))
    o.attention.greeted = True
    o.on_wake()
    o.on_transcript("Remember that I like tea")
    req = of_type(pub, BrainRequest)[-1]
    assert req.person == "Ann" and req.text == "Remember that I like tea"
    assert o.state == State.THINKING
    o.on_brain_response(BrainResponse(request_id="stale", say="ignored"))
    assert o.state == State.THINKING
    o.on_brain_response(
        BrainResponse(
            request_id=req.request_id, say="Noted.", expression="happy", gesture="nod", remember=["likes tea"]
        )
    )
    assert o.state == State.SPEAKING
    assert of_type(pub, VoiceSay)[-1].text == "Noted."
    assert of_type(pub, MotionCommand)[-1].type == "nod"
    assert mem.facts("Ann") == ["likes tea"]
    o.on_speaking("end")
    assert o.state == State.ATTENDING
    o.on_wake()
    o.on_transcript("what do you know about me")
    req2 = of_type(pub, BrainRequest)[-1]
    assert req2.facts == ["likes tea"] and len(req2.history) == 2
    # brain never answers -> recovers
    clk.t += config.brain.timeout_s + 6
    o.tick()
    assert o.state == State.SPEAKING and "train of thought" in of_type(pub, VoiceSay)[-1].text


def test_listening_timeout_and_empty_transcript(config, tmp_path):
    o, pub, _raw, _clk, _mem = make(config, tmp_path)
    o.tick()
    o.on_wake()
    o.on_listening("timeout")
    assert o.state == State.IDLE and of_type(pub, FaceExpression)[-1].name == "confusion"
    o.on_wake()
    o.on_transcript("   ")
    assert o.state == State.IDLE
    states = [s.state for s in of_type(pub, OrchestratorState)]
    assert states[:2] == ["idle", "listening"]


def test_extract_name():
    assert extract_name("I'm Ann") == "Ann"
    assert extract_name("Bob.") == "Bob"
    assert extract_name("I'm hungry") is None
    assert extract_name("well, hello there") is None


def fake_engines():
    return VoiceEngines(
        SilentSource(80), NullSink(realtime=False), FakeWake(), EnergyVad(), FakeStt("hello robot"), FakeTts()
    )


def _wait(pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return False


@pytest.mark.timeout(60)
def test_voice_service_wake_listen_transcribe_speak(config, hub: LocalHub):
    engines = fake_engines()
    svc = VoiceService(config, hub.client("voice"), engines=engines)
    t = ServiceThread(svc)
    probe = hub.client("probe")
    probe.subscribe("voice.")
    t.start()
    try:
        assert _wait(lambda: svc.engines is not None)
        engines.wake.trigger()  # type: ignore[union-attr]
        assert _wait(lambda: svc.state == VoiceState.LISTENING, timeout=3)
        assert _wait(lambda: svc.transcripts == 1, timeout=5)
        seen = []
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not any(e.topic == VoiceTranscript.TOPIC for e in seen):
            e = probe.recv(0.2)
            if e:
                seen.append(e)
        tr = next(e for e in seen if e.topic == VoiceTranscript.TOPIC)
        assert tr.data["text"] == "hello robot"
        assert [e.data["state"] for e in seen if e.topic == "voice.listening"] == ["start", "end"]
        # speaking round trip
        probe.publish_payload(VoiceSay(text="Hello!", request_id="r1"))
        assert _wait(lambda: engines.tts.spoken == ["Hello!"], timeout=3)  # type: ignore[union-attr]
        assert _wait(lambda: svc.state == VoiceState.IDLE, timeout=3)
        states = []
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and "end" not in states:
            e = probe.recv(0.2)
            if e and e.topic == VoiceSpeaking.TOPIC:
                states.append(e.data["state"])
        assert states == ["start", "end"]
        # bus-triggered wake (simulator space bar)
        probe.publish("voice.wake", {"word": "space", "score": 1.0})
        assert _wait(lambda: svc.wakes == 2, timeout=3)
    finally:
        t.stop()
    assert t.error is None


def test_energy_vad_and_silent_source_injection():
    vad = EnergyVad()
    silence = np.zeros(1280, dtype=np.int16)
    for _ in range(20):
        assert not vad.is_speech(silence)
    loud = (np.random.default_rng(0).standard_normal(1280) * 8000).astype(np.int16)
    assert vad.is_speech(loud)
    src = SilentSource(80)
    src.inject(np.ones(3000, dtype=np.int16))
    a = src.read(0.1)
    assert a is not None and len(a) == 1280 and a[0] == 1
    src.read(0.1)
    c = src.read(0.1)
    assert c is not None and c[:440].all() and not c[440:].any()


def test_presence_listening_without_a_wake_word(config, hub: LocalHub):
    """Someone the camera sees starts talking: the voice service listens with no wake word,
    keeps the pre-roll, and ignores the microphone briefly after it spoke itself."""
    engines = fake_engines()
    svc = VoiceService(config, hub.client("voice"), engines=engines)
    svc.bus.subscribe(*svc.subscriptions)
    svc.setup()
    loud = (np.random.default_rng(0).standard_normal(16000 * 2) * 8000).astype(np.int16)
    # nobody in view: loud audio does nothing
    engines.source.inject(loud[:16000])  # type: ignore[union-attr]
    for _ in range(15):
        svc.tick(0.04)
    assert svc.state == VoiceState.IDLE and svc.presence_triggers == 0
    # a person appears (orchestrator says attending) and speaks
    from robot.core.messages import Envelope, OrchestratorState

    svc.on_message(
        Envelope(
            topic="orchestrator.state", ts=0.0, src="orch", seq=1, data=OrchestratorState(state="attending").to_data()
        )
    )
    assert svc.person_present
    engines.source.inject(loud)  # type: ignore[union-attr]
    for _ in range(3):
        svc.tick(0.04)
    assert svc.state == VoiceState.LISTENING and svc.presence_triggers == 1
    assert len(svc._buffer) >= 2  # pre-roll blocks were kept
    # right after speaking, the guard keeps the mic closed so the robot does not hear itself
    svc.state = VoiceState.IDLE
    svc._buffer = []
    svc._quiet_until = time.monotonic() + 10.0
    engines.source.inject(loud)  # type: ignore[union-attr]
    for _ in range(5):
        svc.tick(0.04)
    assert svc.state == VoiceState.IDLE and svc.presence_triggers == 1
    # switched off in config: never triggers
    svc.config.voice.listen_on_presence = False
    svc._quiet_until = 0.0
    engines.source.inject(loud)  # type: ignore[union-attr]
    for _ in range(5):
        svc.tick(0.04)
    assert svc.state == VoiceState.IDLE
    svc.teardown()
