"""Pick engines from configuration and what is installed / downloaded."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from robot.core import paths
from robot.core.config import VoiceConfig
from robot.voice.base import AudioSink, AudioSource, EnergyVad, SttEngine, TtsEngine, Vad, WakeWordEngine
from robot.voice.fake import FakeStt, FakeTts, FakeWake, NullSink, SilentSource

log = logging.getLogger(__name__)


@dataclass
class VoiceEngines:
    source: AudioSource
    sink: AudioSink
    wake: WakeWordEngine | None
    vad: Vad
    stt: SttEngine | None
    tts: TtsEngine | None

    def describe(self) -> str:
        return (
            f"in={type(self.source).__name__} out={type(self.sink).__name__} "
            f"wake={self.wake.name if self.wake else 'none'} stt={self.stt.name if self.stt else 'none'} "
            f"tts={self.tts.name if self.tts else 'none'}"
        )


def voice_models_dir() -> Path:
    return paths.models_dir() / "voice"


def build_engines(cfg: VoiceConfig, *, fake: bool = False) -> VoiceEngines:
    if fake:
        return VoiceEngines(SilentSource(cfg.audio.block_ms), NullSink(), FakeWake(), EnergyVad(), FakeStt(), FakeTts())
    source: AudioSource
    sink: AudioSink
    try:
        from robot.voice.audio import SounddeviceSink, SounddeviceSource

        source = SounddeviceSource(cfg.audio.input_device, cfg.audio.block_ms, cfg.audio.sample_rate)
        sink = SounddeviceSink(cfg.audio.output_device)
    except Exception as exc:
        log.warning("no audio devices (%s); voice runs with a silent source", exc)
        source, sink = SilentSource(cfg.audio.block_ms), NullSink()
    return VoiceEngines(source, sink, _wake(cfg), _vad(), _stt(cfg), _tts(cfg))


def _wake(cfg: VoiceConfig) -> WakeWordEngine | None:
    eng = cfg.wake.engine
    if eng == "none":
        return None
    if eng == "fake":
        return FakeWake()
    d = voice_models_dir()
    if eng in ("auto", "openwakeword"):
        try:
            from robot.voice.openwakeword_engine import OpenWakeWord

            model = d / "openwakeword" / f"{cfg.wake.model}.onnx"
            mel = d / "openwakeword" / "melspectrogram.onnx"
            emb = d / "openwakeword" / "embedding_model.onnx"
            return OpenWakeWord(model, melspec=mel if mel.exists() else None, embedding=emb if emb.exists() else None)
        except Exception as exc:
            if eng == "openwakeword":
                raise
            log.info("openwakeword unavailable (%s)", exc)
    if eng in ("auto", "sherpa_kws"):
        try:
            from robot.voice.sherpa import SherpaKws

            kws_dir = next(iter(sorted(d.glob("sherpa-onnx-kws-*"))), None)
            if kws_dir is None:
                raise FileNotFoundError("no sherpa-onnx-kws-* model directory")
            words = cfg.wake.model.replace("_", " ").replace("v0.1", "").strip() or "hey buddy"
            return SherpaKws(kws_dir, [words])
        except Exception as exc:
            if eng == "sherpa_kws":
                raise
            log.info("sherpa kws unavailable (%s)", exc)
    log.warning("no wake word engine: say nothing, or trigger listening over the bus (voice.wake)")
    return None


def _vad() -> Vad:
    p = voice_models_dir() / "silero_vad.onnx"
    if p.exists():
        try:
            from robot.voice.sherpa import SileroVad

            return SileroVad(p)
        except Exception as exc:
            log.info("silero vad unavailable (%s); energy VAD", exc)
    return EnergyVad()


def _stt(cfg: VoiceConfig) -> SttEngine | None:
    eng = cfg.stt.engine
    if eng == "none":
        return None
    if eng == "fake":
        return FakeStt()
    d = voice_models_dir() / cfg.stt.model
    try:
        if eng in ("auto", "sherpa_moonshine") and ("moonshine" in cfg.stt.model or eng == "sherpa_moonshine"):
            from robot.voice.sherpa import SherpaMoonshineStt

            return SherpaMoonshineStt(d, cfg.stt.num_threads)
        if eng in ("auto", "sherpa_whisper"):
            from robot.voice.sherpa import SherpaWhisperStt

            return SherpaWhisperStt(d, cfg.stt.num_threads)
    except Exception as exc:
        if eng != "auto":
            raise
        log.warning("speech to text unavailable (%s)", exc)
    return None


def _tts(cfg: VoiceConfig) -> TtsEngine | None:
    eng = cfg.tts.engine
    if eng == "none":
        return None
    if eng == "fake":
        return FakeTts()
    try:
        from robot.voice.sherpa import SherpaVitsTts

        return SherpaVitsTts(voice_models_dir() / cfg.tts.voice, speed=cfg.tts.speed, num_threads=cfg.tts.num_threads)
    except Exception as exc:
        if eng != "auto":
            raise
        log.warning("text to speech unavailable (%s)", exc)
    return None
