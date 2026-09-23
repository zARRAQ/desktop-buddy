"""Fake engines for the simulator and tests: no microphone, no models, deterministic."""

from __future__ import annotations

import queue
import time

import numpy as np

from robot.voice.base import (
    SAMPLE_RATE,
    AudioSink,
    AudioSource,
    SttEngine,
    TtsEngine,
    WakeWordEngine,
)


class SilentSource(AudioSource):
    """Delivers zero blocks at real-time pace. Speech can be injected for tests."""

    def __init__(self, block_ms: int = 80) -> None:
        self.block = int(SAMPLE_RATE * block_ms / 1000)
        self._injected: queue.Queue[np.ndarray] = queue.Queue()
        self._last = 0.0

    def inject(self, samples: np.ndarray) -> None:
        for i in range(0, len(samples), self.block):
            chunk = samples[i : i + self.block]
            if len(chunk) < self.block:
                chunk = np.pad(chunk, (0, self.block - len(chunk)))
            self._injected.put(chunk.astype(np.int16))

    def read(self, timeout: float) -> np.ndarray | None:
        try:
            return self._injected.get_nowait()
        except queue.Empty:
            pass
        period = self.block / SAMPLE_RATE
        wait = self._last + period - time.monotonic()
        if wait > 0:
            time.sleep(min(wait, timeout))
        self._last = time.monotonic()
        return np.zeros(self.block, dtype=np.int16)

    def close(self) -> None:
        pass


class NullSink(AudioSink):
    """Sleeps for the audio's duration so timing is realistic. Records what was 'played'."""

    def __init__(self, *, realtime: bool = True) -> None:
        self.realtime = realtime
        self.played: list[tuple[int, float]] = []

    def play(self, samples: np.ndarray, sample_rate: int) -> None:
        dur = len(samples) / max(1, sample_rate)
        self.played.append((len(samples), dur))
        if self.realtime:
            time.sleep(min(dur, 5.0))


class FakeWake(WakeWordEngine):
    name = "fake"

    def __init__(self) -> None:
        self._fire = False

    def trigger(self) -> None:
        self._fire = True

    def process(self, block: np.ndarray) -> float:
        if self._fire:
            self._fire = False
            return 1.0
        return 0.0

    def reset(self) -> None:
        self._fire = False


class FakeStt(SttEngine):
    name = "fake"

    def __init__(self, default: str = "hello there") -> None:
        self.default = default
        self.scripted: queue.Queue[str] = queue.Queue()
        self.calls = 0

    def say_next(self, text: str) -> None:
        self.scripted.put(text)

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        self.calls += 1
        try:
            return self.scripted.get_nowait()
        except queue.Empty:
            return self.default


class FakeTts(TtsEngine):
    name = "fake"

    def __init__(self, seconds_per_char: float = 0.045) -> None:
        self.seconds_per_char = seconds_per_char
        self.spoken: list[str] = []

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        self.spoken.append(text)
        n = int(SAMPLE_RATE * max(0.2, len(text) * self.seconds_per_char))
        return np.zeros(n, dtype=np.float32), SAMPLE_RATE
