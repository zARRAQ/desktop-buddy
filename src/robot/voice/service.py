"""Voice service: a small state machine over the audio engines.

IDLE: run the wake word engine over microphone blocks.
LISTENING: after a wake (engine or bus), record until the VAD hears ``silence_ms`` of quiet
following speech, or ``max_utterance_s``; give up after ``listen_timeout_s`` of no speech.
SPEAKING: synthesise and play ``voice.say`` text; the microphone is ignored meanwhile.
"""

from __future__ import annotations

import queue
import threading
import time
from collections import deque
from enum import Enum

import numpy as np

from robot.core.bus import BusClient
from robot.core.config import RobotConfig
from robot.core.messages import Envelope, VoiceListening, VoiceSay, VoiceSpeaking, VoiceTranscript, VoiceWake
from robot.core.service import Service
from robot.voice.base import to_float32
from robot.voice.factory import VoiceEngines, build_engines
from robot.voice.fake import FakeStt, SilentSource


class VoiceState(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    SPEAKING = "speaking"


class VoiceService(Service):
    name = "voice"
    subscriptions = ("voice.say", "voice.wake", "voice.listen", "orchestrator.state")
    tick_hz = 25.0

    def __init__(
        self, config: RobotConfig, bus: BusClient, *, engines: VoiceEngines | None = None, fake: bool = False
    ) -> None:
        super().__init__(config, bus)
        self._engines = engines
        self._fake = fake
        self.engines: VoiceEngines | None = None
        self.state = VoiceState.IDLE
        self._say_queue: queue.Queue[VoiceSay] = queue.Queue()
        self._buffer: list[np.ndarray] = []
        self._listen_started = 0.0
        self._speech_started: float | None = None
        self._last_speech = 0.0
        self._speaker: threading.Thread | None = None
        self._speaking_text = ""
        self._wake_from_bus = False
        self.transcripts = 0
        self.wakes = 0
        self.person_present = False  # from orchestrator.state: someone is in front of the camera
        self._preroll: deque[np.ndarray] = deque(
            maxlen=max(1, config.voice.presence_preroll_ms // max(config.voice.audio.block_ms, 1))
        )
        self._quiet_until = 0.0  # after speaking: give the room time to stop echoing us
        self.presence_triggers = 0

    def setup(self) -> None:
        if not self.config.voice.enabled:
            self.log.info("voice disabled")
            return
        self.engines = self._engines or build_engines(self.config.voice, fake=self._fake)
        self.log.info("voice engines: %s", self.engines.describe())

    # -- messages -----------------------------------------------------------------------
    def on_message(self, env: Envelope) -> None:
        if env.topic == VoiceSay.TOPIC:
            self._say_queue.put(VoiceSay.model_validate(env.data))
        elif (
            env.topic in (VoiceWake.TOPIC, "voice.listen")
            and env.src != self.bus.src  # externally triggered: simulator space bar, a button
            and self.state == VoiceState.IDLE
        ):
            self._wake_from_bus = True
        elif env.topic == "orchestrator.state":
            self.person_present = env.data.get("state") in ("attending", "listening", "thinking", "speaking")

    # -- loop ----------------------------------------------------------------------------
    def tick(self, dt: float) -> None:
        e = self.engines
        if e is None:
            return
        if self.state == VoiceState.SPEAKING:
            if self._speaker is not None and not self._speaker.is_alive():
                self._speaker = None
                self._quiet_until = time.monotonic() + self.config.voice.after_speech_guard_ms / 1000.0
                self.state = VoiceState.IDLE
                self.bus.publish_payload(VoiceSpeaking(state="end", text=self._speaking_text))
                if e.wake is not None:
                    e.wake.reset()
            return
        if not self._say_queue.empty() and self.state == VoiceState.IDLE:
            self._start_speaking(self._say_queue.get())
            return
        block = e.source.read(timeout=0.05)
        if self.state == VoiceState.IDLE:
            fired = self._wake_from_bus
            self._wake_from_bus = False
            spoke = False
            if block is not None:
                self._preroll.append(block)
                quiet = time.monotonic() < self._quiet_until
                if not fired and e.wake is not None and not quiet:
                    score = e.wake.process(block)
                    if score >= self.config.voice.wake.threshold:
                        fired = True
                        self.bus.publish_payload(VoiceWake(word=self.config.voice.wake.model, score=score))
                if (
                    not fired
                    and self.config.voice.listen_on_presence
                    and self.person_present
                    and not quiet
                    and e.vad.is_speech(block)
                ):
                    # someone we can see started talking: that is the wake word
                    fired = spoke = True
                    self.presence_triggers += 1
                    self.bus.publish_payload(VoiceWake(word="presence", score=1.0))
            if fired:
                self._start_listening(speaking_now=spoke)
        elif self.state == VoiceState.LISTENING:
            self._listen_step(block)

    def _start_listening(self, *, speaking_now: bool = False) -> None:
        e = self.engines
        assert e is not None
        self.wakes += 1
        self.state = VoiceState.LISTENING
        self._listen_started = time.monotonic()
        if speaking_now:
            # keep the audio from just before the trigger so the first word survives
            self._buffer = list(self._preroll)
            self._speech_started = self._last_speech = self._listen_started
        else:
            self._buffer = []
            self._speech_started = None
            e.vad.reset()
        self._preroll.clear()
        self.bus.publish_payload(VoiceListening(state="start"))

    def _listen_step(self, block: np.ndarray | None) -> None:
        e = self.engines
        assert e is not None
        now = time.monotonic()
        vcfg = self.config.voice
        fake_path = isinstance(e.source, SilentSource) and isinstance(e.stt, FakeStt)
        if block is not None:
            self._buffer.append(block)
            if e.vad.is_speech(block):
                if self._speech_started is None:
                    self._speech_started = now
                self._last_speech = now
        elapsed = now - self._listen_started
        if fake_path and elapsed >= 1.0:
            self._finish_listening(timed_out=False)
            return
        if self._speech_started is None:
            if elapsed >= vcfg.listen_timeout_s:
                self._finish_listening(timed_out=True)
            return
        spoke_for = (self._last_speech - self._speech_started) * 1000.0
        if (
            (now - self._last_speech) * 1000.0 >= vcfg.vad.silence_ms and spoke_for >= vcfg.vad.min_speech_ms
        ) or now - self._speech_started >= vcfg.vad.max_utterance_s:
            self._finish_listening(timed_out=False)

    def _finish_listening(self, *, timed_out: bool) -> None:
        e = self.engines
        assert e is not None
        self.state = VoiceState.IDLE
        if e.wake is not None:
            e.wake.reset()
        if timed_out:
            self.bus.publish_payload(VoiceListening(state="timeout"))
            return
        if e.stt is None:
            self.bus.publish_payload(VoiceListening(state="end"))
            self.log.warning("no speech-to-text engine; utterance dropped")
            self.bus.publish_payload(VoiceTranscript(text="", final=True, duration_s=0.0))
            return
        self.bus.publish_payload(VoiceListening(state="end"))
        audio = np.concatenate(self._buffer) if self._buffer else np.zeros(1600, dtype=np.int16)
        duration = len(audio) / e.source.sample_rate
        t0 = time.monotonic()
        try:
            text = e.stt.transcribe(audio, e.source.sample_rate)
        except Exception as exc:
            self.log.error("transcription failed: %s", exc)
            text = ""
        self.log.info("heard %r (%.1fs audio, %.2fs stt)", text, duration, time.monotonic() - t0)
        self.transcripts += 1
        self.bus.publish_payload(VoiceTranscript(text=text, final=True, duration_s=round(duration, 2)))

    def _start_speaking(self, msg: VoiceSay) -> None:
        e = self.engines
        assert e is not None
        if e.tts is None:
            self.log.info("would say: %r (no TTS engine)", msg.text)
            self.bus.publish_payload(VoiceSpeaking(state="start", text=msg.text, request_id=msg.request_id))
            self.bus.publish_payload(VoiceSpeaking(state="end", text=msg.text, request_id=msg.request_id))
            return
        self.state = VoiceState.SPEAKING
        self._speaking_text = msg.text
        self.bus.publish_payload(VoiceSpeaking(state="start", text=msg.text, request_id=msg.request_id))

        def run() -> None:
            try:
                samples, sr = e.tts.synthesize(msg.text)  # type: ignore[union-attr]
                e.sink.play(to_float32(samples), sr)
            except Exception as exc:
                self.log.error("speech failed: %s", exc)
                self.bus.publish_payload(VoiceSpeaking(state="error", text=msg.text, request_id=msg.request_id))

        self._speaker = threading.Thread(target=run, name="tts", daemon=True)
        self._speaker.start()

    def teardown(self) -> None:
        if self.engines is not None:
            self.engines.source.close()
            self.engines.sink.close()
            for eng in (self.engines.wake, self.engines.stt, self.engines.tts):
                if eng is not None:
                    eng.close()
