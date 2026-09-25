"""PortAudio input/output via ``sounddevice``. Imported lazily; absent on CI."""

from __future__ import annotations

import contextlib
import logging
import queue
import time
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
    def __init__(self, device: str | int | None = None, *, lead_in_ms: int = 0) -> None:
        import sounddevice as sd

        self._sd = sd
        self.device = device
        self.lead_in_ms = lead_in_ms
        self.last_play: tuple[float, float] | None = None

    def check(self) -> None:
        """Raise now, at construction time, if there is no usable output device."""
        self._sd.check_output_settings(device=self.device, channels=1)

    def play(self, samples: np.ndarray, sample_rate: int) -> None:
        """Blocking playback through an explicit output stream, written in chunks.

        ``sd.play`` hands the whole clip to a callback and returns as soon as the stream
        stops, which on a Bluetooth or PipeWire route can be an underrun a second in; the
        speech then ends early and the robot appears to cut itself off. Chunked blocking
        writes ride through an underrun and only return when everything has been queued.
        """
        out = samples.astype(np.float32)
        if self.lead_in_ms > 0:
            # a Bluetooth speaker in standby drops the start of a stream; give it silence to wake on
            out = np.concatenate([np.zeros(int(sample_rate * self.lead_in_ms / 1000), dtype=np.float32), out])
        expected = len(out) / sample_rate
        chunk = max(256, int(sample_rate * 0.05))
        t0 = time.monotonic()
        written = 0
        with self._sd.OutputStream(samplerate=sample_rate, channels=1, dtype="float32", device=self.device) as stream:
            for i in range(0, len(out), chunk):
                stream.write(out[i : i + chunk].reshape(-1, 1))
                written += min(chunk, len(out) - i)
            # the last chunks sit in the device buffer; give them time to leave the speaker
            time.sleep(min(0.5, stream.latency + 0.05) if stream.latency else 0.1)
        took = time.monotonic() - t0
        self.last_play = (expected, took)
        if took < expected * 0.8:
            log.warning("playback ended early: %.1fs of audio in %.1fs (device %s)", expected, took, self.device)


def list_devices() -> list[dict[str, Any]]:
    try:
        import sounddevice as sd

        return [dict(d) for d in sd.query_devices()]
    except Exception as exc:
        log.debug("sounddevice unavailable: %s", exc)
        return []
