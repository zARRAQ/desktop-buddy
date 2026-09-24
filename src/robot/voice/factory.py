"""Pick engines from configuration and what is installed / downloaded."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
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
    errors: dict[str, str] = field(default_factory=dict)  # engine name -> why it is missing

    def describe(self) -> str:
        text = (
            f"in={type(self.source).__name__} out={type(self.sink).__name__} "
            f"wake={self.wake.name if self.wake else 'none'} stt={self.stt.name if self.stt else 'none'} "
            f"tts={self.tts.name if self.tts else 'none'}"
        )
        if self.errors:
            text += " | " + "; ".join(f"{k}: {v}" for k, v in self.errors.items())
        return text


def voice_models_dir() -> Path:
    return paths.models_dir() / "voice"


def build_engines(cfg: VoiceConfig, *, fake: bool = False) -> VoiceEngines:
    if fake:
        return VoiceEngines(SilentSource(cfg.audio.block_ms), NullSink(), FakeWake(), EnergyVad(), FakeStt(), FakeTts())
    # Microphone and speaker are opened independently: a speaker without a microphone (or
    # the other way round) is a normal state on a half-built robot, not an error.
    source: AudioSource
    sink: AudioSink
    try:
        from robot.voice.audio import SounddeviceSource

        source = SounddeviceSource(cfg.audio.input_device, cfg.audio.block_ms, cfg.audio.sample_rate)
    except Exception as exc:
        log.warning("no microphone (%s); voice cannot hear until one is plugged in", exc)
        source = SilentSource(cfg.audio.block_ms)
    try:
        from robot.voice.audio import SounddeviceSink

        sink = SounddeviceSink(cfg.audio.output_device)
        sink.check()
    except Exception as exc:
        log.warning("no speaker (%s); speech is logged, not played", exc)
        sink = NullSink()
    # sherpa-onnx (vad, stt, tts) loads before openWakeWord: both bundle an ONNX runtime, and
    # if two copies in one process ever clash, the engines the conversation depends on win
    errors: dict[str, str] = {}
    vad = _vad()
    stt = _stt(cfg, errors)
    tts = _tts(cfg, errors)
    wake = _wake(cfg, errors)
    return VoiceEngines(source, sink, wake, vad, stt, tts, errors)


def _wake(cfg: VoiceConfig, errors: dict[str, str] | None = None) -> WakeWordEngine | None:
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
    log.warning("no wake word engine: presence listening and the bus (voice.wake) still work")
    if errors is not None:
        errors["wake"] = "no engine loaded (see log)"
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


def _stt(cfg: VoiceConfig, errors: dict[str, str] | None = None) -> SttEngine | None:
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
        if errors is not None:
            errors["stt"] = f"{type(exc).__name__}: {exc}"[:160]
    return None


def _tts(cfg: VoiceConfig, errors: dict[str, str] | None = None) -> TtsEngine | None:
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
        if errors is not None:
            errors["tts"] = f"{type(exc).__name__}: {exc}"[:160]
    return None


def retry_missing(engines: VoiceEngines, cfg: VoiceConfig) -> bool:
    """Try again to load the engines that failed at start. Returns True if any came up."""
    changed = False
    if engines.stt is None and cfg.stt.engine not in ("none", "fake"):
        engines.stt = _stt(cfg, engines.errors)
        if engines.stt is not None:
            engines.errors.pop("stt", None)
            changed = True
    if engines.tts is None and cfg.tts.engine not in ("none", "fake"):
        engines.tts = _tts(cfg, engines.errors)
        if engines.tts is not None:
            engines.errors.pop("tts", None)
            changed = True
    return changed
