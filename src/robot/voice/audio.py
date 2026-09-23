"""PortAudio input/output via ``sounddevice``. Imported lazily; absent on CI."""

from __future__ import annotations

import contextlib
import logging
import queue
from typing import Any

import numpy as np

from robot.voice.base import SAMPLE_RATE, AudioSink, AudioSource

log = logging.getLogger(__name__)


class SounddeviceSource(AudioSource):
    def __init__(self, device: str | int | None = None, block_ms: int = 80, sample_rate: int = SAMPLE_RATE) -> None:
        import sounddevice as sd

        self.sample_rate = sample_rate
        self.block = int(sample_rate * block_ms / 1000)
        self._q: queue.Queue[np.ndarray] = queue.Queue(maxsize=200)

        def callback(indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:  # noqa: ARG001
            if status:
                log.debug("audio in: %s", status)
            with contextlib.suppress(queue.Full):
                self._q.put_nowait(indata[:, 0].copy())

        self._stream = sd.InputStream(
            samplerate=sample_rate, channels=1, dtype="int16", blocksize=self.block, device=device, callback=callback
        )
        self._stream.start()
        log.info("audio in: %s @ %d Hz", self._stream.device, sample_rate)

    def read(self, timeout: float) -> np.ndarray | None:
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        self._stream.stop()
        self._stream.close()


class SounddeviceSink(AudioSink):
    def __init__(self, device: str | int | None = None) -> None:
        import sounddevice as sd

        self._sd = sd
        self.device = device

    def check(self) -> None:
        """Raise now, at construction time, if there is no usable output device."""
        self._sd.check_output_settings(device=self.device, channels=1)

    def play(self, samples: np.ndarray, sample_rate: int) -> None:
        self._sd.play(samples.astype(np.float32), sample_rate, device=self.device, blocking=True)


def list_devices() -> list[dict[str, Any]]:
    try:
        import sounddevice as sd

        return [dict(d) for d in sd.query_devices()]
    except Exception as exc:
        log.debug("sounddevice unavailable: %s", exc)
        return []
