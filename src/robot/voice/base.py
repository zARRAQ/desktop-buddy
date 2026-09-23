"""Engine interfaces. Audio is 16 kHz mono; blocks are int16 numpy arrays."""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np

SAMPLE_RATE = 16000


class AudioSource(ABC):
    sample_rate: int = SAMPLE_RATE

    @abstractmethod
    def read(self, timeout: float) -> np.ndarray | None:
        """Next int16 block, or None on timeout."""

    @abstractmethod
    def close(self) -> None: ...


class AudioSink(ABC):
    @abstractmethod
    def play(self, samples: np.ndarray, sample_rate: int) -> None:
        """Blocking playback of float32 samples in [-1, 1]."""

    def close(self) -> None: ...


class WakeWordEngine(ABC):
    name: str = "abstract"

    @abstractmethod
    def process(self, block: np.ndarray) -> float:
        """Feed one block; return the detection score (>= threshold means fire)."""

    @abstractmethod
    def reset(self) -> None: ...

    def close(self) -> None: ...


class Vad(ABC):
    @abstractmethod
    def is_speech(self, block: np.ndarray) -> bool: ...

    def reset(self) -> None: ...


class SttEngine(ABC):
    name: str = "abstract"

    @abstractmethod
    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str: ...

    def close(self) -> None: ...


class TtsEngine(ABC):
    name: str = "abstract"

    @abstractmethod
    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        """(float32 samples, sample_rate)."""

    def close(self) -> None: ...


def to_float32(block: np.ndarray) -> np.ndarray:
    if block.dtype == np.float32:
        return block
    return (block.astype(np.float32) / 32768.0).clip(-1.0, 1.0)


def to_int16(samples: np.ndarray) -> np.ndarray:
    if samples.dtype == np.int16:
        return samples
    return (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16)


class EnergyVad(Vad):
    """Adaptive RMS threshold. Good enough to end an utterance; sherpa's Silero is better."""

    def __init__(self, ratio: float = 3.0, floor: float = 0.004) -> None:
        self.ratio = ratio
        self.floor = floor
        self.noise = 0.01

    def is_speech(self, block: np.ndarray) -> bool:
        x = to_float32(block)
        rms = float(np.sqrt(np.mean(x * x))) if x.size else 0.0
        speech = rms > max(self.floor, self.noise * self.ratio)
        if not speech:
            self.noise = 0.95 * self.noise + 0.05 * rms
        return speech

    def reset(self) -> None:
        self.noise = 0.01
