"""sherpa-onnx engines (Apache-2.0): Moonshine/Whisper STT, VITS (Piper voices) TTS, Zipformer
keyword spotting, Silero VAD. One dependency covers the whole voice stack, and it ships
aarch64 wheels for Python 3.13, which matters on Raspberry Pi OS Trixie.

Model directories are the extracted release archives from
https://github.com/k2-fsa/sherpa-onnx/releases (see ``robot provision --group voice``).
Not yet exercised on a Pi; the API calls follow the sherpa-onnx Python examples.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np

from robot.voice.base import SttEngine, TtsEngine, Vad, WakeWordEngine, to_float32

log = logging.getLogger(__name__)


def _first(dirpath: Path, *patterns: str) -> Path:
    for pat in patterns:
        hits = sorted(dirpath.glob(pat))
        if hits:
            return hits[0]
    raise FileNotFoundError(f"none of {patterns} in {dirpath}")


class SherpaMoonshineStt(SttEngine):
    name = "sherpa-moonshine"

    def __init__(self, model_dir: Path, num_threads: int = 2) -> None:
        import sherpa_onnx

        self._rec = sherpa_onnx.OfflineRecognizer.from_moonshine(
            preprocessor=str(_first(model_dir, "preprocess*.onnx")),
            encoder=str(_first(model_dir, "encode*.onnx")),
            uncached_decoder=str(_first(model_dir, "uncached_decode*.onnx")),
            cached_decoder=str(_first(model_dir, "cached_decode*.onnx")),
            tokens=str(_first(model_dir, "tokens.txt")),
            num_threads=num_threads,
        )

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        stream = self._rec.create_stream()
        stream.accept_waveform(sample_rate, to_float32(audio))
        self._rec.decode_stream(stream)
        return str(stream.result.text).strip()


class SherpaWhisperStt(SttEngine):
    name = "sherpa-whisper"

    def __init__(self, model_dir: Path, num_threads: int = 2, language: str = "en") -> None:
        import sherpa_onnx

        self._rec = sherpa_onnx.OfflineRecognizer.from_whisper(
            encoder=str(_first(model_dir, "*encoder*.onnx")),
            decoder=str(_first(model_dir, "*decoder*.onnx")),
            tokens=str(_first(model_dir, "*tokens.txt")),
            num_threads=num_threads,
            language=language,
            task="transcribe",
        )

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        stream = self._rec.create_stream()
        stream.accept_waveform(sample_rate, to_float32(audio))
        self._rec.decode_stream(stream)
        return str(stream.result.text).strip()


class SherpaVitsTts(TtsEngine):
    name = "sherpa-vits"

    def __init__(self, model_dir: Path, *, speed: float = 1.0, num_threads: int = 2, speaker_id: int = 0) -> None:
        import sherpa_onnx

        vits = sherpa_onnx.OfflineTtsVitsModelConfig(
            model=str(_first(model_dir, "*.onnx")),
            lexicon="",
            tokens=str(_first(model_dir, "tokens.txt")),
            data_dir=str(model_dir / "espeak-ng-data") if (model_dir / "espeak-ng-data").exists() else "",
        )
        model_cfg = sherpa_onnx.OfflineTtsModelConfig(vits=vits, provider="cpu", num_threads=num_threads)
        self._tts = sherpa_onnx.OfflineTts(sherpa_onnx.OfflineTtsConfig(model=model_cfg, max_num_sentences=2))
        self.speed = speed
        self.speaker_id = speaker_id

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        audio = self._tts.generate(text, sid=self.speaker_id, speed=self.speed)
        return np.asarray(audio.samples, dtype=np.float32), int(audio.sample_rate)


class SherpaKws(WakeWordEngine):
    """Zipformer keyword spotter. Keywords are given as text; the model does the rest."""

    name = "sherpa-kws"

    def __init__(self, model_dir: Path, keywords: list[str], num_threads: int = 2) -> None:
        import sherpa_onnx

        keywords_file = model_dir / "keywords_robot.txt"
        keywords_file.write_text("\n".join(keywords) + "\n", encoding="utf-8")
        self._kws = sherpa_onnx.KeywordSpotter(
            tokens=str(_first(model_dir, "tokens.txt")),
            encoder=str(_first(model_dir, "encoder*.onnx")),
            decoder=str(_first(model_dir, "decoder*.onnx")),
            joiner=str(_first(model_dir, "joiner*.onnx")),
            num_threads=num_threads,
            keywords_file=str(keywords_file),
            provider="cpu",
        )
        self._stream: Any = self._kws.create_stream()

    def process(self, block: np.ndarray) -> float:
        self._stream.accept_waveform(16000, to_float32(block))
        fired = 0.0
        while self._kws.is_ready(self._stream):
            self._kws.decode_stream(self._stream)
            if self._kws.get_result(self._stream):
                fired = 1.0
                self._kws.reset_stream(self._stream)
        return fired

    def reset(self) -> None:
        self._stream = self._kws.create_stream()


class SileroVad(Vad):
    def __init__(self, model_path: Path, sample_rate: int = 16000) -> None:
        import sherpa_onnx

        cfg = sherpa_onnx.VadModelConfig()
        cfg.silero_vad.model = str(model_path)
        cfg.silero_vad.min_silence_duration = 0.25
        cfg.sample_rate = sample_rate
        self._vad = sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=30)
        self._sample_rate = sample_rate

    def is_speech(self, block: np.ndarray) -> bool:
        self._vad.accept_waveform(to_float32(block))
        return bool(self._vad.is_speech_detected())

    def reset(self) -> None:
        self._vad.reset()
