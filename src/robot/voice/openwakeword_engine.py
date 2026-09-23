"""openWakeWord (Apache-2.0) through its ONNX path. Pretrained: hey_jarvis, alexa, hey_mycroft."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from robot.voice.base import WakeWordEngine

log = logging.getLogger(__name__)


class OpenWakeWord(WakeWordEngine):
    name = "openwakeword"

    def __init__(self, model_path: Path, *, melspec: Path | None = None, embedding: Path | None = None) -> None:
        from openwakeword.model import Model

        kwargs = {"wakeword_models": [str(model_path)], "inference_framework": "onnx"}
        if melspec is not None:
            kwargs["melspec_model_path"] = str(melspec)
        if embedding is not None:
            kwargs["embedding_model_path"] = str(embedding)
        self._model = Model(**kwargs)
        self._key = model_path.stem

    def process(self, block: np.ndarray) -> float:
        # openWakeWord wants 80 ms (1280 sample) int16 chunks; larger blocks are fine too.
        scores = self._model.predict(block.astype(np.int16))
        return float(scores.get(self._key, max(scores.values()) if scores else 0.0))

    def reset(self) -> None:
        self._model.reset()
